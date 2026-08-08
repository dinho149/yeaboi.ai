# Release published — announce

**Trigger** — GitHub event, release `published`
**Summary** — writes the notes and announces a published release
**Model** — `standard` ([models.md](../../models.md)) — writing notes from commits needs judgement
about what mattered

`publish.yml` cuts the tag and the GitHub Release after a version bump lands on `main`. This routine
tells people about it. Everything runs through `cowork-scribe`.

## Run

1. `gh release view <tag> --json tagName,body,publishedAt` and
   `git log <previous-tag>..<tag> --oneline --no-merges` for the real change list.

2. **Write the release note from the commits, not from the auto-generated body.** Group by what a
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
