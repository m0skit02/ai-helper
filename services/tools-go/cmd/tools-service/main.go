package main

import (
	"encoding/json"
	"encoding/xml"
	"html"
	"io"
	"log"
	"net/http"
	neturl "net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode"

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

type SearchResult struct {
	Title   string
	URL     string
	Snippet string
}

type NewsItem struct {
	Title       string
	Summary     string
	PublishedAt string
	URL         string
	Source      string
}

type SessionState struct {
	UpdatedAt         time.Time
	LastQuery         string
	LastSearchResults []SearchResult
	LastNewsItems     []NewsItem
}

type rssFeed struct {
	Channel rssChannel `xml:"channel"`
}

type rssChannel struct {
	Title string    `xml:"title"`
	Items []rssItem `xml:"item"`
}

type rssItem struct {
	Title       string `xml:"title"`
	Link        string `xml:"link"`
	Description string `xml:"description"`
	PubDate     string `xml:"pubDate"`
	Source      string `xml:"source"`
}

type ToolServer struct {
	mu            sync.Mutex
	drafts        map[string]DraftMessage
	sessions      map[string]*SessionState
	httpClient    *http.Client
	tagStripper   *regexp.Regexp
	maxNewsItems  int
	maxSearchRows int
}

func newToolServer() *ToolServer {
	return &ToolServer{
		drafts:        make(map[string]DraftMessage),
		sessions:      make(map[string]*SessionState),
		httpClient:    &http.Client{Timeout: 20 * time.Second},
		tagStripper:   regexp.MustCompile("<[^>]+>"),
		maxNewsItems:  5,
		maxSearchRows: 5,
	}
}

func (s *ToolServer) ensureSession(sessionID string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.TrimSpace(sessionID) == "" {
		sessionID = "sess-" + uuid.NewString()
	}
	state, ok := s.sessions[sessionID]
	if !ok {
		state = &SessionState{}
		s.sessions[sessionID] = state
	}
	state.UpdatedAt = time.Now().UTC()
	return sessionID
}

func (s *ToolServer) updateSessionSearch(sessionID, query string, results []SearchResult, news []NewsItem) {
	s.mu.Lock()
	defer s.mu.Unlock()
	state, ok := s.sessions[sessionID]
	if !ok {
		state = &SessionState{}
		s.sessions[sessionID] = state
	}
	state.UpdatedAt = time.Now().UTC()
	state.LastQuery = query
	state.LastSearchResults = append([]SearchResult(nil), results...)
	state.LastNewsItems = append([]NewsItem(nil), news...)
}

func (s *ToolServer) getSessionNews(sessionID string) []NewsItem {
	s.mu.Lock()
	defer s.mu.Unlock()
	state, ok := s.sessions[sessionID]
	if !ok {
		return nil
	}
	return append([]NewsItem(nil), state.LastNewsItems...)
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
		results, news, err := s.fetchNewsSearch(query)
		if err != nil || len(results) == 0 {
			results = s.fallbackSearchResults(query)
			news = nil
		}
		s.updateSessionSearch(sessionID, query, results, news)
		resp.Output = map[string]interface{}{
			"results": searchResultsToMaps(results),
		}
	case "browser.extract":
		schemaType := ""
		if schema, ok := req.Input["schema"].(map[string]interface{}); ok {
			schemaType = asString(schema["type"])
		}
		switch schemaType {
		case "news":
			items := s.getSessionNews(sessionID)
			if len(items) == 0 {
				items = []NewsItem{}
			}
			resp.Output = map[string]interface{}{
				"items": newsItemsToMaps(items),
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

func (s *ToolServer) fetchNewsSearch(query string) ([]SearchResult, []NewsItem, error) {
	query = strings.TrimSpace(query)
	if query == "" {
		return nil, nil, nil
	}

	lang := "en"
	region := "US"
	if containsCyrillic(query) {
		lang = "ru"
		region = "RU"
	}

	endpoint := "https://news.google.com/rss/search?q=" + neturl.QueryEscape(query) +
		"&hl=" + lang + "&gl=" + region + "&ceid=" + region + ":" + lang

	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("User-Agent", "ai-helper-tools-go/0.1")

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return nil, nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, nil, io.ErrUnexpectedEOF
	}

	var feed rssFeed
	if err := xml.NewDecoder(resp.Body).Decode(&feed); err != nil {
		return nil, nil, err
	}

	results := make([]SearchResult, 0, s.maxSearchRows)
	items := make([]NewsItem, 0, s.maxNewsItems)
	for _, item := range feed.Channel.Items {
		link := strings.TrimSpace(item.Link)
		title := cleanText(item.Title)
		if link == "" || title == "" {
			continue
		}
		summary := cleanText(item.Description)
		if summary == "" {
			summary = title
		}
		source := cleanText(item.Source)
		if source == "" {
			source = "Google News"
		}

		results = append(results, SearchResult{
			Title:   title,
			URL:     link,
			Snippet: summary,
		})
		items = append(items, NewsItem{
			Title:       title,
			Summary:     summary,
			PublishedAt: normalizePubDate(item.PubDate),
			URL:         link,
			Source:      source,
		})
		if len(results) >= s.maxSearchRows && len(items) >= s.maxNewsItems {
			break
		}
	}

	if len(results) > s.maxSearchRows {
		results = results[:s.maxSearchRows]
	}
	if len(items) > s.maxNewsItems {
		items = items[:s.maxNewsItems]
	}

	return results, items, nil
}

func (s *ToolServer) fallbackSearchResults(query string) []SearchResult {
	return []SearchResult{
		{
			Title:   "Mock result: " + query,
			URL:     "https://example.com/search?q=" + neturl.QueryEscape(query),
			Snippet: "Mock search result from tools-go",
		},
		{
			Title:   "Mock marketplace listing",
			URL:     "https://example.com/product/iphone-256",
			Snippet: "iPhone 256 mock listing",
		},
	}
}

func searchResultsToMaps(items []SearchResult) []map[string]interface{} {
	out := make([]map[string]interface{}, 0, len(items))
	for _, item := range items {
		out = append(out, map[string]interface{}{
			"title":   item.Title,
			"url":     item.URL,
			"snippet": item.Snippet,
		})
	}
	return out
}

func newsItemsToMaps(items []NewsItem) []map[string]interface{} {
	out := make([]map[string]interface{}, 0, len(items))
	for _, item := range items {
		out = append(out, map[string]interface{}{
			"title":        item.Title,
			"summary":      item.Summary,
			"published_at": item.PublishedAt,
			"url":          item.URL,
			"source":       item.Source,
		})
	}
	return out
}

func normalizePubDate(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return time.Now().UTC().Format(time.RFC3339)
	}
	layouts := []string{
		time.RFC1123Z,
		time.RFC1123,
		time.RFC822Z,
		time.RFC822,
		time.RFC3339,
	}
	for _, layout := range layouts {
		parsed, err := time.Parse(layout, raw)
		if err == nil {
			return parsed.UTC().Format(time.RFC3339)
		}
	}
	return time.Now().UTC().Format(time.RFC3339)
}

func cleanText(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	raw = html.UnescapeString(raw)
	re := regexp.MustCompile("<[^>]+>")
	raw = re.ReplaceAllString(raw, " ")
	raw = strings.Join(strings.Fields(raw), " ")
	return raw
}

func containsCyrillic(s string) bool {
	for _, r := range s {
		if unicode.In(r, unicode.Cyrillic) {
			return true
		}
	}
	return false
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
