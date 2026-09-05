---
name: agents-security
description: "(beta) Audit the user's local AI-agent setup with yeaboi: permission-bypass settings, wildcard allow rules, risky hooks, MCP server inventory, secret-shaped text and risky shell commands in session transcripts. Use when the user asks how safe their agent setup is, wants an agent security audit/scan, or asks about agent permissions or MCP server risks."
---

# Agent security workflows with yeaboi

> **Beta.** These checks are deterministic pattern scans — an *indicator*, not
> a security audit. A clean report means no known pattern matched, not that the
> setup is safe; say so when you present it.

1. **Run the scan** with `agents_security_scan`. The default pass audits the
   settings/MCP configs and any new or changed session transcripts; set
   `deep: true` to re-scan every transcript (slower, thorough) and
   `include_info: true` to list the informational findings the report
   otherwise only counts in `hidden_info_count`.

2. **Lead with what changed.** `new_findings` and `resolved_findings` are the
   keys that appeared or went away since the previous saved scan — after a
   first triage they are the whole story. Then the `posture`
   (good / needs-attention / at-risk) with its `posture_reason`, and the
   critical/high findings, each with its `remediation`. Findings are grouped
   per (pattern, file): `occurrences` says how many lines matched, and
   `pattern_totals` sums each transcript pattern across files. The
   `mcp_servers` table shows what is configured and its risk `flags`
   (plain-http, unpinned-package, inline-credential); an MCP finding lists
   every `scopes` entry the same server spec appears in.

3. **Dismiss with a reason, never silently.** When the user says a finding is
   expected (a fixture key, a scoped allow rule they meant), call
   `agents_security_dismiss` with the finding's `key` and their reason — the
   tool refuses an empty one. The next scan drops it from the findings and
   the posture but keeps it in `dismissed_count`. Pass `reason: "undo"` with
   the same key to restore it.

4. **Privacy is structural** — findings carry pattern + file + line only.
   Never ask the user to paste the matched secret back; point them at the
   `location` and recommend rotation. A transcript match is at most `high`:
   a credential-shaped string in a session log is a signal to rotate, not a
   checked-in secret.

5. **Compare over time** with `agents_security_history` (newest first) — a
   posture that regressed since the last scan is the headline.

6. Exports auto-save under `~/.yeaboi/exports/agentwatch/security/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `llm_mode: "fallback"`
means no LLM was reachable — the findings are still real, only the
summary/recommendations prose fell back to deterministic lines.
