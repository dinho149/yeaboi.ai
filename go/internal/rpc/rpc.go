// Package rpc implements the newline-delimited JSON-RPC 2.0 subset framing
// described in contracts/v1/rpc.md.
package rpc

import (
	"encoding/json"
	"io"
	"sync"
)

// Error codes fixed by the contract.
const (
	CodeMethodNotFound = -32601
	CodeInvalidParams  = -32602
	CodeInternal       = 1000
	CodeSchemaGuard    = 1001
)

// Request is one client → server call. Params stay raw so each method can
// decode its own shape.
type Request struct {
	Jsonrpc string          `json:"jsonrpc"`
	ID      *int64          `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

// Error is the JSON-RPC error object.
type Error struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Response is one server → client reply.
type Response struct {
	Jsonrpc string `json:"jsonrpc"`
	ID      int64  `json:"id"`
	Result  any    `json:"result,omitempty"`
	Error   *Error `json:"error,omitempty"`
}

// Notification is a server → client message with no id (progress events).
type Notification struct {
	Jsonrpc string `json:"jsonrpc"`
	Method  string `json:"method"`
	Params  any    `json:"params"`
}

// ProgressParams wraps one progress event with the request id it belongs to.
type ProgressParams struct {
	RequestID int64 `json:"request_id"`
	Event     any   `json:"event"`
}

// Writer serializes JSON objects one per line onto a stream. All stdout
// traffic must go through one Writer — the mutex keeps a progress
// notification from interleaving with a response mid-line.
type Writer struct {
	mu  sync.Mutex
	enc *json.Encoder
}

// NewWriter wraps w. HTML escaping is disabled so output matches Python's
// json.dumps more closely (both are valid JSON either way).
func NewWriter(w io.Writer) *Writer {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return &Writer{enc: enc}
}

// Send writes one JSON object followed by a newline.
func (w *Writer) Send(v any) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.enc.Encode(v)
}

// SendResult writes a success response.
func (w *Writer) SendResult(id int64, result any) error {
	return w.Send(Response{Jsonrpc: "2.0", ID: id, Result: result})
}

// SendError writes an error response.
func (w *Writer) SendError(id int64, code int, message string) error {
	return w.Send(Response{Jsonrpc: "2.0", ID: id, Error: &Error{Code: code, Message: message}})
}

// SendProgress writes one progress notification for a request.
func (w *Writer) SendProgress(requestID int64, event any) error {
	return w.Send(Notification{
		Jsonrpc: "2.0",
		Method:  "progress",
		Params:  ProgressParams{RequestID: requestID, Event: event},
	})
}
