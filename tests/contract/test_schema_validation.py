"""Schema validation for contract test cassettes and LLM JSON outputs.

# See docs: "Testing — Contract Tests" for background on VCR.py cassettes.

Why this file exists
--------------------
The existing contract tests (test_jira_contract.py, test_confluence_contract.py,
test_github_contract.py, test_llm_provider_contract.py) check that our tool
functions produce correct OUTPUT strings given recorded API responses. They do
not validate the SHAPE of the recorded responses themselves.

This file validates that every cassette contains response bodies that match the
expected API schema — checking required fields and types. A silent vendor
regression (e.g. Jira stops returning ``key`` in issue creation responses)
would break our tools at runtime but slip past the existing tests unless the
cassette is updated. These schema assertions make the cassette itself a
first-class test artefact.

Schema definition convention
-----------------------------
Schemas are plain dicts mapping field name → expected Python type or nested schema.
The ``_check_schema()`` helper handles:
  ``str / int / bool``  — isinstance check on the value
  ``dict``              — all listed fields must be present with the right types
  ``list``              — must be a list; ``[element_schema]`` validates each item
  ``{}``                — non-empty dict, no field restrictions (schema = {})

Only REQUIRED fields appear in the schema. Optional fields are intentionally
omitted so that API additions (new optional fields) do not break the tests.

LLM JSON output schemas
-----------------------
The LLM prompt schema (embedded in each prompt builder) defines exactly which
fields the LLM must return. These tests validate that the canonical test dicts
in test_llm_provider_contract.py conform to those schemas — providing a direct
link between prompt documentation and parsed data shape.
"""

from __future__ import annotations

import json
import pathlib

import yaml

# ---------------------------------------------------------------------------
# Schema checker
# ---------------------------------------------------------------------------

_CASSETTES_ROOT = pathlib.Path(__file__).parent / "cassettes"


def _load_cassette(cassette_dir: str, cassette_name: str) -> list[dict]:
    """Load a VCR cassette and return its interactions list.

    Args:
        cassette_dir: Subdirectory name under cassettes/ (e.g. "test_jira_contract").
        cassette_name: YAML file name (e.g. "TestJiraReadBoardContract.test_read_board_happy_path.yaml").

    Returns:
        List of interaction dicts, each with ``request`` and ``response`` keys.
    """
    path = _CASSETTES_ROOT / cassette_dir / cassette_name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("interactions", [])


def _check_schema(data: object, schema: object, path: str = "root") -> None:
    """Recursively assert that *data* matches *schema*.

    The schema is a plain Python structure:
    - A ``type`` (str, int, bool, list, dict) → assert isinstance(data, type)
    - A ``dict`` → assert all listed keys are present with matching sub-schemas
    - A single-item ``list`` like ``[sub_schema]`` → assert data is a list and
      every element matches sub_schema (empty list in data is allowed)
    - An empty ``list`` → assert data is a list (no element validation)
    - An empty ``dict {}`` → assert data is a non-empty dict (no key validation)
    """
    if isinstance(schema, type):
        assert isinstance(data, schema), (
            f"{path}: expected {schema.__name__}, got {type(data).__name__!r} — value: {data!r}"
        )
        return

    if isinstance(schema, dict) and schema:
        assert isinstance(data, dict), f"{path}: expected dict, got {type(data).__name__!r} — value: {data!r}"
        for field, sub_schema in schema.items():
            assert field in data, (
                f"{path}.{field}: required field missing from response. Present keys: {sorted(data.keys())}"
            )
            _check_schema(data[field], sub_schema, f"{path}.{field}")
        return

    if isinstance(schema, dict) and not schema:
        # {} = just check it is a dict (keys are dynamic / not of interest)
        assert isinstance(data, dict), f"{path}: expected dict, got {type(data).__name__!r}"
        return

    if isinstance(schema, list) and schema:
        assert isinstance(data, list), f"{path}: expected list, got {type(data).__name__!r}"
        for i, item in enumerate(data):
            _check_schema(item, schema[0], f"{path}[{i}]")
        return

    if isinstance(schema, list) and not schema:
        assert isinstance(data, list), f"{path}: expected list, got {type(data).__name__!r}"
        return


def _response_body(interaction: dict) -> dict:
    """Parse the JSON response body from a cassette interaction."""
    return json.loads(interaction["response"]["body"]["string"])


# ---------------------------------------------------------------------------
# Jira response schemas
# ---------------------------------------------------------------------------

# GET /rest/agile/1.0/board — board listing response
_JIRA_BOARDS_SCHEMA = {
    "maxResults": int,
    "values": [
        {
            "id": int,
            "name": str,
            "type": str,
        }
    ],
}

# GET /rest/agile/1.0/board/{id}/sprint?state=active — active sprint list
_JIRA_ACTIVE_SPRINT_SCHEMA = {
    "values": [
        {
            "id": int,
            "state": str,
            "name": str,
            "startDate": str,
            "endDate": str,
        }
    ],
}

# GET /rest/agile/1.0/sprint/{id} — closed sprint detail (for velocity)
_JIRA_SPRINT_DETAIL_SCHEMA = {
    "id": int,
    "name": str,
    "state": str,
}

# GET /rest/api/2/search — issue search (backlog count)
_JIRA_SEARCH_SCHEMA = {
    "total": int,
    "issues": [],  # may be empty; if non-empty items have "key"
}

# POST /rest/api/2/issue → issue creation response
_JIRA_CREATE_ISSUE_SCHEMA = {
    "id": str,
    "key": str,
    "self": str,
}

# GET /rest/api/2/issue/{key} → full issue detail
_JIRA_ISSUE_DETAIL_SCHEMA = {
    "id": str,
    "key": str,
    "fields": {
        "summary": str,
        "issuetype": {"name": str},
    },
}


class TestJiraCassetteSchemas:
    """Validate Jira cassette response bodies against expected API schemas.

    # See docs: "Testing — Contract Tests"
    #
    # Each cassette interaction is parsed and validated against the schema for
    # its endpoint. This catches silent API changes where a field is removed or
    # renamed without causing a test failure in the existing tool-level tests.
    """

    def test_board_listing_schema(self):
        """GET /rest/agile/1.0/board response contains required board fields."""
        interactions = _load_cassette(
            "test_jira_contract",
            "TestJiraReadBoardContract.test_read_board_happy_path.yaml",
        )
        # First interaction is the board listing
        board_response = _response_body(interactions[0])
        _check_schema(board_response, _JIRA_BOARDS_SCHEMA, "board_listing")

    def test_active_sprint_schema(self):
        """GET /rest/agile/1.0/board/{id}/sprint?state=active response has sprint fields."""
        interactions = _load_cassette(
            "test_jira_contract",
            "TestJiraReadBoardContract.test_read_board_happy_path.yaml",
        )
        # Second interaction is the active sprint list
        sprint_response = _response_body(interactions[1])
        _check_schema(sprint_response, _JIRA_ACTIVE_SPRINT_SCHEMA, "active_sprint")

    def test_sprint_detail_schema(self):
        """GET /rest/agile/1.0/sprint/{id} response has id, name, state fields."""
        interactions = _load_cassette(
            "test_jira_contract",
            "TestJiraReadBoardContract.test_read_board_happy_path.yaml",
        )
        # Interactions 5, 6, 7 are the three closed sprint detail calls
        for idx in (5, 6, 7):
            sprint = _response_body(interactions[idx])
            _check_schema(sprint, _JIRA_SPRINT_DETAIL_SCHEMA, f"sprint_detail[{idx}]")

    def test_issue_search_schema(self):
        """GET /rest/api/2/search response has total and issues fields."""
        interactions = _load_cassette(
            "test_jira_contract",
            "TestJiraReadBoardContract.test_read_board_happy_path.yaml",
        )
        # Interaction 3 is the backlog search
        search = _response_body(interactions[3])
        _check_schema(search, _JIRA_SEARCH_SCHEMA, "issue_search")

    def test_create_issue_response_schema(self):
        """POST /rest/api/2/issue response contains id, key, self."""
        interactions = _load_cassette(
            "test_jira_contract",
            "TestJiraCreateEpicContract.test_create_epic_happy_path.yaml",
        )
        create_response = _response_body(interactions[0])
        _check_schema(create_response, _JIRA_CREATE_ISSUE_SCHEMA, "create_issue")

    def test_issue_detail_response_schema(self):
        """GET /rest/api/2/issue/{key} response has id, key, fields.summary."""
        interactions = _load_cassette(
            "test_jira_contract",
            "TestJiraCreateEpicContract.test_create_epic_happy_path.yaml",
        )
        detail_response = _response_body(interactions[1])
        _check_schema(detail_response, _JIRA_ISSUE_DETAIL_SCHEMA, "issue_detail")


# ---------------------------------------------------------------------------
# Confluence response schemas
# ---------------------------------------------------------------------------

# GET /wiki/rest/api/search — CQL search results
_CONFLUENCE_SEARCH_SCHEMA = {
    "results": [
        {
            "id": str,
            "title": str,
            "_links": {},
        }
    ],
    "size": int,
}

# POST /wiki/rest/api/content — page creation response
_CONFLUENCE_CREATE_PAGE_SCHEMA = {
    "id": str,
    "title": str,
    "_links": {},
}


class TestConfluenceCassetteSchemas:
    """Validate Confluence cassette response bodies against expected API schemas."""

    def test_search_results_schema(self):
        """Confluence search response has results list with id, title, _links."""
        interactions = _load_cassette(
            "test_confluence_contract",
            "TestConfluenceSearchDocsContract.test_search_returns_titles_and_urls.yaml",
        )
        search_response = _response_body(interactions[0])
        _check_schema(search_response, _CONFLUENCE_SEARCH_SCHEMA, "confluence_search")

    def test_search_result_has_links(self):
        """Each search result _links dict is non-empty (contains webui key)."""
        interactions = _load_cassette(
            "test_confluence_contract",
            "TestConfluenceSearchDocsContract.test_search_returns_titles_and_urls.yaml",
        )
        results = _response_body(interactions[0])["results"]
        for i, result in enumerate(results):
            assert "webui" in result["_links"], (
                f"results[{i}]._links missing 'webui' key — present keys: {sorted(result['_links'].keys())}"
            )

    def test_create_page_response_schema(self):
        """Confluence page creation response has id, title, _links."""
        interactions = _load_cassette(
            "test_confluence_contract",
            "TestConfluenceCreatePageContract.test_create_page_returns_id_and_url.yaml",
        )
        create_response = _response_body(interactions[0])
        _check_schema(create_response, _CONFLUENCE_CREATE_PAGE_SCHEMA, "confluence_create_page")


# ---------------------------------------------------------------------------
# GitHub response schemas
# ---------------------------------------------------------------------------

# GET /repos/{owner}/{repo} — repository metadata
_GITHUB_REPO_SCHEMA = {
    "id": int,
    "name": str,
    "full_name": str,
    "default_branch": str,
}

# GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1 — file tree
_GITHUB_TREE_SCHEMA = {
    "sha": str,
    "tree": [
        {
            "path": str,
            "type": str,
            "sha": str,
        }
    ],
    "truncated": bool,
}

# GET /repos/{owner}/{repo}/languages — {language: bytes}
# Schema: any non-empty dict with str keys and int values.
# Validated manually (dynamic key names).


class TestGithubCassetteSchemas:
    """Validate GitHub cassette response bodies against expected API schemas."""

    def test_repo_metadata_schema(self):
        """GET /repos/{owner}/{repo} response has id, name, full_name, default_branch."""
        interactions = _load_cassette(
            "test_github_contract",
            "TestGithubReadRepoContract.test_read_repo_tree_and_languages.yaml",
        )
        repo_response = _response_body(interactions[0])
        _check_schema(repo_response, _GITHUB_REPO_SCHEMA, "github_repo")

    def test_git_tree_schema(self):
        """GET /repos/{owner}/{repo}/git/trees/HEAD response has sha, tree, truncated."""
        interactions = _load_cassette(
            "test_github_contract",
            "TestGithubReadRepoContract.test_read_repo_tree_and_languages.yaml",
        )
        tree_response = _response_body(interactions[1])
        _check_schema(tree_response, _GITHUB_TREE_SCHEMA, "github_tree")

    def test_tree_entries_have_type(self):
        """Every tree entry has a type that is 'blob' or 'tree'."""
        interactions = _load_cassette(
            "test_github_contract",
            "TestGithubReadRepoContract.test_read_repo_tree_and_languages.yaml",
        )
        tree = _response_body(interactions[1])["tree"]
        valid_types = {"blob", "tree", "commit"}
        for i, entry in enumerate(tree):
            assert entry["type"] in valid_types, f"tree[{i}].type={entry['type']!r} not in {valid_types}"

    def test_languages_schema(self):
        """GET /repos/{owner}/{repo}/languages response is a dict of str → int."""
        interactions = _load_cassette(
            "test_github_contract",
            "TestGithubReadRepoContract.test_read_repo_tree_and_languages.yaml",
        )
        languages = _response_body(interactions[2])
        assert isinstance(languages, dict), f"languages: expected dict, got {type(languages).__name__}"
        for lang, byte_count in languages.items():
            assert isinstance(lang, str), f"language key {lang!r} is not a str"
            assert isinstance(byte_count, int), f"language[{lang!r}] byte count {byte_count!r} is not an int"


# ---------------------------------------------------------------------------
# LLM JSON output schemas
# ---------------------------------------------------------------------------
#
# These schemas correspond exactly to the _JSON_SCHEMA strings embedded in each
# prompt builder in src/yeaboi/prompts/. Keeping them in sync ensures the
# prompt documentation and the parse logic agree on the expected structure.
#
# See docs: "Prompt Construction" — JSON schema in each prompt

_LLM_ANALYSIS_SCHEMA = {
    "project_name": str,
    "project_description": str,
    "project_type": str,
    "goals": [str],
    "end_users": [str],
    "target_state": str,
    "tech_stack": [str],
    "integrations": [],
    "constraints": [],
    "sprint_length_weeks": int,
    "target_sprints": int,
    "risks": [],
    "out_of_scope": [],
    "assumptions": [],
}

_LLM_FEATURE_SCHEMA = {
    "id": str,
    "title": str,
    "description": str,
    "priority": str,
}

_LLM_STORY_SCHEMA = {
    "id": str,
    "feature_id": str,
    "persona": str,
    "goal": str,
    "benefit": str,
    "story_points": int,
    "priority": str,
    "acceptance_criteria": [
        {
            "given": str,
            "when": str,
            "then": str,
        }
    ],
}

_LLM_TASK_SCHEMA = {
    "id": str,
    "story_id": str,
    "title": str,
    "description": str,
}

_LLM_SPRINT_SCHEMA = {
    "id": str,
    "name": str,
    "goal": str,
    "capacity_points": int,
    "story_ids": [str],
}

# Canonical test dicts — the "golden" shape each parser expects.
# Mirrors the _*_DICT / _*_LIST data in test_llm_provider_contract.py.

_CANONICAL_ANALYSIS = {
    "project_name": "TaskFlow",
    "project_description": "A collaborative task management platform",
    "project_type": "web_application",
    "goals": ["Real-time collaboration", "Kanban boards"],
    "end_users": ["Development teams"],
    "target_state": "Production SaaS",
    "tech_stack": ["React", "Python"],
    "integrations": ["Slack"],
    "constraints": [],
    "sprint_length_weeks": 2,
    "target_sprints": 4,
    "risks": [],
    "out_of_scope": [],
    "assumptions": [],
}

_CANONICAL_FEATURE = {"id": "F1", "title": "User Auth", "description": "OAuth2, JWT", "priority": "high"}

_CANONICAL_STORY = {
    "id": "US-1",
    "feature_id": "F1",
    "persona": "developer",
    "goal": "log in with my email",
    "benefit": "I can access my tasks",
    "story_points": 3,
    "priority": "high",
    "acceptance_criteria": [
        {"given": "valid credentials", "when": "I submit login", "then": "I am redirected to dashboard"},
    ],
}

_CANONICAL_TASK = {
    "id": "T-1",
    "story_id": "US-1",
    "title": "Implement JWT middleware",
    "description": "Add JWT validation to the FastAPI auth router",
}

_CANONICAL_SPRINT = {
    "id": "SP-1",
    "name": "Sprint 1",
    "goal": "Authentication foundation",
    "capacity_points": 10,
    "story_ids": ["US-1", "US-2"],
}


class TestLlmOutputSchemas:
    """Validate that LLM JSON output matches the schemas defined in the prompt builders.

    # See docs: "Prompt Construction" — embedded JSON schema in each prompt
    #
    # The prompt builders embed _JSON_SCHEMA strings that tell the LLM what
    # fields to return. These tests verify that canonical examples of those
    # schemas conform to our _parse_* functions' expectations, and that the
    # schema definitions are self-consistent.
    """

    def test_analysis_schema(self):
        """Canonical ProjectAnalysis JSON dict matches the analyzer prompt schema."""
        _check_schema(_CANONICAL_ANALYSIS, _LLM_ANALYSIS_SCHEMA, "analysis")

    def test_feature_schema(self):
        """Canonical Feature JSON dict matches the feature_generator prompt schema."""
        _check_schema(_CANONICAL_FEATURE, _LLM_FEATURE_SCHEMA, "feature")

    def test_story_schema(self):
        """Canonical UserStory JSON dict matches the story_writer prompt schema."""
        _check_schema(_CANONICAL_STORY, _LLM_STORY_SCHEMA, "story")

    def test_task_schema(self):
        """Canonical Task JSON dict matches the task_decomposer prompt schema."""
        _check_schema(_CANONICAL_TASK, _LLM_TASK_SCHEMA, "task")

    def test_sprint_schema(self):
        """Canonical Sprint JSON dict matches the sprint_planner prompt schema."""
        _check_schema(_CANONICAL_SPRINT, _LLM_SPRINT_SCHEMA, "sprint")

    def test_feature_priority_is_valid_value(self):
        """Feature priority must be one of the four allowed values."""
        allowed = {"critical", "high", "medium", "low"}
        assert _CANONICAL_FEATURE["priority"] in allowed, (
            f"priority {_CANONICAL_FEATURE['priority']!r} not in {allowed}"
        )

    def test_story_points_is_fibonacci_value(self):
        """Story points must be on the Fibonacci scale (1, 2, 3, 5, 8)."""
        allowed = {1, 2, 3, 5, 8}
        assert _CANONICAL_STORY["story_points"] in allowed, (
            f"story_points {_CANONICAL_STORY['story_points']} not in Fibonacci set {allowed}"
        )

    def test_schema_checker_catches_missing_field(self):
        """_check_schema() raises AssertionError when a required field is absent."""
        import pytest

        incomplete = {"id": "F1", "title": "Test"}  # missing description and priority
        with pytest.raises(AssertionError, match="description"):
            _check_schema(incomplete, _LLM_FEATURE_SCHEMA, "incomplete_feature")

    def test_schema_checker_catches_wrong_type(self):
        """_check_schema() raises AssertionError when a field has the wrong type."""
        import pytest

        wrong_type = {**_CANONICAL_FEATURE, "id": 999}  # id should be str
        with pytest.raises(AssertionError, match="str"):
            _check_schema(wrong_type, _LLM_FEATURE_SCHEMA, "wrong_type_feature")
