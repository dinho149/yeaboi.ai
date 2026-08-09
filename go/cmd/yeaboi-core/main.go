// yeaboi-core — the Go sidecar behind src/yeaboi/gocore/client.py.
//
// Speaks newline-delimited JSON-RPC 2.0 (subset) over stdio per
// contracts/v1/rpc.md. Requests are served one at a time in arrival order
// (the client sends one at a time); progress notifications stream to stdout
// between the request and its response. stdout carries ONLY protocol JSON —
// logs go to stderr.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/agentwatch"
	"github.com/yeaboi-ai/yeaboi/go/internal/contract"
	"github.com/yeaboi-ai/yeaboi/go/internal/rpc"
)

// binaryVersion is the sidecar's own semver, reported by core.hello.
const binaryVersion = "0.1.0"

var methods = []string{"agentwatch.refresh", "agentwatch.usage"}

func main() {
	log.SetOutput(os.Stderr)
	log.SetPrefix("yeaboi-core: ")
	log.SetFlags(0)

	in := bufio.NewReaderSize(os.Stdin, 1<<20)
	out := rpc.NewWriter(os.Stdout)
	for {
		line, readErr := in.ReadString('\n')
		if trimmed := strings.TrimSpace(line); trimmed != "" {
			serve(trimmed, out)
		}
		if readErr != nil {
			if !errors.Is(readErr, io.EOF) {
				log.Printf("stdin read failed: %v", readErr)
			}
			return
		}
	}
}

// serve handles one request line: decode, dispatch, respond.
func serve(line string, out *rpc.Writer) {
	var req rpc.Request
	if err := json.Unmarshal([]byte(line), &req); err != nil {
		log.Printf("dropping unparseable request line: %v", err)
		return
	}
	if req.ID == nil {
		log.Printf("dropping request with no id (method %q)", req.Method)
		return
	}
	id := *req.ID
	result, rpcErr := dispatch(&req, id, out)
	if rpcErr != nil {
		_ = out.SendError(id, rpcErr.Code, rpcErr.Message)
		return
	}
	_ = out.SendResult(id, result)
}

// dispatch routes one request to its method handler.
func dispatch(req *rpc.Request, id int64, out *rpc.Writer) (any, *rpc.Error) {
	emit := func(ev *contract.Event) {
		_ = out.SendProgress(id, ev)
	}
	switch req.Method {
	case "core.hello":
		return &contract.HelloResult{
			ContractVersion: contract.Version,
			Name:            "yeaboi-core",
			Version:         binaryVersion,
			Methods:         methods,
		}, nil
	case "agentwatch.refresh":
		var params contract.RefreshParams
		if err := decodeParams(req.Params, &params); err != nil {
			return nil, err
		}
		if params.DBPath == "" {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "db_path is required"}
		}
		result, err := agentwatch.RunAgentRefresh(&params, emit)
		if err != nil {
			return nil, mapError(err)
		}
		return result, nil
	case "agentwatch.usage":
		var params contract.UsageParams
		if err := decodeParams(req.Params, &params); err != nil {
			return nil, err
		}
		if params.DBPath == "" {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "db_path is required"}
		}
		if _, err := time.Parse("2006-01-02", params.Today); err != nil {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "today must be a YYYY-MM-DD date"}
		}
		result, err := agentwatch.RunAgentUsage(&params, emit)
		if err != nil {
			return nil, mapError(err)
		}
		return result, nil
	default:
		return nil, &rpc.Error{Code: rpc.CodeMethodNotFound, Message: fmt.Sprintf("method not found: %s", req.Method)}
	}
}

func decodeParams(raw json.RawMessage, into any) *rpc.Error {
	if len(raw) == 0 {
		return nil
	}
	if err := json.Unmarshal(raw, into); err != nil {
		return &rpc.Error{Code: rpc.CodeInvalidParams, Message: fmt.Sprintf("invalid params: %v", err)}
	}
	return nil
}

// mapError converts an internal failure to a contract error code. Messages
// are safe to log — they never contain transcript content (paths and class
// names only).
func mapError(err error) *rpc.Error {
	if errors.Is(err, agentwatch.ErrSchemaTooNew) {
		return &rpc.Error{Code: rpc.CodeSchemaGuard, Message: err.Error()}
	}
	return &rpc.Error{Code: rpc.CodeInternal, Message: err.Error()}
}
