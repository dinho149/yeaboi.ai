UV := $(or $(shell command -v uv 2>/dev/null),$(HOME)/.local/bin/uv)

# Editor CLI used by `make wt-open` to open each worktree in a new window.
# Override for forks of VS Code (e.g. `CODE=cursor make wt-open NAME=my-feature`).
CODE ?= code

# `test` and `ship-gate` order their prerequisites deliberately (cheap checks
# first, unit before integration). `make -j` would run them concurrently, and
# two pytest processes in one worktree invent failures.
.NOTPARALLEL:

.PHONY: install dev test test-fast test-slow test-scoped test-v test-all lint format format-check security package-check preflight ship-gate run run-dry clean env pre-commit graph demo demo-render eval contract record smoke-test snapshot-update budget-report bump-patch bump-minor bump-major build publish beta-check beta-sign-maintenance beta-sign-integration beta-promote help wt-new wt-open wt-headless wt-issue wt-list wt-rm wt-rm-all web web-dev web-check web-test web-install dev-board dev-poker dev-deck dev-editable site-seo site-check site-og site-serve pr-feedback cowork-setup cowork-agenda cowork-check cowork-slots cowork-blocked cowork-teardown go-build go-test go-lint parity cowork-queue cowork-migrate cowork-metrics cowork-lapsed cowork-owner cowork-glyphs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install uv (if missing) and project dependencies
	@command -v uv >/dev/null 2>&1 || (echo "Installing uv..." && curl -LsSf https://astral.sh/uv/install.sh | sh)
	$(UV) venv
	$(UV) pip install -e ".[dev]"

env: ## Copy .env.example to .env (won't overwrite existing)
	@if [ -f .env ]; then echo ".env already exists — skipping"; else cp .env.example .env && echo "Created .env from .env.example — fill in your keys"; fi

# Every unit lane runs through these three variables so the paths and the
# parallel flags cannot drift apart between the Makefile, CI and the hooks.
#
# UNIT_PATHS carries `tests/*.py` as well as `tests/unit/`. Those five files
# (215 tests) were run by no CI job at all for months — `test-fast` and `test`
# both name subdirectories, and only `test-all`, which nothing calls, picked
# them up. One of them had been failing that whole time.
UNIT_PATHS ?= tests/unit/ $(wildcard tests/test_*.py)
# A command-line `UNIT_PATHS=` beats `?=`, so CI handing over an empty selection
# would reach pytest with no paths at all — and pytest then falls through to
# pyproject's `testpaths = ["tests"]`, quietly collecting the integration,
# contract, golden and parity suites as well. That is not the "degrades to the
# full unit lane" the callers assume, so resolve the empty case here instead.
UNIT_LANE = $(if $(strip $(UNIT_PATHS)),$(UNIT_PATHS),tests/unit/ $(wildcard tests/test_*.py))
SLOW_LANE = $(if $(strip $(SLOW_PATHS)),$(SLOW_PATHS),tests/integration/ tests/contract/)
SLOW_PATHS ?= tests/integration/ tests/contract/
# `--dist loadfile`, not the default `load`: it keeps every test in a file on one
# worker, so module-scoped fixtures, the shared `tmp_path` conventions and the
# handful of tests that bind a socket behave exactly as they do serially. The
# integration lane stays serial — `tests/integration/test_repl.py` monkeypatches
# ten-plus names and CLAUDE.md forbids editing it.
PYTEST_PARALLEL ?= -n auto --dist loadfile

test-fast: ## Unit tests only — the tight edit-test loop
	$(UV) run pytest $(UNIT_LANE) $(PYTEST_PARALLEL) --tb=short -q
	@echo "✓ Unit tests passed"

test-slow: ## Integration + contract only — the half `test-fast` does not cover
	$(UV) run pytest $(SLOW_LANE) --tb=short
	@echo "✓ Integration & contract tests passed"

# One pytest process for 10,383 unit tests plus 562 integration ones took 408s,
# of which 310s was the unit half run serially — the same tests `test-fast`
# finishes in ~50s with `-n auto`. Expressed as prerequisites rather than two
# copied recipe lines so the paths and flags cannot drift from UNIT_LANE /
# SLOW_LANE / PYTEST_PARALLEL, which is the whole reason those variables exist.
# Same tests, same order, same split as CI's two test jobs.
test: test-fast test-slow ## Unit + integration + contract tests — full suite, no API keys needed
	@echo "✓ All tests passed"

# `python3`, not `$(UV) run`: scripts/test_scope.py imports the standard library
# only, and putting a dependency resolve in front of the hook that runs on every
# commit is how a fast gate becomes one people disable.
# The `||` is load-bearing: a non-zero exit inside `$$( )` is invisible to make,
# so a crash in the selector would silently become an empty path list. Fall back
# to the whole unit lane, the same direction CI falls back in.
test-scoped: ## Only the areas the working tree touches, plus the always-run guards
	@python3 scripts/test_scope.py --working-tree --explain || echo "scope failed — running the full unit lane"
	@paths=$$(python3 scripts/test_scope.py --working-tree --unit-paths) || paths=""; \
		[ -n "$$paths" ] || paths="$(UNIT_LANE)"; \
		$(UV) run pytest $$paths $(PYTEST_PARALLEL) --tb=short -q
	@echo "✓ Scoped unit tests passed"

test-v: ## Unit + integration + contract tests (verbose)
	$(UV) run pytest $(UNIT_LANE) $(SLOW_LANE) -v

test-all: ## Everything including golden evaluators (requires make eval separately for golden)
	$(UV) run pytest --ignore=tests/smoke --tb=short
	@echo "✓ Full test suite passed"

# `scripts/` is in the paths deliberately. It was not, and CI's required
# "Lint (ruff)" job runs this target — so a lint error in a script was caught by
# pre-commit and by nothing else, on a repo where the fleet, the release channel
# and the test selector all live in scripts/. Found the honest way: this very
# change tripped it.
lint: ## Lint with ruff
	$(UV) run ruff check src/ tests/ scripts/

format: ## Format with ruff
	$(UV) run ruff format src/ tests/ scripts/

# `make format` writes; this one asserts. CI's "Format check (ruff)" is a
# required status check and had no Makefile target, so the only way to fail it
# was to open the PR.
format-check: ## What CI's "Format check (ruff)" job runs — asserts, never writes
	$(UV) run ruff format --check src/ tests/ scripts/

# `security: lint`, not a second copy of `ruff check src/ tests/` — the SAST half
# of this target WAS that command, byte for byte, so `make lint security` linted
# twice. As a prerequisite it still runs standalone and make resolves it once.
security: lint ## Security scan — bandit (ruff S) SAST + dependency CVE audit
	@echo '→ SAST (ruff incl. flake8-bandit S rules; respects pyproject ignores) — ran above as make lint'
	@echo "→ Dependency CVE audit (pip-audit against the synced environment)"
	$(UV) run --with pip-audit pip-audit
	@echo "✓ Security scan passed"

# ── The ship gate ───────────────────────────────────────────────────────────
# `make test` proves the Python suite. It does not prove the eight other things
# CI checks — the format check above, the front-end bundles, the docs site, the
# Go sidecar, the parity suite unskipped, the golden evaluators and the wheel's
# contents. Those used to be discovered after the PR was already open.
#
# `preflight` runs only the ones this branch's diff actually needs, decided by
# scripts/test_scope.py — the same selector CI's `scope` job uses, whose third
# rule is that anything it cannot classify runs everything.

package-check: ## What CI's "Wheel contains the bundles" job runs (uv build + assert)
	$(UV) build
	python3 scripts/check_wheel_bundles.py

preflight: ## Run only the optional CI jobs this branch's diff needs (BASE=origin/main)
	python3 scripts/preflight.py --base $(BASE)

BASE ?= origin/main

# Fail-fast order: seconds-long checks first, then the suite, then the network
# audit, then the heavy optional jobs. This is what /ship runs, as ONE make
# invocation, so `lint` resolves once for both itself and `security`.
ship-gate: lint format-check test security preflight ## The full local gate /ship runs — everything CI will check
	@echo "✓ ship gate passed"

pre-commit: ## Install pre-commit hooks
	$(UV) run pre-commit install

run: ## Run the yeaboi CLI (use ARGS="--flag" to pass arguments)
	$(UV) run yeaboi $(ARGS)

run-dry: ## Run the TUI with fake delays — no LLM calls
	$(UV) run yeaboi --dry-run $(ARGS)

eval: ## Run golden dataset evaluators
	$(UV) run pytest tests/golden/ -v

contract: ## Run contract tests (recorded API responses + LLM provider parsing)
	$(UV) run pytest tests/contract/ -v

smoke-test: ## Run smoke tests against live APIs (requires real credentials)
	$(UV) run pytest tests/smoke/ -v -m smoke

record: ## Re-record VCR cassettes against real APIs (requires API keys)
	$(UV) run pytest tests/ -v --record-mode=rewrite -m vcr

snapshot-update: ## Update syrupy snapshot baselines after intentional formatter changes
	$(UV) run pytest tests/unit/test_formatters.py --snapshot-update -v

budget-report: ## Show live prompt token counts for trend monitoring (runs token budget tests with -s)
	$(UV) run pytest tests/unit/test_token_budgets.py -v -s

graph: ## Generate agent graph visualisation PNG
	$(UV) run python scripts/generate_graph_png.py

demo: ## Re-record docs/demo.cast.gz + docs/demo.gif deterministically (requires agg: brew install agg)
	$(UV) run python scripts/record_demo.py

demo-render: ## Re-render docs/demo.gif from the committed cast (theme/size tweaks, no re-record)
	$(UV) run python scripts/record_demo.py --render-only

bump-patch: ## Bump the patch version in pyproject.toml (X.Y.Z -> X.Y.Z+1)
	$(UV) run python scripts/bump_version.py patch

bump-minor: ## Bump the minor version in pyproject.toml (X.Y.Z -> X.Y+1.0)
	$(UV) run python scripts/bump_version.py minor

bump-major: ## Bump the major version in pyproject.toml (X.Y.Z -> X+1.0.0)
	$(UV) run python scripts/bump_version.py major

# --- Beta sign-off (see cowork/release-signoff.md) --------------------------
#
# Every release-worthy merge publishes a PyPI pre-release; a human turns the
# accumulated batch into the official X.Y.Z once a week. These are that human's
# commands, and there are two test sessions rather than one: the fleet
# maintains what exists AND builds one provider integration a week, and those are
# different things to sit down and exercise.
#
# `beta-check` only reports. Each `beta-sign-*` records that track's sign-off on
# the promotion ask, and the LAST one writes the bare `<!-- tested: -->` marker
# publish.yml greps for — which is what lets it cut the final from the exact
# commit that was tested, and what stops a half-signed batch being promoted.
#
# Two targets rather than `make beta-sign <track>`: Make reads a bare word as a
# second goal, so that spelling fails with "No rule to make target 'integration'".

beta-check: ## What is installable, what changed, and what to exercise by hand
	@$(UV) run python scripts/beta_signoff.py check

beta-sign-maintenance: ## Record the maintenance sign-off (security, bugs, chores, docs)
	@$(UV) run python scripts/beta_signoff.py sign maintenance

beta-sign-integration: ## Record the integration sign-off (this week's provider campaign)
	@$(UV) run python scripts/beta_signoff.py sign integration

beta-promote: ## Promote the tested pre-release to the official X.Y.Z (prompts)
	@$(UV) run python scripts/beta_signoff.py promote

# --- Front end — TS sources in frontend/, built output committed ------------
#
# `make test` never runs any of these: the Python suite reads the committed
# bundles, so contributors (and CI's Python jobs) need no Node at all.

web-install: ## Install front-end dependencies (npm ci from the committed lockfile)
	cd frontend && npm ci

web: ## Build the front-end bundles into src/yeaboi/web/static (commit the result)
	@test -d frontend/node_modules || $(MAKE) web-install
	cd frontend && npm run build
	@echo "✓ bundles built — remember to commit src/yeaboi/web/static"

web-test: ## Front-end unit tests (vitest + jsdom + axe + the theme contrast matrix)
	@test -d frontend/node_modules || $(MAKE) web-install
	cd frontend && npm test

web-check: ## What CI runs: typecheck, test, rebuild, fail if the committed bundles are stale
	@test -d frontend/node_modules || $(MAKE) web-install
	cd frontend && npm run typecheck && npm test && npm run build
	@# --porcelain rather than `git diff --exit-code`: diff is blind to untracked
	@# files, so a brand-new entry that nobody committed would slip through.
	@test -z "$$(git status --porcelain -- src/yeaboi/web/static)" \
	  || { echo ""; git status --short -- src/yeaboi/web/static; \
	       echo "✗ committed bundles are stale — run 'make web' and commit src/yeaboi/web/static"; exit 1; }
	@echo "✓ committed bundles match the sources"

web-dev: ## Vite dev server on :5399 with HMR, proxying /api to a running dev board
	@echo "  frontend/dev/{retro,poker,deck,gate,export}.html on http://localhost:5399/"
	@echo "  boards need ?token=<token> from 'make dev-board' or 'make dev-poker';"
	@echo "  for poker, set YEABOI_DEV_API=http://127.0.0.1:5273 so /api proxies there."
	cd frontend && npm run dev

# --- Docs site (docs/ → yeaboi.ai via GitHub Pages) --------------------------
#
# NOT the same thing as the web-* targets above: those build the app's React
# bundles into src/yeaboi/web/static. These manage the marketing/docs website in
# docs/, which is hand-written flat HTML with no build step, published straight
# off main by GitHub Pages. The SEO head block, the crawlable footer, the ?v=
# cache-bust, sitemap.xml and robots.txt are generated into it — 18 pages x a
# dozen meta tags is exactly what rots by hand. The staleness check lives in
# tests/unit/test_site_seo.py (so it runs in make test-fast and every CI lane);
# site-check is the same assertion for humans.

site-seo: ## Regenerate the SEO block, crawlable footer, ?v=, sitemap.xml and robots.txt in docs/
	$(UV) run python scripts/gen_site_seo.py

site-check: ## Fail if any generated part of docs/ is stale (also asserted by make test-fast)
	$(UV) run python scripts/gen_site_seo.py --check

site-og: ## Re-render the 1200x630 Open Graph card (needs the charts extra for Pillow)
	$(UV) run --extra charts python scripts/gen_og_card.py

site-serve: ## Serve docs/ on :8899 exactly as GitHub Pages would, to preview before merging
	@echo "→ http://localhost:8899  (Ctrl-C to stop)"
	$(UV) run python scripts/serve_docs.py

dev-board: ## Seeded retro board on :5173 for front-end development (prints the URL)
	$(UV) run python scripts/dev_board.py

dev-poker: ## Seeded planning-poker board on :5273 for front-end development
	$(UV) run python scripts/dev_poker.py

dev-deck: ## Seeded reporting slide deck on :5373 for front-end development
	$(UV) run python scripts/dev_deck.py

dev-editable: ## Seeded correctable standup document on :5473 for front-end development
	$(UV) run python scripts/dev_editable.py

build: ## Build sdist + wheel into dist/
	$(UV) build

publish: ## Publish to PyPI (use GitHub Actions for production releases)
	$(UV) publish

# --- Worktrees — parallel Claude sessions, one per task (NAME= required) ------

# Guard NAME= for every wt-* target without duplicating the message.
define need-name
	@test -n "$(NAME)" || { echo "usage: make $@ NAME=<slug>  (e.g. NAME=standup-fix)"; exit 1; }
endef

wt-new: ## Create worktree .claude/worktrees/NAME off latest origin/main (branch + .env + venv) + open in VS Code with claude auto-running
	$(need-name)
	CODE="$(CODE)" bash scripts/wt.sh "$(NAME)" open

wt-open: ## Open worktree in a NEW VS Code window with claude auto-running (creates it off latest origin/main first if needed)
	$(need-name)
	CODE="$(CODE)" bash scripts/wt.sh "$(NAME)" open

wt-headless: ## Create worktree off latest origin/main WITHOUT VS Code auto-launch (driven by background agents instead)
	$(need-name)
	bash scripts/wt.sh "$(NAME)" headless

wt-issue: ## Create worktree from the branch of GitHub issue N (linked branch / closing PR); HEADLESS=1 to skip VS Code
	@test -n "$(ISSUE)" || { echo "usage: make wt-issue ISSUE=<number> [HEADLESS=1]"; exit 1; }
	CODE="$(CODE)" bash scripts/wt-issue.sh "$(ISSUE)" $(if $(filter-out 0,$(HEADLESS)),headless,open)

wt-list: ## List worktrees (branch, clean/dirty, path)
	@bash scripts/wt-list.sh

wt-rm: ## Remove worktree dir + branch
	$(need-name)
	bash scripts/wt.sh "$(NAME)" rm

wt-rm-all: ## Remove ALL worktrees under .claude/worktrees/ (prompts to confirm)
	@read -r -p "Remove ALL .claude/worktrees/* worktrees and their branches? [y/N] " ans; \
	  if [ "$$ans" = "y" ] || [ "$$ans" = "Y" ]; then \
	    for w in $$(git worktree list --porcelain | awk '/^worktree /{print $$2}' | grep "/.claude/worktrees/" || true); do \
	      name="$${w#*/.claude/worktrees/}"; echo "[wt-rm-all] removing $$name"; bash scripts/wt.sh "$$name" rm || true; \
	    done; \
	    git worktree prune; echo "[wt-rm-all] done."; \
	  else echo "[wt-rm-all] aborted"; fi

# --- PR feedback — the merge gate on unanswered review comments ---------------

# Five things comment on a PR here and nothing used to read any of it back. This
# reports what is still unanswered; the `pr-feedback` commit status posted by
# .github/workflows/pr-feedback.yml is what actually holds the merge.
pr-feedback: ## Report unanswered review feedback on a PR (PR=123, or the current branch's)
	@$(UV) run python scripts/pr_feedback.py $(if $(PR),--pr $(PR),)

# --- Cowork — stand the standing workstreams up (see cowork/README.md) --------

# Everything here is derived from cowork/ rather than typed twice: the labels come
# from workstreams/, the model variables from models.md, the routines from the
# README table. The half a shell cannot do — the account-scoped routines and the
# Linear labels — is what /cowork covers (status, deploy, run, pause, teardown).

cowork-setup: ## Create the cowork GitHub labels + model repo variables (idempotent)
	$(UV) run python scripts/cowork_setup.py

cowork-agenda: ## Show what the cowork fleet runs today, and over the next week
	@$(UV) run python scripts/cowork_setup.py --agenda --text

cowork-check: ## Verify cowork labels, repo variables, and routine/README agreement
	@$(UV) run python scripts/cowork_setup.py --check

cowork-slots: ## Show how full each workstream's proposal queue is (WORKSTREAM=name for one)
	@$(UV) run python scripts/cowork_setup.py --proposal-slots $(WORKSTREAM)

cowork-queue: ## Show what each workstream's sweep should build next (WORKSTREAM=name for one)
	@$(UV) run python scripts/cowork_setup.py --queued $(WORKSTREAM)

cowork-lapsed: ## Show the lapsed questions and which are due to close (WORKSTREAM=name for one)
	@$(UV) run python scripts/cowork_setup.py --lapsed $(WORKSTREAM)

# FILE, not PATH: `make cowork-owner PATH=…` would override the shell's PATH for
# the recipe and nothing would resolve.
cowork-owner: ## Which workstream's charter claims a path (FILE=src/yeaboi/retro/engine.py)
	@$(UV) run python scripts/cowork_setup.py --owner $(FILE)

cowork-glyphs: ## Show each workstream's Slack area glyph
	@$(UV) run python scripts/cowork_setup.py --glyphs

.PHONY: cowork-lens
cowork-lens: ## Run one hygiene lens over one workstream (LENS=dead-code WS=tui-ux, JSON=1)
	@$(UV) run python scripts/hygiene_lens.py --lens $(or $(LENS),dead-code) --workstream $(WS) $(if $(JSON),--json,)

.PHONY: cowork-fuzz
cowork-fuzz: ## Fuzz the live TUI in a pty (SEEDS=6 STEPS=120, or SEED=41 to replay one)
	@$(UV) run python scripts/tui_fuzz.py $(if $(SEED),--seed $(SEED),--seeds $(or $(SEEDS),6)) --steps $(or $(STEPS),120)

cowork-metrics: ## What the fleet merged, found, fixed and cost (WINDOW=30, JSON=1 for the raw report)
# The token is borrowed from `gh` when the environment has none. The script itself
# stays honest about needing one — this is the developer-on-a-laptop case, where
# `gh auth login` has already happened and exporting GH_TOKEN by hand is the only
# thing standing between a logged-in machine and a report.
	@GH_TOKEN=$${GH_TOKEN:-$$(gh auth token 2>/dev/null)} 		$(UV) run python scripts/cowork_metrics.py --window $(or $(WINDOW),30) $(if $(JSON),--json,)

cowork-migrate: ## One-off: reclassify auto-lane proposals as cowork:queued (add YES=1 to apply)
	@$(UV) run python scripts/cowork_setup.py --migrate-proposals $(if $(YES),--yes,)

cowork-blocked: ## Ask whether a standing fault is already reported (MARKER="cd-deploy: …")
	@$(UV) run python scripts/cowork_setup.py --blocked-report $(MARKER)

# Deleting a workstream label strips it off every issue carrying it, and nothing
# puts those back — hence the prompt, and hence the routines staying out of it.
cowork-teardown: ## Delete the cowork GitHub labels + model repo variables (prompts to confirm)
	@read -r -p "Delete the cowork labels and unset the model variables? Issues lose their labels. [y/N] " ans; \
	  if [ "$$ans" = "y" ] || [ "$$ans" = "Y" ]; then \
	    $(UV) run python scripts/cowork_setup.py --teardown --labels --variables --yes; \
	  else echo "[cowork] teardown aborted"; fi

# ── Go core (the yeaboi-core sidecar — see contracts/v1/rpc.md) ──────────────
# The binary is NEVER committed; bin/ is gitignored. `make test` stays
# pytest-only: the parity suite skips itself when the binary is absent, and CI
# builds the binary in its own job before running parity unskipped.

go-build: ## Build the Go sidecar into bin/yeaboi-core (static, CGO-free)
	cd go && CGO_ENABLED=0 go build -o ../bin/yeaboi-core ./cmd/yeaboi-core

go-test: ## Run the Go unit tests
	cd go && go test ./...

go-lint: ## Vet + gofmt check for the Go tree
	cd go && go vet ./...
	@cd go && files="$$(gofmt -l .)"; if [ -n "$$files" ]; then echo "$$files"; echo "gofmt: files need formatting"; exit 1; fi

parity: go-build ## Build the sidecar and run the Python↔Go parity suite unskipped
	YEABOI_CORE_BIN=$(CURDIR)/bin/yeaboi-core $(UV) run pytest tests/parity -v

clean: ## Remove build artifacts and caches
	rm -rf .venv build dist .pytest_cache .ruff_cache *.egg-info src/*.egg-info bin
	find . -type d -name __pycache__ -exec rm -rf {} +
