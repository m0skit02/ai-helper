package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
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

type DraftMessage struct {
	ActionID        string
	DestinationHint string
	MessageText     string
	SessionID       string
	CreatedAt       time.Time
}

type ToolServer struct {
	mu       sync.Mutex
	drafts   map[string]DraftMessage
	sessions map[string]time.Time
}

func newToolServer() *ToolServer {
	return &ToolServer{
		drafts:   make(map[string]DraftMessage),
		sessions: make(map[string]time.Time),
	}
}

func (s *ToolServer) ensureSession(sessionID string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.TrimSpace(sessionID) == "" {
		sessionID = "sess-" + uuid.NewString()
	}
	s.sessions[sessionID] = time.Now().UTC()
	return sessionID
}

func (s *ToolServer) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
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

	sessionID := s.ensureSession(req.SessionID)
	resp := ToolCallResponse{
		TraceID:   req.TraceID,
		SessionID: sessionID,
		Tool:      req.Tool,
		OK:        true,
		Output:    map[string]interface{}{},
		Error:     nil,
	}

	switch req.Tool {
	case "browser.search":
		query := asString(req.Input["query"])
		resp.Output = map[string]interface{}{
			"results": []map[string]interface{}{
				{
					"title":   "Mock result: " + query,
					"url":     "https://example.com/search?q=" + query,
					"snippet": "Mock search result from tools-go",
				},
				{
					"title":   "Mock marketplace listing",
					"url":     "https://example.com/product/iphone-256",
					"snippet": "iPhone 256 mock listing",
				},
			},
		}
	case "browser.extract":
		schemaType := ""
		if schema, ok := req.Input["schema"].(map[string]interface{}); ok {
			schemaType = asString(schema["type"])
		}
		switch schemaType {
		case "news":
			resp.Output = map[string]interface{}{
				"items": []map[string]interface{}{
					{
						"title":        "Apple announces mock update",
						"summary":      "Mock news summary for MVP integration.",
						"published_at": time.Now().UTC().Format(time.RFC3339),
						"url":          "https://example.com/news/apple-update",
						"source":       "example",
					},
				},
			}
		default:
			resp.Output = map[string]interface{}{
				"items": []map[string]interface{}{
					{
						"title":         "iPhone 15 256GB (mock)",
						"price":         89990,
						"currency":      "RUB",
						"url":           "https://example.com/product/iphone-15-256",
						"seller":        "MockStore",
						"rating":        4.8,
						"reviews_count": 132,
						"delivery":      "2 дня",
						"condition":     "new",
						"storage_gb":    256,
					},
				},
			}
		}
	case "browser.message.draft":
		actionID := "act-" + uuid.NewString()
		destinationHint := asString(req.Input["destination_hint"])
		messageText := asString(req.Input["message_text"])
		s.mu.Lock()
		s.drafts[actionID] = DraftMessage{
			ActionID:        actionID,
			DestinationHint: destinationHint,
			MessageText:     messageText,
			SessionID:       sessionID,
			CreatedAt:       time.Now().UTC(),
		}
		s.mu.Unlock()

		resp.Output = map[string]interface{}{
			"action_id":        actionID,
			"destination_hint": destinationHint,
			"message_text":     messageText,
			"status":           "waiting_confirm",
			"resolved_recipient": map[string]interface{}{
				"name":       destinationHint,
				"confidence": 0.7,
			},
		}
	case "browser.message.send":
		actionID := asString(req.Input["action_id"])
		confirm := asBool(req.Input["confirm"])

		s.mu.Lock()
		draft, exists := s.drafts[actionID]
		if exists {
			delete(s.drafts, actionID)
		}
		s.mu.Unlock()

		if !exists || !confirm {
			resp.OK = false
			resp.Error = &ToolError{
				Code:      "VALIDATION_ERROR",
				Message:   "invalid action_id or confirm flag",
				Retryable: false,
			}
			resp.Output = map[string]interface{}{
				"status": "failed",
			}
		} else {
			resp.Output = map[string]interface{}{
				"status":      "sent",
				"action_id":   actionID,
				"message_ref": "msg-" + uuid.NewString(),
				"recipient":   draft.DestinationHint,
			}
		}
	default:
		resp.OK = false
		resp.Error = &ToolError{
			Code:      "UNKNOWN_TOOL",
			Message:   "unsupported tool: " + req.Tool,
			Retryable: false,
		}
	}

	resp.DurationMS = time.Since(start).Milliseconds()
	writeJSON(w, http.StatusOK, resp)
}

func asString(v interface{}) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func asBool(v interface{}) bool {
	if v == nil {
		return false
	}
	if b, ok := v.(bool); ok {
		return b
	}
	return false
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

	addr := "127.0.0.1:" + port
	log.Printf("tools-go listening on http://%s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}
