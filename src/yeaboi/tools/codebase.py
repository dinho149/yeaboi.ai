"""Pure-Python tool for scanning a local repository.

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# read_codebase is a read-only, zero-network tool — it uses only pathlib and
# the standard library to walk a local directory. No credentials or tokens
# required. Risk level: low (read-only filesystem access).
#
# Output format matches github_read_repo so _scan_repo_context can treat
# local and remote scans uniformly when building the LLM context string.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from collections import defaultdict

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Directories that are never interesting to include in the tree or language count.
# These contain generated/vendored code, cache files, or VCS internals.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "dist",
    "build",
    ".build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".cache",
    "coverage",
    ".coverage",
    "htmlcoverage",
    ".eggs",
    "*.egg-info",
    ".DS_Store",
}

# Key config/manifest files to call out explicitly in the summary.
# Mirrors the same constant in github.py for a consistent output format.
_KEY_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".github",
    "README.md",
    "README.rst",
    "CONTRIBUTING.md",
    "Makefile",
    "requirements.txt",
    ".env.example",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.ts",
    "vite.config.js",
    ".terraform",
    "terraform.tf",
    "main.tf",
    "Chart.yaml",
}

# Extension → language display name. Used to build a language breakdown
# similar to GitHub's language bar. Only source-code extensions are included —
# data files (.json, .yaml, .csv) are intentionally omitted.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".rb": "Ruby",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".hpp": "C++",
    ".swift": "Swift",
    ".php": "PHP",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".clj": "Clojure",
    ".hs": "Haskell",
    ".ml": "OCaml",
    ".r": "R",
    ".R": "R",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".sass": "CSS",
    ".sql": "SQL",
    ".dart": "Dart",
    ".elm": "Elm",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".tf": "Terraform",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".pl": "Perl",
    ".pm": "Perl",
}

# Truncate README content at this many characters to avoid flooding LLM context.
_MAX_README_CHARS = 4_000

# Maximum characters for the indented file tree section. When the tree exceeds
# this budget, remaining directories are collapsed to "dir/ (N files)".
# Keeps the overall tool output bounded for large monorepos.
_MAX_TREE_CHARS = 6_000


def _should_skip(name: str) -> bool:
    """Return True if a directory name should be excluded from the scan."""
    return name in _SKIP_DIRS or name.startswith(".") or name.endswith(".egg-info")


def _walk(root: pathlib.Path, max_depth: int) -> tuple[list[str], list[str], dict[str, int], int]:
    """Walk root up to max_depth, collecting structure, key files, and language stats.

    Returns:
        - tree_lines: indented file tree lines (budget-limited to _MAX_TREE_CHARS)
        - key_files: paths of detected key config files (any depth)
        - lang_bytes: mapping of language name → total bytes across all source files
        - total_files: total number of files encountered (regardless of depth/budget)
    """
    key_files: list[str] = []
    lang_bytes: dict[str, int] = defaultdict(int)
    total_files = 0

    # Two-pass approach:
    # Pass 1 — collect all directory info (file counts, key files, lang bytes)
    # Pass 2 — build the indented tree within a character budget
    #
    # We store per-directory info so the tree builder can show collapsed
    # summaries like "utils/ (12 files)" when the budget runs out.
    import os

    # dir_info[rel_dir] = (sorted_subdirs, sorted_files)
    dir_info: dict[str, tuple[list[str], list[str]]] = {}

    for dirpath, dirs, files in os.walk(root):
        rel = pathlib.Path(dirpath).relative_to(root)
        depth = len(rel.parts)

        # Prune skipped directories in-place so os.walk never descends into them.
        dirs[:] = sorted(d for d in dirs if not _should_skip(d))

        if depth > max_depth:
            dirs.clear()  # Stop descending beyond max_depth
            continue

        rel_str = str(rel) if rel.parts else ""
        sorted_files = sorted(files)
        dir_info[rel_str] = (list(dirs), sorted_files)

        for name in files:
            total_files += 1
            file_path = pathlib.Path(dirpath) / name
            rel_path = str(file_path.relative_to(root))

            # Key file detection — check by name at any depth
            if name in _KEY_FILES:
                key_files.append(rel_path)

            # Language byte count
            lang = _EXT_TO_LANG.get(file_path.suffix)
            if lang:
                try:
                    lang_bytes[lang] += file_path.stat().st_size
                except OSError:
                    pass  # Unreadable file — skip silently

    # Build indented tree within a character budget.
    tree_lines: list[str] = []
    budget_used = 0
    budget_exceeded = False

    def _add_dir(rel_dir: str, indent: int) -> None:
        nonlocal budget_used, budget_exceeded
        if budget_exceeded:
            return

        info = dir_info.get(rel_dir)
        if info is None:
            return
        subdirs, files = info
        prefix = "  " * indent

        # Add files at this level
        for f in files:
            line = f"{prefix}{f}"
            budget_used += len(line) + 1
            if budget_used > _MAX_TREE_CHARS:
                remaining = len(files) - files.index(f)
                tree_lines.append(f"{prefix}... and {remaining} more files")
                budget_exceeded = True
                return
            tree_lines.append(line)

        # Recurse into subdirectories
        for d in subdirs:
            child_rel = f"{rel_dir}/{d}" if rel_dir else d
            child_info = dir_info.get(child_rel)
            dir_line = f"{prefix}{d}/"
            budget_used += len(dir_line) + 1

            if budget_used > _MAX_TREE_CHARS:
                budget_exceeded = True
                tree_lines.append(f"{prefix}... and more directories")
                return

            # If the child has many entries and we're running low on budget,
            # collapse it to a single summary line.
            if child_info:
                child_subdirs, child_files = child_info
                child_total = len(child_files) + len(child_subdirs)
                remaining_budget = _MAX_TREE_CHARS - budget_used
                if child_total > 20 and remaining_budget < 1500:
                    tree_lines.append(f"{dir_line} ({len(child_files)} files, {len(child_subdirs)} dirs)")
                    continue

            tree_lines.append(dir_line)
            _add_dir(child_rel, indent + 1)

    _add_dir("", 0)

    return tree_lines, sorted(set(key_files)), dict(lang_bytes), total_files


def _read_readme(root: pathlib.Path) -> str | None:
    """Read the first README found at the repo root, truncated."""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        candidate = root / name
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
                if len(content) > _MAX_README_CHARS:
                    content = content[:_MAX_README_CHARS] + f"\n\n[Truncated at {_MAX_README_CHARS} characters]"
                return content
            except OSError:
                pass
    return None


@tool
def read_codebase(path: str, max_depth: int = 4) -> str:
    """Scan a local repository directory and return a structured summary.

    Returns an indented file tree (budget-limited for large repos), detected
    languages by file size, key config/manifest files (pyproject.toml,
    Dockerfile, package.json, etc.), and the README content. Use this when
    the user has a local codebase rather than a remote repository URL.
    For large codebases the tree is automatically truncated — use
    read_local_file to drill into specific files of interest.

    path: Absolute or relative path to the repository root directory.
    max_depth: How many directory levels deep to scan (default 4).
    """
    # See docs: "Tools" — read-only tool pattern
    root = pathlib.Path(path).expanduser().resolve()

    if not root.exists():
        return f"Error: path does not exist: {path}"
    if not root.is_dir():
        return f"Error: path is not a directory: {path}"

    try:
        tree_lines, key_files, lang_bytes, total_files = _walk(root, max_depth)
    except PermissionError as e:
        return f"Error: permission denied reading {path}: {e}"
    except OSError as e:
        return f"Error: {e}"

    lines: list[str] = [f"Local repository: {root}"]
    if total_files:
        lines.append(f"Total files scanned: {total_files}")
    lines.append("")

    # Indented file tree (budget-limited by _MAX_TREE_CHARS)
    lines.append("File tree:")
    lines.extend(f"  {tl}" for tl in tree_lines)

    # Key files
    if key_files:
        lines.append("")
        lines.append("Key files detected:")
        for kf in key_files[:30]:
            lines.append(f"  {kf}")

    # Language breakdown (top 5 by bytes, same format as github_read_repo)
    if lang_bytes:
        total = sum(lang_bytes.values())
        lines.append("")
        lines.append("Languages:")
        for lang, size in sorted(lang_bytes.items(), key=lambda x: -x[1])[:5]:
            pct = size / total * 100
            lines.append(f"  {lang}: {pct:.1f}%")

    # README
    readme = _read_readme(root)
    if readme:
        lines.append("")
        lines.append("README:")
        lines.append("---")
        lines.append(readme)
        lines.append("---")

    return "\n".join(lines)


# Truncate file content at this many characters — matches github_read_file's limit
# so the LLM gets a consistent experience across local and remote reads.
_MAX_CONTENT_CHARS = 8_000

# Binary-indicating extensions that should never be read as text.
_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".svg",
        ".webp",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".obj",
        ".sqlite",
        ".db",
    }
)


@tool
def read_local_file(repo_path: str, file_path: str) -> str:
    """Read the contents of a specific file from a local repository.

    Use this after read_codebase identifies an important file (e.g. a config
    file, main source module, or API definition). The repo_path should be the
    same path passed to read_codebase. file_path is relative to the repo root.
    Truncates at 8 000 characters with a note if the file is larger.

    repo_path: Absolute or relative path to the repository root directory.
    file_path: Path to the file relative to the repo root (e.g. "src/main.py").
    """
    # See docs: "Tools" — read-only tool pattern
    root = pathlib.Path(repo_path).expanduser().resolve()
    target = (root / file_path).resolve()

    # Security: ensure the resolved path is inside the repo root to prevent
    # path traversal attacks (e.g. file_path="../../etc/passwd").
    if not str(target).startswith(str(root)):
        return f"Error: path traversal detected — {file_path} resolves outside the repository"

    if not target.exists():
        return f"Error: file not found: {file_path}"
    if not target.is_file():
        return f"Error: not a file: {file_path}"

    # Reject binary files — they produce garbage text and waste LLM context.
    if target.suffix.lower() in _BINARY_EXTENSIONS:
        return f"Error: {file_path} appears to be a binary file ({target.suffix}) — skipping"

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading {file_path}: {e}"

    truncated = False
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS]
        truncated = True

    size = target.stat().st_size
    header = f"File: {file_path} ({size} bytes)\n\n"
    suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
    return header + content + suffix


# Extensions accepted for docs in the scrum-docs/ directory.
# PDF support requires the optional pymupdf dependency (uv sync --extra pdf).
_DOC_EXTENSIONS = frozenset({".md", ".txt", ".rst", ".pdf"})

# Maximum total characters to read from all docs in scrum-docs/.
# Keeps the LLM context bounded even with many docs.
_MAX_DOCS_CHARS = 12_000


def _read_pdf(fpath: str) -> str | None:
    """Extract text from a PDF file using pymupdf.

    Returns None if pymupdf is not installed or the file cannot be read.
    Text is extracted page-by-page and joined with double newlines.
    """
    try:
        import pymupdf  # optional dependency: uv sync --extra pdf
    except ImportError:
        logger.debug("pymupdf not installed — skipping PDF: %s", fpath)
        return None
    try:
        doc = pymupdf.open(fpath)
        pages = [page.get_text() for page in doc]
        doc.close()
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        return text if text else None
    except Exception:
        logger.debug("Failed to read PDF: %s", fpath, exc_info=True)
        return None


@tool
def load_project_context(path: str = "", docs_dir: str = "") -> str:
    """Read SCRUM.md and scrum-docs/ directory and return combined project context.

    SCRUM.md is the project's free-form context file — analogous to CLAUDE.md for
    Claude Code. scrum-docs/ is an optional directory for additional documents
    (PRDs, design docs, architecture notes) exported from any source (Google Docs,
    Notion, etc.) as .md, .txt, .rst, or .pdf files.

    PDF support requires the optional pymupdf dependency (uv sync --extra pdf).
    PDFs without pymupdf are silently skipped.

    Both are read as plain text and concatenated. URLs are NOT fetched — the LLM
    reasons about the content as written. Returns a JSON envelope with the context
    string and a status dict describing what was loaded.

    path: Override the SCRUM.md file path. Defaults to SCRUM.md in CWD.
    docs_dir: Override the docs directory path. Defaults to scrum-docs/ in CWD.

    Returns a JSON string with keys: context (string or null), status (dict with
    name/status/detail). Returns an error JSON on unexpected failure.

    # See docs: "Tools" — read-only tool pattern
    """
    sections: list[str] = []
    loaded_names: list[str] = []

    try:
        # 1. Load SCRUM.md
        target = path.strip() if path.strip() else os.path.join(os.getcwd(), "SCRUM.md")
        if os.path.isfile(target):
            content = open(target).read().strip()  # noqa: WPS515
            if content:
                sections.append(content)
                loaded_names.append("SCRUM.md")

        # 2. Scan scrum-docs/ directory for additional documents
        docs_path = docs_dir.strip() if docs_dir.strip() else os.path.join(os.getcwd(), "scrum-docs")
        if os.path.isdir(docs_path):
            budget = _MAX_DOCS_CHARS
            doc_files = sorted(
                f
                for f in os.listdir(docs_path)
                if os.path.isfile(os.path.join(docs_path, f)) and os.path.splitext(f)[1].lower() in _DOC_EXTENSIONS
            )
            for fname in doc_files:
                if budget <= 0:
                    loaded_names.append(f"... and {len(doc_files) - doc_files.index(fname)} more (budget reached)")
                    break
                fpath = os.path.join(docs_path, fname)
                try:
                    if os.path.splitext(fname)[1].lower() == ".pdf":
                        doc_content = _read_pdf(fpath)
                        if doc_content is None:
                            continue
                        doc_content = doc_content.strip()
                    else:
                        doc_content = open(fpath).read().strip()  # noqa: WPS515
                except OSError:
                    continue
                if not doc_content:
                    continue
                if len(doc_content) > budget:
                    doc_content = doc_content[:budget] + f"\n\n[Truncated at {_MAX_DOCS_CHARS} total docs budget]"
                sections.append(f"--- {fname} ---\n{doc_content}")
                loaded_names.append(fname)
                budget -= len(doc_content)

        if not sections:
            status = {"name": "User context", "status": "skipped", "detail": "no SCRUM.md or scrum-docs/ found"}
            return json.dumps({"context": None, "status": status})

        combined = "\n\n".join(sections)
        detail = ", ".join(loaded_names)
        status = {"name": "User context", "status": "success", "detail": detail}
        return json.dumps({"context": combined, "status": status})

    except Exception as e:
        logger.debug("User context load failed (non-fatal)", exc_info=True)
        return json.dumps(
            {"context": None, "status": {"name": "User context", "status": "error", "detail": str(e)[:80]}}
        )
