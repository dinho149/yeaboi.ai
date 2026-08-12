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

   **If an ask is already open and its `<!-- beta: … -->` marker names the same tag as
   `installable_tag`, stop too.** Nothing has been published since it was written, so the open
   issue already describes this batch exactly. Re-asking would close a live issue, open an
   identical one, and put a second unanswered ✅ prompt in Slack — two things to react to for one
   decision, which is the shape that lets a stale reply get approved a week later.

2. **Find what was already signed off on** — `installable` is what is on PyPI; the newest
   `<!-- tested: beta/X.Y.ZrcN -->` comment across
   `gh issue list --label release:promotion --state all --limit 5` is what a human has actually
   run. `make beta-check` writes those. If one exists and it is not the newest published
   pre-release, pass it as `--since <tag>`.

   That is what keeps a skipped week bounded. An unanswered batch grows every merge, and the
   fourth Monday of re-reading the same twelve entries to find the two new ones is the Monday it
   gets skimmed. The delta is the part with new risk in it; the rest was reviewed already.

   When the signed-off tag *is* the newest published one, `nothing_new` is true and the rendered
   body says so instead of printing a checklist — everything since is on `main` and in nothing
   installable, so there is a promotion to make and nothing new to test. Post it as rendered.

3. **Render the body** — `uv run python scripts/release_channel.py --manifest --markdown`
   (with `--since <tag>` when step 2 found one). Use it verbatim. It carries the changelog
   entries, the commit list, the install line for the pre-release that **really exists**, the
   hand-test checklist for the surfaces this batch touched, and two markers `publish.yml` reads:
   `<!-- promote: X.Y.Z -->` for what was asked, and `<!-- beta: beta/X.Y.ZrcN -->` for the commit
   to cut it from. **Never edit a marker, and never write one by hand.**

4. **Open the issue, and never reuse a stale one** —
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

5. **Ask, through `cowork-scribe`** — one message to `#yeaboi-claude`:

   ```slack
   🏷️ **Promote 3.7.0?** — 8 changes over 6 days · tested build `3.7.0rc8`

   Everything merged since `v3.6.0` is already installable as a pre-release. Promoting cuts the
   official `3.7.0` to PyPI from that exact build and opens a GitHub release.

   [The batch, and what to check](https://github.com/dinho149/yeaboi.ai/issues/244)

   Try it first: `pip install --pre yeaboi==3.7.0rc8`  ·  or run `make beta-check`
   ✅ on the reply below to release · ❌ to wait another week
   ```

   The rc named here is `installable`, never `latest_prerelease`. The second is what the *next*
   merge would be numbered — every docs and chore commit raises it past anything on PyPI — so
   quoting it hands out an install command that 404s, and the person it fails for is the one
   person who did what was asked.

   **Every number here is copied from the manifest you just read, never restated from memory** —
   the version, the count, the span, the rc. This message is *composed*, unlike the issue body,
   which `scripts/release_channel.py --manifest --markdown` renders and which you post byte for
   byte; `cowork-scribe.md` draws that line explicitly because it is the one place a composed
   sentence could name a version the issue does not.

   The ✅/❌ pair is a footer that instructs, which is the one context those two glyphs are
   allowed in — never in the title line, never in a heading.

   Then **one thread reply in the parsed contract**, plain text, no emoji and no bold:

   ```slack-reply
   #<issue> — promote X.Y.Z — <issue link>
   ```

   `scripts/cowork_relay.py` parses that line before any human reads it (`PROMOTE_RE`), and a ✅ from
   an allowlisted human on it is what applies `release:promote` and cuts the release. Any other
   shape is read as an ordinary proposal approval, so this line is a contract, not a style.

## Stop conditions

- **Nothing pending, or nothing new since the open ask → nothing posted.** Step 1 is the whole
  gate, and its second half is what stops a skipped week producing a fresh ask every Monday for a
  batch that has not changed.
- **Never quote `latest_prerelease` as something to install.** `installable` is the only field
  backed by a `beta/*` tag, and a tag is only pushed after the upload succeeds.
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
