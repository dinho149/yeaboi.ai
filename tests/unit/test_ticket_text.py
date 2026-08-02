"""Unit tests for tracker long-form text flattening.

The clipping tests carry the weight: this text exists so the standup can tell
whether a change belongs to a ticket, and a definition of done is conventionally
written LAST — so a budget that trimmed the joined string would silently delete
exactly the section the feature was built to read.
"""

from yeaboi.ticket_text import (
    MAX_SECTION_CHARS,
    MAX_TICKET_TEXT_CHARS,
    jira_wiki_to_text,
    strip_html,
    ticket_text,
)


class TestStripHtml:
    def test_block_boundaries_become_newlines(self):
        assert strip_html("<p>One</p><p>Two</p>") == "One\nTwo"

    def test_list_items_stay_on_separate_lines(self):
        # An acceptance list collapsed onto one line reads as a single sentence
        # to the matcher, which is how a bullet list stops being evidence.
        out = strip_html("<ul><li>Retry twice</li><li>Log the failure</li></ul>")
        assert out.splitlines() == ["Retry twice", "Log the failure"]

    def test_entities_are_unescaped(self):
        assert strip_html("<div>a &amp; b</div>") == "a & b"


class TestJiraWiki:
    def test_headings_and_bullets_flatten(self):
        assert jira_wiki_to_text("h2. Goal\n* First\n* Second") == "Goal\n- First\n- Second"

    def test_account_ids_never_surface(self):
        assert "accountid" not in jira_wiki_to_text("[~accountid:5f2a1b] please review")

    def test_embedded_adf_is_flattened(self):
        blob = (
            '{adf:display=block}{"type":"doc","content":[{"type":"paragraph","content":'
            '[{"type":"text","text":"Rename the plugins"}]}]}{adf}'
        )
        assert "Rename the plugins" in jira_wiki_to_text(blob)
        assert '"type"' not in jira_wiki_to_text(blob)


class TestTicketText:
    def test_sections_are_joined_in_order(self):
        out = ticket_text("Description here", "AC one", "DoD one", flatten=lambda t: t)
        assert out == "Description here\n\nAC one\n\nDoD one"

    def test_empty_sections_are_dropped(self):
        assert ticket_text("Only this", "", "", flatten=lambda t: t) == "Only this"

    def test_a_long_description_never_evicts_the_definition_of_done(self):
        # The whole reason clipping is per section: DoD is written last, and it
        # is the section that says documentation is part of this ticket.
        out = ticket_text("d" * 5000, "", "Definition of done: documentation", flatten=lambda t: t)
        assert "Definition of done: documentation" in out
        assert len(out.split("\n\n")[0]) <= MAX_SECTION_CHARS + 1  # +1 for the ellipsis

    def test_joined_output_respects_the_hard_cap(self):
        out = ticket_text(*["x" * 2000] * 3, flatten=lambda t: t)
        assert len(out) <= MAX_TICKET_TEXT_CHARS + 1

    def test_non_string_payload_is_coerced_not_raised(self):
        # A checklist-typed acceptance field comes back as a list; collection
        # must not die over one ticket with an exotic custom field.
        assert "retry" in ticket_text("", ["retry", "backoff"], "", flatten=lambda t: t)

    def test_a_raising_flattener_degrades_to_the_raw_text(self):
        def boom(_text):
            raise ValueError("bad markup")

        assert ticket_text("plain words", flatten=boom) == "plain words"
