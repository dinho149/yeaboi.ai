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
   `deep: true` to re-scan every transcript (slower, thorough).

2. **Present it worst-first**: lead with the `posture`
   (good / needs-attention / at-risk) and the critical/high findings, each with
   its `remediation`. The `mcp_servers` table shows what is configured and its
   risk `flags` (plain-http, unpinned-package, inline-credential).

3. **Privacy is structural** — findings carry pattern + file + line only.
   Never ask the user to paste the matched secret back; point them at the
   `location` and recommend rotation.

4. **Compare over time** with `agents_security_history` (newest first) — a
   posture that regressed since the last scan is the headline.

5. Exports auto-save under `~/.yeaboi/exports/agentwatch/security/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `llm_mode: "fallback"`
means no LLM was reachable — the findings are still real, only the
summary/recommendations prose fell back to deterministic lines.
