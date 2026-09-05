---
name: agents-security
description: "(beta) Audit the user's local AI-agent setup with yeaboi: permission-bypass settings, wildcard allow rules, risky hooks, MCP server inventory, secret-shaped text and risky shell commands in session transcripts — each finding with a verdict (did it actually run, or was it test data?), a replay of the session around it, and one-click fixes (guard hook, settings edit, PR, rotate). Use when the user asks how safe their agent setup is, wants an agent security audit/scan, asks about agent permissions or MCP server risks, or wants to see and fix what an agent did."
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

2. **Lead with `verdict_line`**, then the `issues` in order. An issue is one
   pattern across every place it fired, and its `verdict` is what to say about it:
   - `needs-decision` — the command actually ran, or a live-looking key sat in a
     command or in what the user typed. These are the only rows that need action.
   - `unsure` — a generic shape (an `sk-` prefix, a `user:password` URL) or a row
     scanned before context was recorded. Open the replay before judging.
   - `test-data` — written into or read from a test, fixture, docs or plan file.
     **Not a risk.** Never tell the user to rotate a key that only appeared here.
   - `handled` — dismissed, fixed or rotated; `info` — counted, not listed.
   Each issue carries `why` (say it in your own words) and `signals` / `sessions`
   / `last_seen`. `new_findings` and `resolved_findings` are the keys that
   appeared or went away since the previous saved scan.

3. **Show what happened** with `agents_security_replay` on a finding's `key`
   (from the issue's `finding_keys`): the turns before and after the flagged
   line, redacted, with the matched turn marked. `agents_security_signals`
   lists every matching line behind a grouped finding so the user can pick
   another `line` to replay. A `context` of `heredoc`, `write-input` or
   `inline-script` means the text was written, not run.

4. **Offer the fixes, then apply the one the user picks** with
   `agents_security_fix(key, fix_id)`. Every finding lists its `fixes`
   (`id`, `label`, `detail`): `guard-hook` writes a Claude Code PreToolUse guard
   that refuses the command family (or a key-shaped argument) before it runs —
   the first write into `~/.claude` asks for sandbox consent; `guard-hook-pr` and
   `mcp-edit-pr` open a PR against the session's repository (pass `repo` to
   choose); `settings-edit` removes one settings key or rule after a backup;
   `rotate` hands back the provider's key page; `mark-rotated`,
   `mark-test-data` and `dismiss` (needs `reason`) set the finding aside;
   `undo` brings it back. Use `agents_security_verdict(keys, "test-data")` to
   set a whole issue aside at once. An applied fix reads as `handled` on the
   next report and is recorded in `yeaboi agents security --list-fixes`.

5. **Privacy is structural** — a finding carries pattern + file + line + where
   the match sat + a ≤120-character redacted snippet with the matched span
   masked. Never ask the user to paste the matched secret back. A transcript
   match is at most `high`: a credential-shaped string in a session log is a
   signal to rotate, not a checked-in secret.

6. **Compare over time** with `agents_security_history` (newest first) — a
   posture that regressed since the last scan is the headline.

7. Exports auto-save under `~/.yeaboi/exports/agentwatch/security/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. A fix that answers with a
path outside the allowed paths needs the user's consent — say which path and
retry once they allow it. `llm_mode: "fallback"` means no LLM was reachable —
the findings and verdicts are still real, only the write-up fell back to the
deterministic `verdict_line`.
