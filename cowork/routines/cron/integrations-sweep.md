# integrations sweep

**Trigger** — cron `30 6 * * 2` (Tue 06:30 UTC)
**Workstream** — [`workstreams/integrations.md`](../../workstreams/integrations.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = integrations`.

## Focus

Rotate one provider per week — jira, azure_devops, github, confluence, notion, calendar — picking the
one whose cassette in `tests/contract/` is oldest.

- Run `make contract` and read the cassette for the chosen provider.
- Compare the recorded response shape against the provider's current API docs (WebFetch). A field
  that has moved, been deprecated, or gained a required parameter is a real finding even though the
  test is green.
- Check every list call in that provider's module for explicit pagination and for a truncation guard.
- Check `jira_sync.py` against `azdevops_sync.py` for capability drift.
