# security sweep

**Trigger** — cron `0 6 * * 1,4` (Mon + Thu 06:00 UTC)
**Summary** — Mon: dependency and SAST audit. Thu: a guardrail surface review
**Workstream** — [`workstreams/security.md`](../../workstreams/security.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = security`.

## Focus

Alternate between the two weekly runs:

- **Monday — dependency and SAST.** Run `make security`. Every `pip-audit` CVE and every new ruff
  `S`-rule hit is a candidate; CVE bumps are auto lane — after
  `gh pr list --author "app/dependabot" --state open` shows nothing already raising that pin.
  Then check the CodeQL triage PR is moving —
  `gh pr list --label workstream:security --state open`. You do **not** hunt alerts yourself:
  `codeql-triage.yml` owns that, because an alert is state in the GitHub API and a scout reads
  files. One sitting red for more than a week is itself the finding.
- **Thursday — surface review.** Pick one input or output surface added or changed in the last two
  weeks (`git log --since='2 weeks' --name-only -- src/yeaboi/`) and check it against the guardrail
  layers: input validation, redaction on the way to logs, CSP on the way to a browser, `fs_policy`
  on the way to disk.

Both runs: verify the `ARTIFACT_CSP` / `EDIT_CSP` diff test still asserts the *whole* policy.

## Extra stop conditions

- A finding that requires disclosure (an exploitable path in a shipped release) is **not** filed as a
  public GitHub issue. File the Linear ticket, post to `#yeaboi-claude`, and stop.

  **`critical: true` does not override this — it points the other way.** The critical flag exists to
  jump the proposal cap, and the fastest route past a queue is a public issue, which is precisely
  what a disclosure may not become. A find that is both critical and disclosable takes this path,
  not the cap bypass.

  The post is the **degenerate form** — a title line, the ticket, and nothing else:

  ```slack
  🔐 **Security** — disclosure filed, details in YEA-91 · not public
  ```

  **This message is deliberately exempt from the rule that every named thing carries a link, and
  from carrying a right-hand fact that quantifies anything.** The only linkable artefacts are the
  private Linear ticket and the repo path that *is* the exploit; a grammar that says "link what you
  name" pushes toward the second, and a title line that says which version is exploitable is the
  same disclosure through a narrower pipe. The channel is read by an allowlisted human, but it also
  carries attacker-influenceable text and `cron/slack-relay.md` reads it while holding write
  credentials. Say that something was found, say where it is written down, stop.
