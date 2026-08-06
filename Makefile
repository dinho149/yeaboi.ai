UV := $(or $(shell command -v uv 2>/dev/null),$(HOME)/.local/bin/uv)

# Editor CLI used by `make wt-open` to open each worktree in a new window.
# Override for forks of VS Code (e.g. `CODE=cursor make wt-open NAME=my-feature`).
CODE ?= code

.PHONY: install dev test test-fast test-v test-all lint format security run run-dry clean env pre-commit graph eval contract record smoke-test snapshot-update budget-report bump-patch bump-minor bump-major build publish help wt-new wt-open wt-headless wt-issue wt-list wt-rm wt-rm-all web web-dev web-check web-test web-install dev-board dev-poker dev-deck dev-editable site-seo site-check site-og site-serve pr-feedback cowork-setup cowork-check cowork-teardown

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install uv (if missing) and project dependencies
	@command -v uv >/dev/null 2>&1 || (echo "Installing uv..." && curl -LsSf https://astral.sh/uv/install.sh | sh)
	$(UV) venv
	$(UV) pip install -e ".[dev]"

env: ## Copy .env.example to .env (won't overwrite existing)
	@if [ -f .env ]; then echo ".env already exists — skipping"; else cp .env.example .env && echo "Created .env from .env.example — fill in your keys"; fi

test-fast: ## Unit tests only — < 3s, no graph compilation (tight edit-test loop)
	$(UV) run pytest tests/unit/ --tb=short -q
	@echo "✓ Unit tests passed"

test: ## Unit + integration + contract tests — full suite, no API keys needed
	$(UV) run pytest tests/unit/ tests/integration/ tests/contract/ --tb=short
	@echo "✓ All tests passed"

test-v: ## Unit + integration + contract tests (verbose)
	$(UV) run pytest tests/unit/ tests/integration/ tests/contract/ -v

test-all: ## Everything including golden evaluators (requires make eval separately for golden)
	$(UV) run pytest --ignore=tests/smoke --tb=short
	@echo "✓ Full test suite passed"

lint: ## Lint with ruff
	$(UV) run ruff check src/ tests/

format: ## Format with ruff
	$(UV) run ruff format src/ tests/

security: ## Security scan — bandit (ruff S) SAST + dependency CVE audit
	@echo "→ SAST (ruff incl. flake8-bandit S rules; respects pyproject ignores)"
	$(UV) run ruff check src/ tests/
	@echo "→ Dependency CVE audit (pip-audit against the synced environment)"
	$(UV) run --with pip-audit pip-audit
	@echo "✓ Security scan passed"

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

bump-patch: ## Bump the patch version in pyproject.toml (X.Y.Z -> X.Y.Z+1)
	$(UV) run python scripts/bump_version.py patch

bump-minor: ## Bump the minor version in pyproject.toml (X.Y.Z -> X.Y+1.0)
	$(UV) run python scripts/bump_version.py minor

bump-major: ## Bump the major version in pyproject.toml (X.Y.Z -> X+1.0.0)
	$(UV) run python scripts/bump_version.py major

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
	$(UV) run python -m http.server 8899 -d docs

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

cowork-check: ## Verify cowork labels, repo variables, and routine/README agreement
	@$(UV) run python scripts/cowork_setup.py --check

# Deleting a workstream label strips it off every issue carrying it, and nothing
# puts those back — hence the prompt, and hence the routines staying out of it.
cowork-teardown: ## Delete the cowork GitHub labels + model repo variables (prompts to confirm)
	@read -r -p "Delete the cowork labels and unset the model variables? Issues lose their labels. [y/N] " ans; \
	  if [ "$$ans" = "y" ] || [ "$$ans" = "Y" ]; then \
	    $(UV) run python scripts/cowork_setup.py --teardown --labels --variables --yes; \
	  else echo "[cowork] teardown aborted"; fi

clean: ## Remove build artifacts and caches
	rm -rf .venv build dist .pytest_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
