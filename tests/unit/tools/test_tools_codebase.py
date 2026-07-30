"""Tests for the read_codebase local repo scanner tool.

All filesystem access uses tmp_path (pytest's temporary directory fixture)
so no real repository paths are required. Tests verify the happy path,
key-file detection, language breakdown, README inclusion, depth limiting,
skipped directories, and error handling.
"""

import json
import pathlib

from yeaboi.tools.codebase import (
    _BINARY_EXTENSIONS,
    _EXT_TO_LANG,
    _KEY_FILES,
    _MAX_CONTENT_CHARS,
    _MAX_TREE_CHARS,
    _SKIP_DIRS,
    _read_readme,
    _walk,
    load_project_context,
    read_codebase,
    read_local_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    """Write a dict of {relative_path: content} into tmp_path and return it."""
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# _walk
# ---------------------------------------------------------------------------


class TestWalk:
    def test_tree_includes_files_and_dirs(self, tmp_path):
        _make_repo(
            tmp_path,
            {
                "src/main.py": "x=1",
                "tests/test_main.py": "pass",
                "README.md": "# Hello",
            },
        )
        tree, _, _, _ = _walk(tmp_path, max_depth=2)
        tree_text = "\n".join(tree)
        assert "src/" in tree_text
        assert "tests/" in tree_text
        assert "README.md" in tree_text

    def test_skipped_dirs_excluded(self, tmp_path):
        _make_repo(
            tmp_path,
            {
                "src/app.py": "x=1",
                "node_modules/lib/index.js": "module=1",
                "__pycache__/app.cpython.pyc": "garbage",
                ".git/config": "git config",
            },
        )
        tree, _, _, _ = _walk(tmp_path, max_depth=3)
        tree_text = "\n".join(tree)
        assert "node_modules" not in tree_text
        assert "__pycache__" not in tree_text
        assert ".git" not in tree_text

    def test_language_bytes_counted(self, tmp_path):
        _make_repo(
            tmp_path,
            {
                "app.py": "x = 1\n",
                "helper.py": "def f(): pass\n",
                "main.js": "console.log('hi');\n",
            },
        )
        _, _, lang_bytes, _ = _walk(tmp_path, max_depth=2)
        assert "Python" in lang_bytes
        assert "JavaScript" in lang_bytes
        assert lang_bytes["Python"] > 0
        assert lang_bytes["JavaScript"] > 0

    def test_key_files_detected(self, tmp_path):
        _make_repo(
            tmp_path,
            {
                "pyproject.toml": "[project]\nname = 'x'",
                "Dockerfile": "FROM python:3.11",
                "src/app.py": "pass",
            },
        )
        _, key_files, _, _ = _walk(tmp_path, max_depth=2)
        assert "pyproject.toml" in key_files
        assert "Dockerfile" in key_files

    def test_depth_limiting(self, tmp_path):
        _make_repo(
            tmp_path,
            {
                "a/b/c/deep.py": "x=1",
                "a/shallow.py": "y=2",
            },
        )
        tree, _, _, _ = _walk(tmp_path, max_depth=1)
        tree_text = "\n".join(tree)
        assert "a/" in tree_text
        # depth 1 should include files at depth 1 (a/shallow.py) but not depth 3
        assert "deep.py" not in tree_text

    def test_unknown_extensions_not_in_lang_bytes(self, tmp_path):
        _make_repo(tmp_path, {"data.json": '{"k": 1}', "config.yaml": "key: val"})
        _, _, lang_bytes, _ = _walk(tmp_path, max_depth=2)
        assert "JSON" not in lang_bytes
        assert "YAML" not in lang_bytes

    def test_nested_key_file_detected(self, tmp_path):
        _make_repo(tmp_path, {"infra/Dockerfile": "FROM ubuntu"})
        _, key_files, _, _ = _walk(tmp_path, max_depth=3)
        assert "infra/Dockerfile" in key_files

    def test_total_files_counted(self, tmp_path):
        _make_repo(tmp_path, {"a.py": "x", "b.py": "y", "src/c.py": "z"})
        _, _, _, total = _walk(tmp_path, max_depth=2)
        assert total == 3

    def test_tree_budget_limits_output(self, tmp_path):
        """A repo with many files should produce a tree shorter than the budget."""
        files = {f"pkg/mod{i}/file{j}.py": f"x={j}" for i in range(50) for j in range(20)}
        _make_repo(tmp_path, files)
        tree, _, _, total = _walk(tmp_path, max_depth=4)
        tree_text = "\n".join(tree)
        assert len(tree_text) <= _MAX_TREE_CHARS + 200  # small overshoot from final line
        assert total == 1000


# ---------------------------------------------------------------------------
# _read_readme
# ---------------------------------------------------------------------------


class TestReadReadme:
    def test_reads_readme_md(self, tmp_path):
        (tmp_path / "README.md").write_text("# My Project\nAwesome.", encoding="utf-8")
        result = _read_readme(tmp_path)
        assert result is not None
        assert "My Project" in result

    def test_reads_readme_rst_if_no_md(self, tmp_path):
        (tmp_path / "README.rst").write_text("My Project\n=========", encoding="utf-8")
        result = _read_readme(tmp_path)
        assert result is not None
        assert "My Project" in result

    def test_prefers_readme_md_over_rst(self, tmp_path):
        (tmp_path / "README.md").write_text("MD version", encoding="utf-8")
        (tmp_path / "README.rst").write_text("RST version", encoding="utf-8")
        result = _read_readme(tmp_path)
        assert result is not None
        assert "MD version" in result

    def test_returns_none_when_no_readme(self, tmp_path):
        assert _read_readme(tmp_path) is None

    def test_truncates_long_readme(self, tmp_path):
        long_content = "A" * 10_000
        (tmp_path / "README.md").write_text(long_content, encoding="utf-8")
        result = _read_readme(tmp_path)
        assert result is not None
        assert "Truncated" in result
        assert len(result) < 10_000


# ---------------------------------------------------------------------------
# read_codebase (end-to-end)
# ---------------------------------------------------------------------------


class TestReadCodebase:
    def test_returns_string(self, tmp_path):
        _make_repo(tmp_path, {"src/app.py": "x = 1"})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_local_repository_header(self, tmp_path):
        _make_repo(tmp_path, {"app.py": "x = 1"})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "Local repository" in result

    def test_includes_file_tree_section(self, tmp_path):
        _make_repo(tmp_path, {"src/app.py": "x = 1", "tests/test_app.py": "pass"})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "File tree" in result
        assert "src/" in result
        assert "tests/" in result

    def test_includes_languages_section(self, tmp_path):
        _make_repo(tmp_path, {"app.py": "x = 1\n" * 50, "main.js": "console.log(1);\n" * 10})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "Languages" in result
        assert "Python" in result
        assert "JavaScript" in result

    def test_includes_key_files_section(self, tmp_path):
        _make_repo(tmp_path, {"pyproject.toml": "[project]", "src/app.py": "pass"})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "Key files" in result
        assert "pyproject.toml" in result

    def test_includes_readme_section(self, tmp_path):
        _make_repo(tmp_path, {"README.md": "# My Repo\nA great project."})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "README" in result
        assert "My Repo" in result

    def test_omits_readme_when_absent(self, tmp_path):
        _make_repo(tmp_path, {"app.py": "x = 1"})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "README" not in result

    def test_skips_node_modules(self, tmp_path):
        _make_repo(
            tmp_path,
            {
                "src/app.ts": "const x = 1;",
                "node_modules/lib/index.js": "module.exports = {}",
            },
        )
        result = read_codebase.invoke({"path": str(tmp_path)})
        # "node_modules/" should not appear in the file tree — skip the first
        # line which contains the full tmp_path (pytest names it after the test).
        tree_section = result.split("\n", 1)[1]  # drop the "Local repository: ..." line
        assert "node_modules/" not in tree_section

    def test_includes_total_file_count(self, tmp_path):
        _make_repo(tmp_path, {"a.py": "x", "b.py": "y", "src/c.py": "z"})
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "Total files scanned: 3" in result

    def test_large_repo_tree_is_bounded(self, tmp_path):
        """Monorepo-scale test: tree output should be bounded by budget."""
        files = {f"services/svc{i}/src/mod{j}.py": f"x={j}" for i in range(30) for j in range(30)}
        _make_repo(tmp_path, files)
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert "Total files scanned: 900" in result
        # The tree section should be bounded, not contain all 900 file paths
        assert len(result) < 20_000  # README(4k) + tree(6k) + overhead — well under 20k

    def test_error_nonexistent_path(self, tmp_path):
        result = read_codebase.invoke({"path": str(tmp_path / "does_not_exist")})
        assert result.startswith("Error:")
        assert "does not exist" in result

    def test_error_path_is_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = read_codebase.invoke({"path": str(f)})
        assert result.startswith("Error:")
        assert "not a directory" in result

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        """Path starting with ~ should be expanded correctly."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "app.py").write_text("x = 1")
        result = read_codebase.invoke({"path": "~"})
        assert isinstance(result, str)
        assert not result.startswith("Error:")


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants:
    def test_skip_dirs_contains_common_dirs(self):
        assert ".git" in _SKIP_DIRS
        assert "node_modules" in _SKIP_DIRS
        assert "__pycache__" in _SKIP_DIRS
        assert ".venv" in _SKIP_DIRS

    def test_key_files_contains_common_manifests(self):
        assert "pyproject.toml" in _KEY_FILES
        assert "package.json" in _KEY_FILES
        assert "Dockerfile" in _KEY_FILES
        assert "README.md" in _KEY_FILES

    def test_ext_to_lang_covers_common_extensions(self):
        assert _EXT_TO_LANG[".py"] == "Python"
        assert _EXT_TO_LANG[".ts"] == "TypeScript"
        assert _EXT_TO_LANG[".go"] == "Go"
        assert _EXT_TO_LANG[".rs"] == "Rust"

    def test_binary_extensions_contains_common_types(self):
        assert ".png" in _BINARY_EXTENSIONS
        assert ".pdf" in _BINARY_EXTENSIONS
        assert ".zip" in _BINARY_EXTENSIONS
        assert ".exe" in _BINARY_EXTENSIONS
        assert ".pyc" in _BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# read_local_file
# ---------------------------------------------------------------------------


class TestReadLocalFile:
    def test_reads_file_content(self, tmp_path):
        _make_repo(tmp_path, {"src/main.py": "print('hello')\n"})
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "src/main.py"})
        assert "print('hello')" in result
        assert "File: src/main.py" in result

    def test_shows_file_size(self, tmp_path):
        content = "x = 42\n"
        _make_repo(tmp_path, {"app.py": content})
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "app.py"})
        assert f"({len(content)} bytes)" in result

    def test_truncates_large_files(self, tmp_path):
        large = "A" * (_MAX_CONTENT_CHARS + 1000)
        _make_repo(tmp_path, {"big.py": large})
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "big.py"})
        assert "Truncated" in result
        # Header + truncated content + suffix should be less than original
        assert len(result) < len(large)

    def test_error_nonexistent_file(self, tmp_path):
        _make_repo(tmp_path, {"app.py": "x = 1"})
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "missing.py"})
        assert result.startswith("Error:")
        assert "not found" in result

    def test_error_directory_not_file(self, tmp_path):
        _make_repo(tmp_path, {"src/app.py": "x = 1"})
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "src"})
        assert result.startswith("Error:")
        assert "not a file" in result

    def test_rejects_binary_files(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "image.png"})
        assert result.startswith("Error:")
        assert "binary" in result

    def test_rejects_path_traversal(self, tmp_path):
        _make_repo(tmp_path, {"app.py": "x = 1"})
        result = read_local_file.invoke({"repo_path": str(tmp_path), "file_path": "../../etc/passwd"})
        assert result.startswith("Error:")
        assert "traversal" in result

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "config.py").write_text("DEBUG = True")
        result = read_local_file.invoke({"repo_path": "~", "file_path": "config.py"})
        assert "DEBUG = True" in result

    def test_error_nonexistent_repo(self, tmp_path):
        result = read_local_file.invoke({"repo_path": str(tmp_path / "nope"), "file_path": "app.py"})
        assert result.startswith("Error:")


class TestSandboxGating:
    """The three codebase tools deny paths outside the sandbox whitelist.

    tmp_path is whitelisted by the top-level conftest fixture; the denied
    paths below are outside every allowed root, so the tools must return the
    violation message (tools report errors as strings for the LLM to relay).
    """

    def test_read_codebase_denied_outside_whitelist(self):
        result = read_codebase.invoke({"path": "/denied-sandbox-dir/repo"})
        assert result.startswith("Error:")
        assert "YEABOI_ALLOWED_PATHS" in result

    def test_read_local_file_denied_outside_whitelist(self):
        result = read_local_file.invoke({"repo_path": "/denied-sandbox-dir/repo", "file_path": "a.py"})
        assert result.startswith("Error:")
        assert "YEABOI_ALLOWED_PATHS" in result

    def test_read_local_file_sibling_prefix_traversal_blocked(self, tmp_path):
        """The old startswith() guard allowed /repo → /repo-secret; is_relative_to must not."""
        repo = tmp_path / "repo"
        repo.mkdir()
        sibling = tmp_path / "repo-secret"
        sibling.mkdir()
        (sibling / "creds.txt").write_text("secret")
        result = read_local_file.invoke({"repo_path": str(repo), "file_path": "../repo-secret/creds.txt"})
        assert "path traversal detected" in result

    def test_load_project_context_denied_override(self):
        result = load_project_context.invoke({"path": "/denied-sandbox-dir/SCRUM.md"})
        payload = json.loads(result)
        assert payload["context"] is None
        assert "YEABOI_ALLOWED_PATHS" in payload["status"]["detail"]

    def test_read_codebase_allowed_in_whitelist(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hi')")
        result = read_codebase.invoke({"path": str(tmp_path)})
        assert not result.startswith("Error:")
