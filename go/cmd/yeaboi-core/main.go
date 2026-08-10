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
	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
	"github.com/yeaboi-ai/yeaboi/go/internal/rpc"
	"github.com/yeaboi-ai/yeaboi/go/internal/standup"
)

// binaryVersion is the sidecar's own semver, reported by core.hello.
// 0.2.0: standup.aggregate joined the method set (additive; contract v1).
const binaryVersion = "0.2.0"

var methods = []string{
	"agentwatch.refresh",
	"agentwatch.usage",
	"agentwatch.standup",
	"agentwatch.security",
	"standup.aggregate",
}

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
	case "agentwatch.standup":
		var params contract.StandupParams
		if err := decodeParams(req.Params, &params); err != nil {
			return nil, err
		}
		if params.DBPath == "" {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "db_path is required"}
		}
		for _, d := range []string{params.WindowStart, params.DigestDate} {
			if _, err := time.Parse("2006-01-02", d); err != nil {
				return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "window_start and digest_date must be YYYY-MM-DD dates"}
			}
		}
		result, err := agentwatch.RunAgentStandup(&params, emit)
		if err != nil {
			return nil, mapError(err)
		}
		return result, nil
	case "agentwatch.security":
		var params contract.SecurityParams
		if err := decodeParams(req.Params, &params); err != nil {
			return nil, err
		}
		if params.DBPath == "" {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "db_path is required"}
		}
		if _, err := time.Parse("2006-01-02", params.ScanDate); err != nil {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "scan_date must be a YYYY-MM-DD date"}
		}
		if params.ClaudeDir == "" || params.ClaudeJSON == "" {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "claude_dir and claude_json are required"}
		}
		result, err := agentwatch.RunAgentSecurity(&params, emit)
		if err != nil {
			return nil, mapError(err)
		}
		return result, nil
	case "standup.aggregate":
		// The whole params document is Python-dict-shaped data whose object
		// key order is part of the contract (grouped/practices/yesterday are
		// member-keyed), so it is decoded ORDERED rather than into structs —
		// and the result goes back out the same way (pysem.Obj.MarshalJSON).
		// Emits no progress: the call is milliseconds of pure compute.
		if len(req.Params) == 0 {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "params are required"}
		}
		decoded, err := pysem.DecodeOrdered(req.Params)
		if err != nil {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: fmt.Sprintf("invalid params: %v", err)}
		}
		params := pysem.AsObj(decoded)
		if params == nil {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: "params must be an object"}
		}
		result, aggErr := standup.RunStandupAggregate(params)
		if aggErr != nil {
			return nil, &rpc.Error{Code: rpc.CodeInvalidParams, Message: aggErr.Error()}
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
