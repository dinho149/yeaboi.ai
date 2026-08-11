# release promote ask

**Trigger** — cron `0 9 * * 1` (Mondays 09:00 UTC, after the digest)
**Summary** — asks once a week whether the accumulated pre-releases should become an official version
**Workstream** — none; this routine is the release channel's one human decision.
**Model** — `fast` ([models.md](../../models.md))

Merging to `main` does not ship to users any more. Every release-worthy merge publishes a PyPI
**pre-release** (`X.Y.ZrcN`) that `pip install yeaboi` cannot see, and those accumulate. This routine
is the one place a human is asked to turn a batch into an official version — the last backstop in
[house-rules.md](../../house-rules.md)'s gate, and the only one that involves somebody who has
actually been running the code.

It composes nothing. `scripts/release_channel.py` owns every comparison: which pre-release HEAD is,
what sits between the last final tag and now, and whether there is anything to promote at all. The
routine runs it and posts what it prints. A batch summarised by eye is a batch that quietly drops an
entry, and the entry it drops is the one nobody then knows shipped.

## Run

1. **Read the batch** — `uv run python scripts/release_channel.py --manifest --json`.

   **If `promotable` is `false`, stop. Post nothing, open nothing, comment nothing.** That is the
   ordinary state of a quiet week: `main` moved but the version line did not, so there is no new
   version to release and promoting would only re-tag one already out. A weekly message that says
   "nothing to promote" every week trains everyone to ignore the channel.

2. **Render the body** — `uv run python scripts/release_channel.py --manifest --markdown`. Use it
   verbatim. It already carries the changelog entries, the commit list, the install line and the
   `<!-- promote: X.Y.Z -->` marker that `publish.yml` reads to tell whether `main` moved between
   this ask and the approval. **Never edit the marker, and never write one by hand.**

3. **Open the issue, and never reuse a stale one** —
   `gh issue list --label release:promotion --state open --limit 1`.
   - None open: `gh issue create --label release:promotion --label type:chore` titled
     `[chore] promote X.Y.Z — N pre-releases pending`, with the rendered body.
   - One already open: **close it and open a fresh one.** Comment on the old one first, saying it
     is superseded and linking the new issue, then `gh issue close`.

   Commenting a refreshed manifest onto the open issue would be the obvious move and it is wrong.
   `publish.yml` reads the version the human approved from `<!-- promote: X.Y.Z -->` in the
   **issue body**, and this routine holds no `gh issue edit`, so it cannot update that body — the
   marker would keep saying last week's version while the manifest the human actually read sat in
   a comment underneath. Every promotion of a reused issue would then take the drift branch and
   announce a discrepancy that did not happen, which is worse than no disclosure because it is
   confidently wrong about the common case. A fresh issue per ask keeps the body, the title and
   the marker describing the same batch.

   Never leave two open. A ✅ on a stale ask promotes against a manifest nobody read.

4. **Ask, through `cowork-scribe`** — one message to `#yeaboi-claude` naming the version, the number
   of changes, how long the batch has been accumulating, and the install line. Then **one thread
   reply in the parsed contract**, plain text, no emoji and no bold:

   ```
   #<issue> — promote X.Y.Z — <issue link>
   ```

   `scripts/cowork_relay.py` parses that line before any human reads it (`PROMOTE_RE`), and a ✅ from
   an allowlisted human on it is what applies `release:promote` and cuts the release. Any other
   shape is read as an ordinary proposal approval, so this line is a contract, not a style.

## Stop conditions

- **Nothing pending → nothing posted.** Step 1 is the whole gate.
- **Never apply `release:promote` yourself**, and never hold a grant that could. The routine that
  asks the question must not be able to answer it — this is the same rule that keeps a sweep from
  applying `claude-implement` to its own proposal.
- **Never leave two `release:promotion` issues open.** Superseding one is closing it, not
  commenting on it.
- **Never apply a label to an issue.** Closing and creating are the only two writes here; the grant
  withholds `gh issue edit` precisely so this routine cannot label its own ask `release:promote`.
- **Never edit or invent a version.** Everything numeric on the page comes from
  `release_channel.py`; if it refuses to number the commit, report its message and stop rather than
  working around it — a version that goes backwards is exactly what it is refusing to let happen.
- A ❌ on the ask closes the issue and means "not this week". Next Monday opens a fresh one against
  the grown batch. Do not re-ask in between.
