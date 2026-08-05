"""Tests for the slash-command registry — dispatch, availability, bypass."""

from unittest.mock import MagicMock, patch

from yeaboi.ui.session.chat._commands import COMMANDS, ChatContext, dispatch, matching_commands


def _ctx(**overrides) -> ChatContext:
    defaults = dict(
        state=lambda: {},
        run_turn=MagicMock(),
        add_system=MagicMock(),
        add_artifact=MagicMock(),
        insert_text=MagicMock(return_value=True),
        trigger_voice=MagicMock(),
        trigger_image=MagicMock(),
        export=MagicMock(),
        switch_size=MagicMock(),
        edit_question=MagicMock(),
        request_quit=MagicMock(),
        intake_active=lambda: True,
        questionnaire_exists=lambda: True,
        enter_form=MagicMock(),
        fast_forward=MagicMock(),
        plan_complete=lambda: False,
    )
    defaults.update(overrides)
    return ChatContext(**defaults)


class TestDispatch:
    def test_non_slash_is_not_consumed(self):
        assert dispatch(_ctx(), "hello there") is False

    def test_unknown_command_consumed_with_notice_and_no_graph_turn(self):
        ctx = _ctx()
        assert dispatch(ctx, "/frobnicate") is True
        ctx.add_system.assert_called_once()
        assert "/help" in ctx.add_system.call_args[0][0]
        ctx.run_turn.assert_not_called()

    def test_slash_input_never_hits_guardrails(self):
        # Commands dispatch locally; validate_chat_input must never run.
        ctx = _ctx()
        with patch("yeaboi.input_guardrails.validate_chat_input", side_effect=AssertionError("guardrail ran")):
            dispatch(ctx, "/help")
            dispatch(ctx, "/unknown-thing")

    def test_skip_and_defaults_submit_intake_literals(self):
        ctx = _ctx()
        dispatch(ctx, "/skip")
        ctx.run_turn.assert_called_with("skip")
        dispatch(ctx, "/defaults")
        ctx.run_turn.assert_called_with("defaults")

    def test_skip_unavailable_outside_intake(self):
        ctx = _ctx(intake_active=lambda: False)
        dispatch(ctx, "/skip")
        ctx.run_turn.assert_not_called()
        ctx.add_system.assert_called_once()  # "unknown command" notice

    def test_export_scopes(self):
        ctx = _ctx()
        dispatch(ctx, "/export")
        ctx.export.assert_called_with("")
        dispatch(ctx, "/export transcript")
        ctx.export.assert_called_with("transcript")
        dispatch(ctx, "/export plan")
        ctx.export.assert_called_with("plan")

    def test_export_bad_scope_notices(self):
        ctx = _ctx()
        dispatch(ctx, "/export everything")
        ctx.export.assert_not_called()
        assert "scope" in ctx.add_system.call_args[0][0]

    def test_edit_with_number_and_without(self):
        ctx = _ctx()
        dispatch(ctx, "/edit 6")
        ctx.edit_question.assert_called_with(6)
        dispatch(ctx, "/edit")
        ctx.edit_question.assert_called_with(None)

    def test_size_switch_commands(self):
        ctx = _ctx()
        dispatch(ctx, "/small")
        ctx.switch_size.assert_called_with("small_project")
        dispatch(ctx, "/large")
        ctx.switch_size.assert_called_with("smart")

    def test_image_voice_quit(self):
        ctx = _ctx()
        dispatch(ctx, "/image")
        ctx.trigger_image.assert_called_once()
        dispatch(ctx, "/voice")
        ctx.trigger_voice.assert_called_once()
        dispatch(ctx, "/quit")
        ctx.request_quit.assert_called_once()

    def test_summary_pushes_card(self):
        ctx = _ctx()
        dispatch(ctx, "/summary")
        ctx.add_artifact.assert_called_with("intake_summary")

    def test_paste_inserts_clipboard_text(self):
        ctx = _ctx()
        with patch("yeaboi.clipboard.read_clipboard_text", return_value="line1\nline2"):
            dispatch(ctx, "/paste")
        ctx.insert_text.assert_called_with("line1\nline2")

    def test_paste_empty_clipboard_notices(self):
        ctx = _ctx()
        with patch("yeaboi.clipboard.read_clipboard_text", return_value=""):
            dispatch(ctx, "/paste")
        ctx.insert_text.assert_not_called()
        assert "Clipboard" in ctx.add_system.call_args[0][0]


class TestHelp:
    def test_help_lists_available_commands_only(self):
        ctx = _ctx(intake_active=lambda: False)
        dispatch(ctx, "/help")
        text = ctx.add_system.call_args[0][0]
        assert "/export" in text
        assert "/skip" not in text  # unavailable outside intake
        assert "Shortcuts:" in text


class TestMenu:
    def test_prefix_filtering(self):
        names = [c.name for c in matching_commands(_ctx(), "/e")]
        assert names == ["export", "edit"]

    def test_bare_slash_lists_everything_available(self):
        assert len(matching_commands(_ctx(), "/")) == len(COMMANDS)


class TestExactCommand:
    def test_exact_name_matches(self):
        from yeaboi.ui.session.chat._commands import exact_command

        command = exact_command(_ctx(), "/small")
        assert command is not None
        assert command.name == "small"

    def test_prefix_does_not_match(self):
        from yeaboi.ui.session.chat._commands import exact_command

        assert exact_command(_ctx(), "/sma") is None

    def test_prose_path_does_not_match(self):
        from yeaboi.ui.session.chat._commands import exact_command

        assert exact_command(_ctx(), "/usr/bin") is None

    def test_unavailable_command_is_none(self):
        from yeaboi.ui.session.chat._commands import exact_command

        assert exact_command(_ctx(intake_active=lambda: False), "/skip") is None


class TestFormCommand:
    def test_form_dispatches_to_enter_form(self):
        ctx = _ctx()
        dispatch(ctx, "/form")
        ctx.enter_form.assert_called_once()

    def test_form_available_pre_questionnaire(self):
        # The greeting-time /form defers instead of bouncing off "Unknown".
        ctx = _ctx(intake_active=lambda: False, questionnaire_exists=lambda: False)
        dispatch(ctx, "/form")
        ctx.enter_form.assert_called_once()

    def test_form_unavailable_after_intake(self):
        # Questionnaire done (exists but intake no longer active) — /form is moot.
        ctx = _ctx(intake_active=lambda: False, questionnaire_exists=lambda: True)
        dispatch(ctx, "/form")
        ctx.enter_form.assert_not_called()
        assert "Unknown command" in ctx.add_system.call_args[0][0]


class TestFinishCommand:
    def test_finish_dispatches_to_fast_forward(self):
        ctx = _ctx()
        dispatch(ctx, "/finish")
        ctx.fast_forward.assert_called_once()

    def test_finish_available_pre_questionnaire(self):
        # The greeting advertises /finish, so it must dispatch there — the
        # driver defers until the description exists.
        ctx = _ctx(intake_active=lambda: False, questionnaire_exists=lambda: False)
        dispatch(ctx, "/finish")
        ctx.fast_forward.assert_called_once()

    def test_finish_unavailable_when_plan_complete(self):
        ctx = _ctx(plan_complete=lambda: True)
        dispatch(ctx, "/finish")
        ctx.fast_forward.assert_not_called()

    def test_finish_available_at_review_stages(self):
        # Mid-pipeline: questionnaire exists, intake inactive, no sprints yet.
        ctx = _ctx(intake_active=lambda: False, questionnaire_exists=lambda: True)
        dispatch(ctx, "/finish")
        ctx.fast_forward.assert_called_once()
