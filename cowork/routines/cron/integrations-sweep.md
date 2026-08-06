# integrations sweep

**Trigger** — cron `30 6 * * 2` (Tue 06:30 UTC)
**Workstream** — [`workstreams/integrations.md`](../../workstreams/integrations.md)
Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = integrations`. Like
`security`, this sweep scouts at `deep` rather than the shared `standard` — the reach axis
reasons across six modes' code in one run, which is synthesis rather than a survey of one
directory. That exception is named in `sweep-procedure.md` and the tier is in the README table;
a sweep never carries its own `Model` line.

## Pick this week's axis first

The charter covers three axes and a run does **exactly one**. Run:

```bash
echo $(( 10#$(date -u +%V) % 3 ))
```

`0` → **Edge**, `1` → **Reach**, `2` → **Surface**. Nothing is stored between runs — the ISO week is
the state, and it is the same number for every agent in the run. A year is 52 or 53 weeks, neither
divisible by 3, so at each year boundary one axis repeats and one is skipped. Expected, not a bug:
no axis ever goes more than five weeks unvisited, and the rotation evens out.

One axis per run is not a budget, it is the stop condition. The charter's read scope spans seven directories plus
`config.py`; asked all three questions at once, a scout would return more than ten finds and
`sweep-procedure.md` would abort the run as a scoping failure. State the axis in the scout's prompt
and tell it to ignore the other two.

## Edge — the provider API

Rotate one provider per run — jira, azure_devops, github, confluence, notion, calendar — picking the
one whose cassette in `tests/contract/` is oldest.

- Run `make contract` and read the cassette for the chosen provider.
- Compare the recorded response shape against the provider's current API docs (WebFetch). A field
  that has moved, been deprecated, or gained a required parameter is a real finding even though the
  test is green.
- Check every list call in that provider's module for explicit pagination and for a truncation guard.
- Check `jira_sync.py` against `azdevops_sync.py` for capability drift.

## Reach — does a connected provider arrive anywhere

Pick one provider and trace the whole chain, in order: credential getter in `config.py` → the
wizard's verification probe → scope discovery → each mode that consumes it → the output a user
finally sees.

- Read the provider's row in [`integrations-map.md`](../../integrations-map.md) first and correct it
  where the code has moved. That file is this axis's output; a run that changes nothing about it has
  probably not read carefully enough.
- A mode that **could** consume this provider and does not, with no reason recorded in the map's
  *Recorded gaps* section, is a find owned by that mode — not by you. Propose it there, and record
  the answer in the map when it comes back so the question is not re-asked in twelve weeks.
- Check that every fetch path registers coverage (`analysis/coverage.py::CoverageTracker`, or
  `standup/collector.py`'s error/skip classification). A source that fails silently reads as a zero,
  and a zero is a number someone will believe.
- Watch the canonical spelling. `azdevops`, `azdo` and `azuredevops` all name the same system in
  different modules today.

Everything under `Reads` in the charter is read-only. You may not open a PR against those paths in
this or any run.

## Surface — can a user tell

The four wizard steps in `ui/provider_select/`, the Credentials tab of the settings screen, and the
ops admission test.

- Connect-time: does the step verify, or does it accept a string and fail on first use? Five
  *provider* probes exist in `_verification.py` — `_verify_jira`, `_verify_azdevops`,
  `_verify_notion`, `_verify_confluence`, `_verify_vc_token`, alongside two model-config ones that
  are not providers. Check which fields are covered by one.
- Steady-state: is there anywhere a user learns that a credential has expired, other than a failed
  run? Is there anywhere they learn what a connected provider actually powers?
- Blast radius: a credential that silently enables a second provider (the `CONFLUENCE_* → JIRA_*`
  fallback) should say so on screen or not do it.

Anything you find in `ui/` is **tui-ux**'s to fix — propose it with the owner set.

## Every run, whichever axis

Ask one standing question and file at most one find from it: **did this run surface a question a mode
asks that no connected provider can answer?** Judge any candidate against the three-condition
admission test in the charter — read-only, attributable, answers a question a mode already asks — and
say in the proposal which mode consumes it first. An ops provider that fails any condition is not a
finding, it is a dashboard.
