# Hygiene lenses

A **lens** is a standing thing to look for, with a command behind it that produces the evidence.
The scout is told to prefer evidence over impression and that "could be cleaner" is not a find —
lenses are what turn that instruction into something it can act on.

They exist because the fleet has never found dead code, and not because dead code is forbidden:
[house-rules.md](house-rules.md)'s auto lane has permitted "dead code removal" and "a flaky or
outright broken test" since it was written. Nothing ever told a scout to look, and nothing handed
it a command whose output settles the question.

**No new category, and no new type.** Every lens below lands in an existing auto-lane category
(3, 4 or 6) and returns one of the scout's four words. This is the fleet finding work it was
already allowed to do.

## Survey narrow, confirm wide, change narrow

A scout scoped to one charter cannot prove a symbol is unused — the surviving reference may sit in
another workstream's directory. The rule that resolves it, and the only new one this file adds:

> A lens **finds** only inside your charter's `**Owns**` paths. To **confirm** a find it may read
> the entire repository — proving a negative is a read, and a read changes nothing about who may
> edit. What stays scoped is the find: the symbol lives in your paths, and only your builder may
> touch it.

This is the same shape as the `**Reads**` grant a charter may already declare, stated for
confirmation rather than for discovery. `scripts/hygiene_lens.py` implements both halves, and
`tests/unit/test_hygiene_lens.py` asserts over every charter that no find ever names a file
outside the one that asked for it.

Two lenses land on that rule in a shape worth reading twice, and both are the rule working rather
than an exception to it:

- **`crash-fuzz` runs wide and finds narrow.** A fuzzer cannot be confined to one charter's
  screens — the TUI is one process and a keystroke goes wherever the app takes it. So the *run*
  reads everything and the *find* is the deepest frame we own: a crash tui-ux reaches in
  `agent/nodes.py` is **planning's**, reported as `outside-owns` and never filed. That is why both
  charters run the lens rather than one running it for everybody.
- **A `layering` invariant may be declared by one charter and answered by all of them.**
  `applies_to: "*"` scans the pattern inside *each* charter's own files, so web-ux writes down that
  headers come from `web/security.py` once, and a second CSP spelled in `standup/` is **standup's**
  find. The charter that owns a boundary is rarely the charter that crosses it.

## Running one

```bash
uv run python scripts/hygiene_lens.py --lens dead-code --workstream tui-ux
uv run python scripts/hygiene_lens.py --lens layering --workstream platform --json
make cowork-lens LENS=duplication WS=platform
```

`crash-fuzz` is the one that costs real time — six processes, a couple of minutes — and
`scripts/tui_fuzz.py` can be driven on its own, which is how a reported find is reproduced:

```bash
uv run python scripts/tui_fuzz.py --seeds 6 --steps 120
uv run python scripts/tui_fuzz.py --seed 41 --steps 400        # replay exactly what crashed
```

`--paths <workstream>` prints how a charter's `**Owns**` block resolved, which is the thing to
check first when a lens reports nothing on a surface you expected finds on.

## The lenses

| Lens | Type | Lane ceiling | Looks for |
|---|---|---|---|
| `dead-code` | `chore` | auto | A module-level function or class whose name appears nowhere else in the repository — Python, Go, TypeScript, markdown, YAML or JSON |
| `assertion-free-tests` | `chore` | auto | A `test_*` with no `assert`, no `pytest.raises`, no assertion helper, no `AssertionError` trap and no `# must not raise` |
| `layering` | `chore` | auto **only when the fix is an import swap** | A pattern that must not appear in a charter's own paths, and the helper that should be there instead |
| `stale-flags` | `chore` | auto | An `is_new=True` badge on a feature that has shipped in two or more `vX.Y.Z` releases, dated by blaming the line |
| `duplication` | `chore` | **propose only** | Two or more blocks of ≥70 tokens and ≥8 lines, identical once numbers and strings are normalised, both inside one charter |
| `crash-fuzz` | `bug` | auto (a hang proposes) | A traceback or a wedged screen reached by seeded keystrokes against the live TUI, whose deepest frame we own is in this charter |

The first three shipped first and were piloted on three charters; the last three followed once that
pilot had a fortnight behind it. All six run the same way and obey the same rule.

Everything each lens must never flag is in
[`.github/hygiene/lens-policy.yml`](../.github/hygiene/lens-policy.yml), each entry with the reason
it exists. A test fails on an exclusion with no `why`, for the same reason the CodeQL policy carries
one: an exclusion without a reason is indistinguishable from a bug that was easier to silence than
to fix.

**`lane` in that file is a ceiling, not a grant.** It says the best a find from this lens can be.
The scout still classifies it against house-rules like anything else, and still proposes whenever
it is arguing with itself.

## One find per lens per run

Six dead symbols come back as **one** find listing up to `max_batch` of them, not six finds. Two
reasons, and neither is tidiness:

- the scout's stop condition is "more than 10 finds means the charter's scope is wrong", and a
  first run over a charter that has never been swept this way would trip it on a charter whose
  scope is fine;
- shipping them together is already permitted — house-rules grants three same-`type` `chore` items
  in one PR, and six one-line deletions reviewed together is one reading rather than six.

Anything over the cap is **reported as `held`, never dropped**. A silent truncation reads as "that
was all of it", which is the one thing a hygiene number must not imply.

## What the first audit found, and why the exclusions look the way they do

Every lens was run by hand over every charter before any of it was wired into a sweep. Four
false-positive classes came out of that, and each is now a policy entry or a detector fix rather
than a caveat:

- **`tests/parity/` delegates to `_assert_match`.** Twelve real, load-bearing parity tests read as
  assertion-free because the helper starts with an underscore. The detector strips it now.
- **`# must not raise` is an assertion**, written that way at 23 sites. It is the only shape
  available for "the assertion is that this line completed", and a detector that cannot read it is
  measuring the convention instead of the tests. A stub raising `AssertionError` over a call that
  must never happen counts for the same reason.
- **`CoreBinaryHook` has no caller and never will** — hatchling finds a build hook by scanning for
  a subclass of its interface. Deleting it merges green and stops producing the `yeaboi-core`
  wheel.
- **`paths.py` and `config.py` are provider modules.** Seven `get_*_log_dir` helpers have no caller
  today. They are **unadopted, not dead**: the fix is to route those modes' logging through them,
  which the Observability rule already requires. A lens that cannot tell those two apart deletes
  the convention instead of the drift, and this is the one that would have looked most like a
  clean, obvious win.

That last one is the general lesson: **a mechanically correct detector can be pointed at a
documented convention and produce a find whose right answer is the opposite of what it suggests.**
When a lens names something you would have to argue for deleting, it is a `propose`.

The second round of lenses was audited the same way and produced three more, all of which were
found by running the thing rather than by reasoning about it:

- **A literal table duplicates itself.** `SEED` in `dev_board.py` — sixty rows of
  `("went_well", "…", "Ada"),` — normalises to a periodic run of `( #str , #str , #str ) ,` and
  matches itself at every period. It was the very first thing `duplication` reported and it looked
  exactly like a real find. The fix is a floor on how much *vocabulary* a window carries, not on
  how long it is, and it is calibrated from both ends: the two tables carry four and seven distinct
  tokens, and a block of repetitive-but-real code carries nineteen.
- **A clone longer than the window is one find, not two hundred.** A 167-token duplicated block
  matches at every offset inside it. Merging by walking the shift forward took tui-ux from 560
  finds to 79, and none of the 481 that went away were different from each other.
- **A closed network is not a crash.** `crash-fuzz` points `HTTPS_PROXY` at a dead port so a mode
  that ignores `--dry-run` cannot spend money, and the resulting `APIConnectionError` is the
  containment working. It is excluded by exception name, and the same run still exercises the error
  path a user with no network takes, which is worth having.
- **One second of a full input buffer is not a wedge.** The render loop reads on a timeout and a
  heavy repaint can outrun it, so a failed keystroke is retried once under the full hang budget
  before anything is called a hang. Confirm before you claim, the same as everywhere else here.

Three traps in the fuzzer itself are worth naming, because each one made it *look* like it was
working while it was not:

- **`buf += chunk` over a 60 FPS screen is quadratic.** A twenty-second run paints tens of
  megabytes, and accumulating them wedged the fuzzer long before it wedged the app — the same trap
  `tests/integration/test_tui_smoke.py` documents in its own comments, walked straight back into.
  The pty tail is bounded now and the byte total is counted separately.
- **`start_new_session=True` is what makes `proc.kill()` insufficient.** It is also what gives the
  child a controlling terminal, so it stays — but the group has to be killed, or an interrupted run
  leaves a TUI reparented to init, repainting a screen nobody is reading at 100% of a core, for as
  long as the machine is up. One of those was left behind on a real laptop before this was fixed.
- **`select` says writable when *some* space is free.** The 4000-byte paste in the alphabet is
  bigger than that space, so a write that cleared the readiness check could still park forever. The
  master end is `O_NONBLOCK` and partial writes are looped under one deadline.

**`ok` is ambiguous on its own**, so every run reports how many keystrokes it actually sent and how
many bytes were painted. A seed that quit on its third key also finds nothing, and a fuzzer whose
silence cannot be told from a pass is not evidence of anything.

And one that is not a false positive but reads like one: **`duplication` is deliberately not on
tui-ux's list.** It finds a great deal there, and almost all of it is `ui/mode_select/__init__.py`
duplicating itself — which is the 14k-LOC problem that charter already has a standing proposal for.
A propose-only lens costs one of two slots, and re-filing an answered question every week under a
different name is how a lens stops being read. Run it by hand when the split is being argued.

## What the auto lane may do with each of these

Five of the six are `chore` and land in categories house-rules already permits. `crash-fuzz` is the
only one that returns a `bug`, and house-rules admits a bug to the auto lane **only on a regression
test that fails before the fix and passes after**. A seed is exactly that — the key sequence is the
test and `--seed N` is how it is run — which is what makes an unattended crash fix legitimate here
and would not be legitimate for a crash somebody merely described.

**A hang is not.** There is no mechanical reproduction of "it stopped repainting" that a builder can
turn into a failing test, so a `hang` verdict proposes however confident the evidence looks. The
lane in the policy file is a ceiling for both; the evidence line says which one you have.

### A hang has no traceback, so it has to be asked where it is

This is the one part of `crash-fuzz` worth understanding before reading its output. A crash carries
frames; a hang carries none — so for as long as the lens only parsed tracebacks, **every hang it
found was reported as "no frame in our tree" and dropped**. It could see the failure and could not
say whose it was, which is the same as not finding it.

The child therefore runs with `PYTHONFAULTHANDLER=1`, and a wedged process is sent `SIGABRT` — to
the process, not the group, because we want one interpreter's stacks and not an abort of everything
it started. The interpreter prints every thread's stack to stderr on its way out, on the same pty,
and the deepest frame we own is the line it stopped on. Two details make that parseable: a
faulthandler dump writes `line 12 in f` where a traceback writes `line 12, in f`, and it announces
itself *most recent call first* — the opposite order — so the frames are reversed on the way in and
everything downstream can read `frames[-1]` as the deepest one.

**The first thing this lens found is a hang, and it is still open.** `--seed 2 --steps 150` wedges
the TUI after 28 keystrokes inside `_sweep_menu_in` — a menu transition — with the screen still
repainting and stdin no longer being read. It is reproducible, it is attributed to **tui-ux**, and
the root cause has not been established here: an animation that does not drain input while it plays
is the obvious reading, and confirming that is the sweep's job, not this file's. It is a `propose`
for the reason above.

## Declaring a layering invariant

A charter that owns a boundary states it in its **Standing concerns** for the scout to read, and
the machine-readable half goes under `layering.invariants` in the policy file with a `workstream`,
a `forbid` pattern, the `instead` a builder should use, and a `why`. **A workstream with no
mechanical boundary declares none** — a lens with nothing to say on a surface must say nothing.

Two escape hatches, and they are not interchangeable:

- `exempt:` waives a **whole file**, and is for the file that *is* the boundary — `paths.py` is
  where paths come from, so it cannot violate "paths come from `paths.py`".
- `# lens-exempt: <id> — why` waives **one site**. A boundary usually has one or two deliberate
  crossings, each with a reason; recording it beside the code keeps the rest of the file live,
  which exempting the file would not. `config.py`'s live `Path.home()` is the one in the tree
  today, and its docstring says why.

## Rollout

**First:** three lenses on three charters — **tui-ux**, **platform**, and **retro** as a control.
The control was the real test. Cadence is tiered by surface size because "a 1.2k-LOC mode asked for
findings weekly will invent them", and the same risk applies to a lens: **one that finds something
every fortnight on a quiet 2.5k-LOC surface is wrong.**

**Now:** all six lenses, and all thirteen sweeps run at least three. Retro still returns nothing on
every lens it runs, which is the expected and correct result — and it has now stayed true through a
second round of detectors, which is the only evidence anybody has that these are detectors rather
than work generators. **If that changes without the code changing, the lens is the fault**: say so
in the run log and file it against `platform`.

Two lenses are narrower than the rest and say so in the policy file rather than in prose:

- `crash-fuzz` carries a `workstreams:` allowlist — **tui-ux** and **planning**, the two charters
  that own a screen. Everyone else's crash is still *found* by those runs and reported as
  `outside-owns`.
- `duplication` is on five sweeps rather than thirteen. It is propose-only, so every find it makes
  spends a slot, and a lens that saturates the queue gets the whole mechanism muted.
