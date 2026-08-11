# Release published — announce

**Trigger** — GitHub event, release `published`
**Summary** — writes the notes and announces a published release
**Model** — `standard` ([models.md](../../models.md)) — writing notes from commits needs judgement
about what mattered

```json webhook
{"source": "github", "events": ["release"], "filter": {"actions": ["published"]}}
```

`publish.yml` cuts the tag and the GitHub Release when a human **promotes** the accumulated
pre-releases — a ✅ on `cron/release-promote-ask.md`'s weekly question, not a merge. This routine
tells people about it. Everything runs through `cowork-scribe`.

Two consequences of that channel split, both of which change what this routine is looking at. The
release body now *is* a batch manifest — `scripts/release_channel.py --manifest --markdown`, one
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

4. **Slack** — one `#yeaboi-claude` message: version, the two or three things that actually matter,
   the PyPI link (`https://pypi.org/project/yeaboi/<version>/`), and the GitHub release link.

5. **Sanity check before announcing** — confirm the version is actually installable
   (`pip index versions yeaboi` or the PyPI JSON API). If PyPI has not caught up, wait and re-check
   rather than announcing a version nobody can install.

## Stop conditions

- Never announce a pre-release or a draft release.
- Never edit the GitHub release body — it is generated, and rewriting it fights `publish.yml`.
