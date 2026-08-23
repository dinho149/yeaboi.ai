#!/bin/sh
# yeaboi installer — https://yeaboi.ai/install.sh
#
#   curl -LsSf https://yeaboi.ai/install.sh | sh
#
# Installs uv (if missing), then installs yeaboi as an isolated uv tool on a
# Python that uv fetches itself. The point is that the Python already on the
# machine is irrelevant: `pip` and `pipx` both use the interpreter they are run
# with and hard-fail when it is too old, which is the single most common reason
# a first-time user never sees the product.
#
# Overridable, all optional:
#   YEABOI_PYTHON         Python to build the tool env on   (default: >=3.10)
#   YEABOI_PACKAGE        what to install                   (default: yeaboi)
#   YEABOI_UV_ARGS        extra args for `uv tool install`  (default: none)
#   UV_INSTALLER_VERSION  uv release used when uv is absent
#
#   curl -LsSf https://yeaboi.ai/install.sh | YEABOI_PACKAGE='yeaboi[voice]' sh
#
# Constraints this file must keep, both of them load-bearing:
#
#   * It never reads stdin. Under `curl | sh` the script *is* stdin, so a single
#     `read` would swallow the rest of itself and execute a truncated program.
#   * POSIX sh only — no [[, no arrays, no `source`, no `function`. The documented
#     invocation pipes into `sh`, which on Debian and Ubuntu is dash, not bash.
#
# It writes only under $HOME and never calls sudo.

set -eu

# A version specifier, not a version. `--python 3.10` pins every user to the
# oldest supported runtime and downloads a ~30 MB interpreter onto machines that
# already have a perfectly good one; `--python '>=3.10'` reuses what is there and
# downloads only when nothing qualifies. Keep this byte-identical to
# `requires-python` in pyproject.toml — tests/unit/test_install_script.py asserts it.
YEABOI_PYTHON="${YEABOI_PYTHON:->=3.10}"
YEABOI_PACKAGE="${YEABOI_PACKAGE:-yeaboi}"
YEABOI_UV_ARGS="${YEABOI_UV_ARGS:-}"
UV_INSTALLER_VERSION="${UV_INSTALLER_VERSION:-0.11.2}"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------
# Native Windows is refused rather than half-served: src/yeaboi/ui/shared/_input.py
# imports termios and tty at module scope, so the TUI cannot start there at all.
# Installing successfully into a shell that can never run the program is a worse
# failure than refusing with a pointer.
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW* | MSYS* | CYGWIN*)
        die "yeaboi's terminal UI needs a POSIX terminal and cannot run on native Windows.
       Install inside WSL (https://learn.microsoft.com/windows/wsl/install) and
       re-run this command from your WSL shell."
        ;;
esac

# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv (the Python package manager yeaboi installs through)..."
    uv_installer="https://astral.sh/uv/${UV_INSTALLER_VERSION}/install.sh"
    # Pinned rather than tracking latest, for the same reason this repo pins its
    # GitHub Actions by SHA. uv's own installer checksum-verifies the binary it
    # downloads, so that half is already covered.
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$uv_installer" | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$uv_installer" | sh
    else
        die "neither curl nor wget is available — install one, or install uv yourself:
       https://docs.astral.sh/uv/getting-started/installation/"
    fi

    # Make uv usable in *this* run rather than telling the user to open a new
    # shell. The uv installer writes this env file for exactly this purpose.
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    command -v uv >/dev/null 2>&1 || die "uv installed but is not on PATH — open a new shell and re-run."
fi

# ---------------------------------------------------------------------------
# yeaboi
# ---------------------------------------------------------------------------
# This is the line the whole script exists for, and it is the one setting that
# must not be left to a default: automatic downloads are uv's default, but a
# ~/.config/uv/uv.toml or a corporate image setting python-downloads = "never"
# collapses the install straight back onto system Python — the exact failure
# being fixed here.
export UV_PYTHON_DOWNLOADS=automatic

say "Installing ${YEABOI_PACKAGE} on Python ${YEABOI_PYTHON}..."
# YEABOI_UV_ARGS is deliberately unquoted so callers can pass more than one flag.
# shellcheck disable=SC2086
uv tool install --python "$YEABOI_PYTHON" $YEABOI_UV_ARGS "$YEABOI_PACKAGE"

# ---------------------------------------------------------------------------
# PATH and next steps
# ---------------------------------------------------------------------------
if ! command -v yeaboi >/dev/null 2>&1; then
    say ""
    say "yeaboi is installed but not yet on your PATH."
    uv tool update-shell >/dev/null 2>&1 || true
    say "Open a new terminal, or add this to your shell profile:"
    say ""
    say "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

say ""
say "Done. Next:"
say ""
say "    yeaboi --setup      # add your API key"
say "    yeaboi              # launch the TUI"
say ""
