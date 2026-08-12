"""Tests for the chat transcript exporter (src/yeaboi/transcript.py)."""

from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

from yeaboi.transcript import build_chat_transcript_markdown, export_chat_transcript


def _state(**overrides) -> dict:
    state = {
        "messages": [
            HumanMessage(content="build a dog-walking app"),
            AIMessage(content="What is your team size?"),
            HumanMessage(content="4 engineers"),
        ],
        "_chat_preamble": [
            {"role": "ai", "text": "Hey — tell me about your project."},
            {"role": "user", "text": "small"},
        ],
    }
    state.update(overrides)
    return state


class TestMarkdown:
    def test_roles_and_order(self):
        md = build_chat_transcript_markdown(_state())
        assert md.index("Hey — tell me") < md.index("build a dog-walking app")
        assert "### You" in md
        assert "### yeaboi" in md
        assert "What is your team size?" in md

    def test_header_counts_messages(self):
        md = build_chat_transcript_markdown(_state())
        assert "_5 messages_" in md

    def test_dict_form_messages_render_identically(self):
        state = _state()
        as_dicts = dict(state, messages=messages_to_dict(state["messages"]))
        assert build_chat_transcript_markdown(as_dicts) == build_chat_transcript_markdown(state)

    def test_tool_calls_collapse_and_payloads_never_dump(self):
        secret_payload = "SECRET FILE CONTENTS"
        state = _state(
            messages=[
                HumanMessage(content="sync it"),
                AIMessage(content="", tool_calls=[{"name": "jira_create", "args": {"x": 1}, "id": "t1"}]),
                ToolMessage(content=secret_payload, tool_call_id="t1"),
                AIMessage(content="Done — synced."),
            ]
        )
        md = build_chat_transcript_markdown(state)
        assert "_(used tool: jira_create)_" in md
        assert secret_payload not in md
        assert "Done — synced." in md

    def test_artifact_markers_not_bodies(self):
        state = _state(sprints=["sp"], stories=["s"])
        md = build_chat_transcript_markdown(state)
        assert "- Sprint plan generated" in md
        assert "- Stories generated" in md

    def test_attachments_section(self):
        state = _state(pasted_images=["/tmp/att/img-abc.png"], chat_images=["/tmp/att/img-def.png"])
        md = build_chat_transcript_markdown(state)
        assert "## Attachments" in md
        assert "img-abc.png" in md
        assert "img-def.png" in md

    def test_project_name_in_title(self):
        state = _state(project_analysis=SimpleNamespace(project_name="Dog Walker"))
        assert "# Chat Transcript — Dog Walker" in build_chat_transcript_markdown(state)


class TestRedaction:
    def test_pasted_secret_is_masked(self, monkeypatch):
        secret = "ghp_" + "a1b2c3d4e5f6" * 3
        monkeypatch.setenv("GITHUB_TOKEN", secret)
        # redaction caches its regex per secret set — reset it for this env.
        import yeaboi.redaction as redaction

        monkeypatch.setattr(redaction, "_cached", None, raising=False)
        state = _state(messages=[HumanMessage(content=f"our token is {secret}")])
        md = build_chat_transcript_markdown(state)
        assert secret not in md

    def test_anon_replacements_applied(self):
        anon = SimpleNamespace(replacements={"Dog Walker": "Project A"})

        def fake_mask(lines, replacements):
            return [line.replace("Dog Walker", "Project A") for line in lines]

        state = _state(messages=[HumanMessage(content="the Dog Walker app")])
        with patch("yeaboi.anonymize.apply.mask_lines", side_effect=fake_mask):
            md = build_chat_transcript_markdown(state, anon=anon)
        assert "Project A" in md
        assert "Dog Walker" not in md


class TestExport:
    def test_writes_file(self, tmp_path, monkeypatch):
        from yeaboi import fs_policy

        monkeypatch.setattr(fs_policy, "resolve_and_check", lambda path, **kw: path)
        out = export_chat_transcript(_state(), tmp_path / "scrum-chat.md")
        assert out.exists()
        assert "### You" in out.read_text()
