# Release published — announce

**Trigger** — GitHub event, release `published`
**Summary** — writes the notes and announces a published release
**Model** — `standard` ([models.md](../../models.md)) — writing notes from commits needs judgement
about what mattered

```json webhook
{"source": "github", "events": ["release"], "filter": {"actions": ["published"]}}
```

`publish.yml` cuts the tag and the GitHub Release when a human's merge lands on `main` — their
own PR, or the release batch PR they assembled and hand-tested
([release-signoff.md](../../release-signoff.md)). This routine tells people about it. Everything
runs through `cowork-scribe`.

Two consequences of that channel split, both of which change what this routine is looking at. The
release body now *is* a batch manifest — `scripts/release_channel.py --manifest --release-notes`, one
section per changelog entry since the last final tag — so the span you are describing is a week or
more of merges rather than one, and there is more to group. And a pre-release never reaches here at
all: `publish-beta.yml` creates no tag and no GitHub Release, so the webhook simply does not fire
for one. The stop condition below is belt and braces rather than the thing keeping rc noise out.

## Run

1. `gh release view <tag> --json tagName,body,publishedAt` and
   `git log <previous-tag>..<tag> --oneline --no-merges` for the real change list.

2. **Write the release note from the commits, not from the auto-generated body.** The batch manifest
   in the body is a faithful list and still not the announcement — it is grouped by version, and a
   reader wants it grouped by what changed for them. Read it for the user-facing prose
   `auto-version.yml` wrote per bump; take the change list from the commits. Group by what a
   user would care about — new capability, fixed behaviour, integration change — and drop pure
   chores. If the whole release is chores and dependency bumps, say that in one line rather than
   inflating it.

3. **Notion** — append an entry to the changelog page under 🤙 yeaboi (create it on first run):
   version, date, and the grouped notes. Newest first.

4. **Slack** — one `#yeaboi-claude` message:

   ```slack
   🎉 **3.7.0 is out** — 8 changes · [release notes](https://github.com/dinho149/yeaboi.ai/releases/tag/v3.7.0)

   1. Standup now names every source it skipped instead of reporting a partial run as a whole one
   2. The live standup share is editable in the browser
   3. cd-deploy reaches GitHub without the `gh` CLI, so merges to `cowork/` actually deploy
   ───────────────────────────

   `pip install --upgrade yeaboi` · [on PyPI](https://pypi.org/project/yeaboi/3.7.0/)
   ```

   Two or three things that actually matter, grouped by what changed for a user — not the batch
   manifest, which is grouped by version. If the whole release is chores and dependency bumps, say
   that in one line rather than inflating it into three.

5. **Sanity check before announcing** — confirm the version is actually installable
   (`pip index versions yeaboi` or the PyPI JSON API). If PyPI has not caught up, wait and re-check
   rather than announcing a version nobody can install.

6. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- Never announce a pre-release or a draft release.
- Never edit the GitHub release body — it is generated, and rewriting it fights `publish.yml`.
