# yeaboi-core

Prebuilt platform wheels for the `yeaboi-core` sidecar — the Go half of
[yeaboi](https://pypi.org/project/yeaboi/). You almost never install this
directly; it arrives as the optional extra:

```bash
pip install "yeaboi[core]"
```

With the wheel installed, yeaboi's agentwatch pipelines (usage, standup,
security) are served by the sidecar automatically. `YEABOI_GO=0` opts out;
everything falls back to the pure-Python implementation silently — the sidecar
is an accelerator, never the only path.

The binary is pure Go (no cgo, static), built from `go/` in the yeaboi
repository. Wheels are published per platform only — there is no sdist, because
building from source requires the full repository and a Go toolchain
(`make go-build` there, then `YEABOI_CORE_BIN=bin/yeaboi-core`).

This package's version tracks the sidecar's own semver (`binaryVersion` in
`go/cmd/yeaboi-core/main.go`), independent of yeaboi's version. Compatibility
between the two is enforced at runtime by the `core.hello` contract-version
handshake.
