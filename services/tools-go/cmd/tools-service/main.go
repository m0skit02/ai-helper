package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type ToolCallRequest struct {
	TraceID   string                 `json:"trace_id"`
	SessionID string                 `json:"session_id"`
	Tool      string                 `json:"tool"`
	Input     map[string]interface{} `json:"input"`
}

type ToolError struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

type ToolCallResponse struct {
	TraceID    string                 `json:"trace_id"`
	SessionID  string                 `json:"session_id"`
	Tool       string                 `json:"tool"`
	OK         bool                   `json:"ok"`
	Output     map[string]interface{} `json:"output"`
	Error      *ToolError             `json:"error"`
	DurationMS int64                  `json:"duration_ms"`
}

type BridgeMessage struct {
	Type      string            `json:"type"`
	ClientID  string            `json:"client_id,omitempty"`
	Request   *ToolCallRequest  `json:"request,omitempty"`
	Response  *ToolCallResponse `json:"response,omitempty"`
	Connected bool              `json:"connected,omitempty"`
}

type ToolServer struct {
	mu                sync.Mutex
	upgrader          websocket.Upgrader
	bridgeConn        *websocket.Conn
	bridgeClientID    string
	bridgeConnectedAt time.Time
	pending           map[string]chan ToolCallResponse
	writeMu           sync.Mutex
	toolTimeout       time.Duration
}

func newToolServer() *ToolServer {
	return &ToolServer{
		upgrader: websocket.Upgrader{
			CheckOrigin: func(_ *http.Request) bool { return true },
		},
		pending:     make(map[string]chan ToolCallResponse),
		toolTimeout: 30 * time.Second,
	}
}

func newID(prefix string) string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return prefix + strings.ReplaceAll(time.Now().UTC().Format("20060102150405.000000000"), ".", "")
	}
	return prefix + hex.EncodeToString(buf)
}

func (s *ToolServer) handleHealth(w http.ResponseWriter, _ *http.Request) {
	s.mu.Lock()
	connected := s.bridgeConn != nil
	clientID := s.bridgeClientID
	connectedAt := s.bridgeConnectedAt
	s.mu.Unlock()

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"status":              "ok",
		"bridge_connected":    connected,
		"bridge_client_id":    clientID,
		"bridge_connected_at": connectedAt,
	})
}

func (s *ToolServer) handleCallTool(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method_not_allowed"})
		return
	}

	var req ToolCallRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_json"})
		return
	}

	if strings.TrimSpace(req.TraceID) == "" {
		req.TraceID = newID("trace-")
	}

	if strings.TrimSpace(req.Tool) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing_tool"})
		return
	}

	response, ok := s.dispatchToBridge(req)
	if !ok {
		resp := ToolCallResponse{
			TraceID:    req.TraceID,
			SessionID:  req.SessionID,
			Tool:       req.Tool,
			OK:         false,
			Output:     map[string]interface{}{},
			DurationMS: time.Since(start).Milliseconds(),
			Error: &ToolError{
				Code:      "BRIDGE_UNAVAILABLE",
				Message:   "browser extension is not connected",
				Retryable: true,
			},
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	if response.TraceID == "" {
		response.TraceID = req.TraceID
	}
	if response.Tool == "" {
		response.Tool = req.Tool
	}
	if response.SessionID == "" {
		response.SessionID = req.SessionID
	}
	response.DurationMS = time.Since(start).Milliseconds()
	writeJSON(w, http.StatusOK, response)
}

func (s *ToolServer) dispatchToBridge(req ToolCallRequest) (ToolCallResponse, bool) {
	s.mu.Lock()
	conn := s.bridgeConn
	s.mu.Unlock()
	if conn == nil {
		return ToolCallResponse{}, false
	}

	responseCh := make(chan ToolCallResponse, 1)

	s.mu.Lock()
	s.pending[req.TraceID] = responseCh
	s.mu.Unlock()

	err := s.writeBridgeMessage(BridgeMessage{
		Type:    "tool_request",
		Request: &req,
	})
	if err != nil {
		s.mu.Lock()
		delete(s.pending, req.TraceID)
		s.mu.Unlock()
		return ToolCallResponse{}, false
	}

	select {
	case response := <-responseCh:
		return response, true
	case <-time.After(s.toolTimeout):
		s.mu.Lock()
		delete(s.pending, req.TraceID)
		s.mu.Unlock()
		return ToolCallResponse{
			TraceID:   req.TraceID,
			SessionID: req.SessionID,
			Tool:      req.Tool,
			OK:        false,
			Output:    map[string]interface{}{},
			Error: &ToolError{
				Code:      "TIMEOUT",
				Message:   "tool response timeout waiting for browser extension",
				Retryable: true,
			},
		}, true
	}
}

func (s *ToolServer) handleBridgeWS(w http.ResponseWriter, r *http.Request) {
	conn, err := s.upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("bridge upgrade failed: %v", err)
		return
	}

	clientID := newID("ext-")
	s.registerBridgeConnection(conn, clientID)
	log.Printf("bridge connected: %s", clientID)

	_ = s.writeBridgeMessage(BridgeMessage{
		Type:      "bridge_state",
		ClientID:  clientID,
		Connected: true,
	})

	go s.heartbeatLoop(conn, clientID)
	s.readBridgeLoop(conn, clientID)
}

func (s *ToolServer) registerBridgeConnection(conn *websocket.Conn, clientID string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.bridgeConn != nil {
		_ = s.bridgeConn.Close()
	}

	s.bridgeConn = conn
	s.bridgeClientID = clientID
	s.bridgeConnectedAt = time.Now().UTC()
}

func (s *ToolServer) readBridgeLoop(conn *websocket.Conn, clientID string) {
	defer func() {
		s.unregisterBridgeConnection(conn, clientID)
		_ = conn.Close()
		log.Printf("bridge disconnected: %s", clientID)
	}()

	for {
		var msg BridgeMessage
		if err := conn.ReadJSON(&msg); err != nil {
			return
		}

		switch msg.Type {
		case "hello":
			continue
		case "pong":
			continue
		case "tool_response":
			if msg.Response == nil {
				continue
			}

			s.mu.Lock()
			responseCh, ok := s.pending[msg.Response.TraceID]
			if ok {
				delete(s.pending, msg.Response.TraceID)
			}
			s.mu.Unlock()

			if ok {
				responseCh <- *msg.Response
			}
		}
	}
}

func (s *ToolServer) heartbeatLoop(conn *websocket.Conn, clientID string) {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		s.mu.Lock()
		currentConn := s.bridgeConn
		currentClientID := s.bridgeClientID
		s.mu.Unlock()

		if currentConn != conn || currentClientID != clientID {
			return
		}

		if err := s.writeBridgeMessage(BridgeMessage{
			Type:     "ping",
			ClientID: clientID,
		}); err != nil {
			_ = conn.Close()
			return
		}
	}
}

func (s *ToolServer) unregisterBridgeConnection(conn *websocket.Conn, clientID string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.bridgeConn == conn {
		s.bridgeConn = nil
		s.bridgeClientID = ""
		s.bridgeConnectedAt = time.Time{}
	}

	for traceID, responseCh := range s.pending {
		select {
		case responseCh <- ToolCallResponse{
			TraceID: traceID,
			OK:      false,
			Output:  map[string]interface{}{},
			Error: &ToolError{
				Code:      "BRIDGE_UNAVAILABLE",
				Message:   "browser extension disconnected",
				Retryable: true,
			},
		}:
		default:
		}
		delete(s.pending, traceID)
	}
}

func (s *ToolServer) writeBridgeMessage(msg BridgeMessage) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()

	s.mu.Lock()
	conn := s.bridgeConn
	s.mu.Unlock()
	if conn == nil {
		return websocket.ErrCloseSent
	}

	_ = conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
	return conn.WriteJSON(msg)
}

func writeJSON(w http.ResponseWriter, status int, payload interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func main() {
	port := os.Getenv("TOOLS_PORT")
	if strings.TrimSpace(port) == "" {
		port = "8080"
	}

	srv := newToolServer()
	mux := http.NewServeMux()
	mux.HandleFunc("/health", srv.handleHealth)
	mux.HandleFunc("/mcp/tool/call", srv.handleCallTool)
	mux.HandleFunc("/bridge/ws", srv.handleBridgeWS)

	addr := "0.0.0.0:" + port
	log.Printf("tools-go listening on http://%s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}
