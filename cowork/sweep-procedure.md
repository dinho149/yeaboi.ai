# Sweep procedure

The shared run shape for all fourteen workstream sweep routines. Your routine file names the
workstream and any per-run focus; everything else is here.

1. **Read** [house-rules.md](house-rules.md), [models.md](models.md), your charter in
   `workstreams/<name>.md`, and the `.claude/skills/*/SKILL.md` your charter names.

2. **Check for work in flight** — `gh pr list --label "workstream:<name>" --state open`.
   If a PR is open: drive it to green (fix CI, answer review comments) and **stop**. That is the
   whole run.

3. **Scout** — spawn `cowork-scout` at the `standard` tier (`security` uses `deep`) with your
   charter's paths. It returns ranked finds, each
   classified `auto` or `propose` against the allowlist in house-rules. Nothing found is a normal
   outcome: exit silently.

4. **Deduplicate** — `gh issue list --label "workstream:<name>" --state open` and
   `gh issue list --label "workstream:<name>" --state closed --limit 50`. Drop any find that
   restates an open proposal or one that was closed unapproved. Do not re-file rejected ideas.

5. **Auto lane** — take the single highest-impact `auto` find (one per run, never more).
   Tiers come from [models.md](models.md); pass each one explicitly on spawn:
   - spawn `cowork-scribe` (`standard`) to open the Linear ticket (DoD item 1)
   - spawn `cowork-builder` (`deep`) to implement it in a branch off `main` and run the DoD gate
   - **you** then spawn `code-reviewer` (`deep`) on `git diff main...HEAD` with a one-paragraph
     description of the find — the builder does not review its own work, and agents do not nest
   - resolve every `blocker` and `should-fix` finding, then have the builder open the PR labelled
     `cowork` + `workstream:<name>`

6. **Propose lane** — hand every remaining `propose` find to `cowork-scribe` (`standard`), which files one GitHub
   issue each (`cowork:proposal` + `workstream:<name>`) with a cross-linked Linear ticket. **No Slack
   post** — the daily digest routine is the only thing that talks to Slack about proposals.

7. **Stop.** Do not follow interesting threads outside your charter. File them as proposals for the
   owning workstream and move on.

## Stop conditions

Abort the run and report if: `main` cannot be fetched; `make test` fails on a clean checkout (that is
a `platform` problem, not yours — file an issue); or the scout returns more than 10 finds (something
is wrong with the charter's scope, and filing 10 issues would bury the digest).
