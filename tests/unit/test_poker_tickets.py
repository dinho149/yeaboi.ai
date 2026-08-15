"""Tests for the Poker tracker facade (poker/tickets.py).

All tracker SDK calls are monkeypatched at the tools-module seam, so these
tests exercise source selection, normalization, and error degradation without
any network access — the same seam pattern as the standup collector tests.
"""

import json

import pytest

from yeaboi.poker import tickets


class TestAvailableSources:
    def test_both_configured_jira_first(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: "https://x.atlassian.net")
        monkeypatch.setattr("yeaboi.config.get_jira_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "https://dev.azure.com/org")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "pat")
        assert tickets.available_sources() == ["jira", "azdevops"]

    def test_only_azdo(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_jira_token", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "https://dev.azure.com/org")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "pat")
        assert tickets.available_sources() == ["azdevops"]

    def test_none_configured(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_jira_token", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: None)
        assert tickets.available_sources() == []

    def test_partial_creds_do_not_count(self, monkeypatch):
        # A url without a token (or vice versa) is not a usable source.
        monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: "https://x.atlassian.net")
        monkeypatch.setattr("yeaboi.config.get_jira_token", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "pat")
        assert tickets.available_sources() == []


class TestSourceLabel:
    def test_known_labels(self):
        assert tickets.source_label("jira") == "Jira"
        assert tickets.source_label("azdevops") == "Azure DevOps"
        assert tickets.source_label("demo") == "Demo"

    def test_unknown_passthrough(self):
        assert tickets.source_label("weird") == "weird"
        assert tickets.source_label("") == "?"


class TestStripHtml:
    def test_tags_removed_and_breaks_kept(self):
        html_in = "<div>Line one<br>Line two</div><p>Para</p>"
        assert tickets._strip_html(html_in) == "Line one\nLine two\nPara"

    def test_entities_unescaped(self):
        assert tickets._strip_html("<div>a &amp; b &lt;c&gt;</div>") == "a & b <c>"

    def test_plain_text_untouched(self):
        assert tickets._strip_html("just text") == "just text"


class TestJiraWikiToText:
    def test_plain_text_untouched(self):
        assert tickets._jira_wiki_to_text("Just a plain description.\nSecond line.") == (
            "Just a plain description.\nSecond line."
        )

    def test_color_and_emphasis_markup_stripped(self):
        raw = "{color:#bf2600}+*We will improve security*+{color}"
        assert tickets._jira_wiki_to_text(raw) == "We will improve security"

    def test_account_id_mentions_never_surface(self):
        out = tickets._jira_wiki_to_text("Owner: [~accountid:712020:0820c2eb-975e-461a] please review")
        assert out == "Owner: @user please review"
        assert "712020" not in out

    def test_named_mentions_links_and_breaks(self):
        raw = "[~jsmith] see [the RFC|https://example.com/rfc]\\\\next line"
        assert tickets._jira_wiki_to_text(raw) == "@jsmith see the RFC\nnext line"

    def test_headings_lists_and_rules(self):
        raw = "h2. Goals\n----\n* first\n* second\n# ordered\nbq. quoted"
        assert tickets._jira_wiki_to_text(raw) == "Goals\n\n- first\n- second\n- ordered\n> quoted"

    def test_table_pipes_become_separators(self):
        raw = "||Col A||Col B||\n|one|two|"
        out = tickets._jira_wiki_to_text(raw)
        assert out == "Col A | Col B\none | two"

    def test_adf_block_flattened_to_readable_text(self):
        # The real-world shape: an {adf} macro whose body is editor JSON with
        # nested expands, bullet lists, an ordered AC list, and a mention.
        doc = {
            "type": "nestedExpand",
            "attrs": {"localId": "f5a1", "title": "Notes"},
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Set up automation for risks"}],
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "orderedList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "AC #1: SLA agreed with "},
                                        {"type": "mention", "attrs": {"id": "63b6c46b"}},
                                    ],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        raw = "Intro line\n{adf:display=block}\n" + json.dumps(doc) + "\n{adf}\nOutro"
        out = tickets._jira_wiki_to_text(raw)
        assert "Notes" in out
        assert "- Set up automation for risks" in out
        assert "- AC #1: SLA agreed with @user" in out
        assert "63b6c46b" not in out  # mention ids never surface
        assert '"type"' not in out  # no raw JSON leaks through
        assert out.startswith("Intro line")
        assert out.endswith("Outro")

    def test_unparseable_adf_block_dropped(self):
        raw = "Before\n{adf:display=block}{not json at all{adf}\nAfter"
        out = tickets._jira_wiki_to_text(raw)
        assert '"type"' not in out
        assert "not json" not in out
        assert "Before" in out
        assert "After" in out

    def test_truncated_adf_block_salvages_text_values(self):
        # A cut-off blob isn't valid JSON, but its "text" values are still the
        # human-readable content — salvage them rather than dropping the ACs.
        blob = '{"type":"paragraph","content":[{"type":"text","text":"AC #1: agreed \\"SLA\\""},{"ty'
        raw = "{adf:display=block}" + blob + "{adf}"
        out = tickets._jira_wiki_to_text(raw)
        assert out == 'AC #1: agreed "SLA"'

    def test_monospace_and_snake_case_survive(self):
        # {{code}} is unwrapped; snake_case identifiers must not be eaten by
        # the _italic_ rule.
        assert tickets._jira_wiki_to_text("Use {{user_id}} and created_at") == "Use user_id and created_at"


class TestExtractAcceptance:
    def test_heading_section(self):
        text = "Some intro\nAcceptance Criteria\n- works offline\n- syncs on reconnect\nNotes:\nunrelated"
        out = tickets._extract_acceptance_section(text)
        assert out == "- works offline\n- syncs on reconnect"

    def test_inline_form_with_bullets(self):
        text = "Overview here\nAcceptance criteria: must load in under 2s\n- includes mobile\nSomething else"
        out = tickets._extract_acceptance_section(text)
        assert out == "must load in under 2s\n- includes mobile"

    def test_ac_numbered_lines(self):
        text = "Intro\nAC #1: SLA agreed\nAC #2: reporting set up\nOutro"
        out = tickets._extract_acceptance_section(text)
        assert out == "AC #1: SLA agreed\nAC #2: reporting set up"

    def test_single_ac_line_not_enough(self):
        assert tickets._extract_acceptance_section("Intro\nAC1: only one line\nOutro") == ""

    def test_no_match(self):
        assert tickets._extract_acceptance_section("Just a plain description.") == ""


class TestAcceptanceText:
    def test_azdo_acceptance_html_stripped(self):
        row = {"source": "azdevops", "description": "", "acceptance": "<div>Given a user<br>Then it works</div>"}
        out = tickets._with_description_text(row)
        assert out["acceptance_text"] == "Given a user\nThen it works"

    def test_jira_acceptance_wiki_flattened(self):
        row = {"source": "jira", "description": "", "acceptance": "*AC1:* works {color:red}fast{color}"}
        out = tickets._with_description_text(row)
        assert out["acceptance_text"] == "AC1: works fast"

    def test_non_string_acceptance_coerced_not_fatal(self):
        # A rich-text AC custom field can return a non-string payload — it must
        # degrade to its string form, never raise and empty the whole fetch.
        row = {"source": "jira", "description": "", "acceptance": {"weird": "payload"}}
        out = tickets._with_description_text(row)
        assert isinstance(out["acceptance_text"], str)

    def test_fallback_from_description_only_when_field_empty(self):
        row = {"source": "jira", "description": "Intro\nAC #1: a\nAC #2: b", "acceptance": ""}
        out = tickets._with_description_text(row)
        assert out["acceptance_text"] == "AC #1: a\nAC #2: b"
        # Description itself is untouched (lossless copy).
        assert "AC #1: a" in out["description_text"]
        # A real field value wins over the description fallback.
        row2 = {"source": "jira", "description": "Intro\nAC #1: a\nAC #2: b", "acceptance": "the real ACs"}
        assert tickets._with_description_text(row2)["acceptance_text"] == "the real ACs"


class TestTicketTypes:
    def test_defaults_per_source(self):
        assert tickets.default_include_types("jira") == ("story", "bug", "task")
        assert tickets.default_include_types("azdevops") == ("story", "bug")
        assert tickets.default_include_types("demo") == ("story", "bug", "task")

    def test_labels_cover_all_types(self):
        assert set(tickets.TICKET_TYPE_LABELS) == set(tickets.TICKET_TYPES)


class TestPlainTextToAzdoHtml:
    def test_escapes_and_converts_newlines(self):
        out = tickets._plain_text_to_azdo_html("a <b>\nnext & last")
        assert out == "<div>a &lt;b&gt;<br>next &amp; last</div>"


class TestListSprints:
    def test_jira(self, monkeypatch):
        rows = [{"id": 1, "name": "S1"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_list_sprints", lambda *a, **k: rows, raising=False)
        assert tickets.list_sprints("jira") == rows

    def test_azdevops(self, monkeypatch):
        rows = [{"id": "guid", "name": "It1"}]
        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_list_sprints", lambda *a, **k: rows, raising=False)
        assert tickets.list_sprints("azdevops") == rows

    def test_unknown_source_empty(self):
        assert tickets.list_sprints("demo") == []
        assert tickets.list_sprints("") == []

    def test_error_degrades_to_empty(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.tools.jira.jira_list_sprints", _boom, raising=False)
        assert tickets.list_sprints("jira") == []


class TestFetchTickets:
    def _jira_row(self, key="PROJ-1"):
        return {
            "source": "jira",
            "key": key,
            "summary": "S",
            "description": "wiki *text*",
            "story_points": None,
            "state": "To Do",
            "assignee": "",
            "url": "",
        }

    def _azdo_row(self, key="101"):
        return {
            "source": "azdevops",
            "key": key,
            "summary": "S",
            "description": "<div>Line<br>two</div>",
            "story_points": 3.0,
            "state": "New",
            "assignee": "",
            "url": "",
        }

    def test_jira_sprint_uses_sprint_id(self, monkeypatch):
        seen = {}

        def _sprint_issues(sprint_id, **kwargs):
            seen["sprint_id"] = sprint_id
            return [self._jira_row()]

        monkeypatch.setattr("yeaboi.tools.jira.jira_sprint_issues", _sprint_issues, raising=False)
        out = tickets.fetch_tickets("jira", sprint={"id": 42, "name": "S1"})
        assert seen["sprint_id"] == 42
        # Jira wiki-markup is flattened for display; the raw payload is kept.
        assert out[0]["description"] == "wiki *text*"
        assert out[0]["description_text"] == "wiki text"

    def test_jira_sprint_without_id_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.jira.jira_sprint_issues",
            lambda *a, **k: pytest.fail("must not be called without a sprint id"),
            raising=False,
        )
        assert tickets.fetch_tickets("jira", sprint={"name": "S1"}) == []

    def test_jira_backlog(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira.jira_backlog_issues", lambda **k: [self._jira_row()], raising=False)
        out = tickets.fetch_tickets("jira")
        assert [r["key"] for r in out] == ["PROJ-1"]

    def test_azdo_sprint_strips_html_for_display(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_sprint_issues",
            lambda iteration_id, **k: [self._azdo_row()],
            raising=False,
        )
        out = tickets.fetch_tickets("azdevops", sprint={"id": "guid", "name": "It1"})
        assert out[0]["description"] == "<div>Line<br>two</div>"  # raw kept
        assert out[0]["description_text"] == "Line\ntwo"

    def test_azdo_backlog(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_backlog_issues", lambda **k: [self._azdo_row()], raising=False
        )
        out = tickets.fetch_tickets("azdevops")
        assert [r["key"] for r in out] == ["101"]

    def test_include_types_forwarded_with_default(self, monkeypatch):
        seen = {}

        def _backlog(**kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr("yeaboi.tools.jira.jira_backlog_issues", _backlog, raising=False)
        tickets.fetch_tickets("jira")
        assert seen["include_types"] == ("story", "bug", "task")  # jira default
        tickets.fetch_tickets("jira", include_types=("story",))
        assert seen["include_types"] == ("story",)

    def test_azdo_default_excludes_tasks(self, monkeypatch):
        seen = {}

        def _backlog(**kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_backlog_issues", _backlog, raising=False)
        tickets.fetch_tickets("azdevops")
        assert seen["include_types"] == ("story", "bug")

    def test_demo_source(self):
        out = tickets.fetch_tickets("demo")
        assert len(out) == 6
        assert all(r["source"] == "demo" for r in out)
        assert all("description_text" in r for r in out)
        assert all("acceptance_text" in r for r in out)
        # DEMO-1 carries ACs so dry-run sessions exercise the display.
        assert "AC1:" in out[0]["acceptance_text"]

    def test_limit_applied(self):
        assert len(tickets.fetch_tickets("demo", limit=2)) == 2

    def test_error_degrades_to_empty(self, monkeypatch):
        def _boom(**k):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.tools.jira.jira_backlog_issues", _boom, raising=False)
        assert tickets.fetch_tickets("jira") == []


class TestUpdateTicket:
    def test_demo_is_noop_success(self):
        ok, err = tickets.update_ticket("demo", {"key": "DEMO-1"}, story_points=5)
        assert (ok, err) == (True, "")

    def test_jira_passes_through_plain_description(self, monkeypatch):
        seen = {}

        def _update(key, **kwargs):
            seen["key"] = key
            seen.update(kwargs)
            return True, ""

        monkeypatch.setattr("yeaboi.tools.jira.jira_update_issue_fields", _update, raising=False)
        ok, _ = tickets.update_ticket("jira", {"key": "PROJ-1"}, description="plain text", story_points=8)
        assert ok is True
        assert seen == {
            "key": "PROJ-1",
            "summary": None,
            "description": "plain text",
            "story_points": 8,
            "issue_type": None,
            "assignee": None,
            "acceptance": None,
        }

    def test_azdo_converts_description_to_html(self, monkeypatch):
        seen = {}

        def _update(wi_id, **kwargs):
            seen["id"] = wi_id
            seen.update(kwargs)
            return True, ""

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_update_work_item_fields", _update, raising=False)
        ok, _ = tickets.update_ticket("azdevops", {"key": "101"}, description="a & b\nnext")
        assert ok is True
        assert seen["id"] == 101
        assert seen["description"] == "<div>a &amp; b<br>next</div>"

    def test_azdo_untouched_description_not_rewritten(self, monkeypatch):
        seen = {}

        def _update(wi_id, **kwargs):
            seen.update(kwargs)
            return True, ""

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_update_work_item_fields", _update, raising=False)
        tickets.update_ticket("azdevops", {"key": "101"}, story_points=5)
        assert seen["description"] is None

    def test_unknown_source_fails(self):
        ok, err = tickets.update_ticket("weird", {"key": "X-1"}, story_points=5)
        assert ok is False
        assert "unknown" in err.lower()

    def test_error_degrades_to_tuple(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.tools.jira.jira_update_issue_fields", _boom, raising=False)
        ok, err = tickets.update_ticket("jira", {"key": "PROJ-1"}, story_points=5)
        assert ok is False
        assert "network down" in err


class TestTicketOptions:
    def test_demo_has_nothing_to_ask(self):
        assert tickets.ticket_options("demo", {"key": "DEMO-1"}) == {}

    def test_jira_passes_the_key_through(self, monkeypatch):
        seen = {}

        def _options(key):
            seen["key"] = key
            return {"states": ["To Do", "Done"]}

        monkeypatch.setattr("yeaboi.tools.jira.jira_ticket_options", _options, raising=False)
        assert tickets.ticket_options("jira", {"key": "PROJ-1"}) == {"states": ["To Do", "Done"]}
        assert seen["key"] == "PROJ-1"

    def test_azdo_passes_the_numeric_id(self, monkeypatch):
        seen = {}

        def _options(work_item_id):
            seen["id"] = work_item_id
            return {"assignees": ["Ada"]}

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_work_item_options", _options, raising=False)
        assert tickets.ticket_options("azdevops", {"key": "101"}) == {"assignees": ["Ada"]}
        assert seen["id"] == 101

    def test_unknown_source_asks_nothing(self):
        assert tickets.ticket_options("weird", {"key": "X-1"}) == {}

    def test_error_degrades_to_empty(self, monkeypatch):
        def _boom(key):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.tools.jira.jira_ticket_options", _boom, raising=False)
        assert tickets.ticket_options("jira", {"key": "PROJ-1"}) == {}


class TestDemoTickets:
    def test_shape_matches_normalized_rows(self):
        rows = tickets.demo_tickets()
        assert len(rows) == 6
        required = {
            "source",
            "key",
            "summary",
            "description",
            "description_text",
            "story_points",
            "state",
            "assignee",
            "url",
        }
        for row in rows:
            assert required.issubset(row.keys())
            assert row["source"] == "demo"
        # A mix of estimated and unestimated tickets so the prefill logic is exercisable.
        assert any(r["story_points"] is None for r in rows)
        assert any(r["story_points"] is not None for r in rows)
