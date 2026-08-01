"""Rendering reader-added notes and fields, for every mode that carries them.

One helper per output, rather than the same loop copied into seven exporters.
The rule this exists to enforce is simple and easy to get wrong: **an annotation
that is stored but not rendered is worse than one that was never accepted.** The
person who wrote it believes they corrected the document; nobody who reads the
document ever learns otherwise.

So anything that grows an ``annotations`` field grows a call to both of these at
the same time, and ``test_artifacts_render.py`` fails when an artifact carries
annotations that its exporters silently drop.
"""

from __future__ import annotations

from collections.abc import Sequence

from yeaboi.agent.state import Annotation

NOTES_HEADING = "Added by the team"
"""Section title. Says who put it there without claiming it was verified — the
attribution on these is self-declared, and the heading should not imply more."""


def annotations_payload(annotations: Sequence[Annotation]) -> list[dict]:
    """Return the wire form of a document's annotations, or ``[]``.

    Text and a discriminator, never presentation: a note and a field differ by
    ``kind``, and what that looks like is the bundle's business.

    Callers omit the key entirely when this returns empty, matching
    ``web.brand.build_chrome`` — an absent key and an empty list mean the same
    thing to a reader, and omitting keeps every existing export byte-identical.
    """
    return [
        {
            "kind": a.kind,
            "anchor": a.anchor,
            "label": a.label,
            "text": a.text,
            "author": a.author,
            "avatar": a.avatar,
            "at": a.at,
        }
        for a in annotations
        if a.text
    ]


def annotations_markdown(annotations: Sequence[Annotation]) -> list[str]:
    """Return Markdown lines for a document's annotations, or ``[]``.

    Markdown is the artifact that survives — it is what gets pasted into Slack,
    published to Confluence, and read when the HTML is long gone. A correction
    that only exists in the browser is a correction that did not happen.
    """
    rows = [a for a in annotations if a.text]
    if not rows:
        return []
    lines = [f"## {NOTES_HEADING}", ""]
    for a in rows:
        who = f" — _{a.author}_" if a.author else ""
        where = f" (on `{a.anchor}`)" if a.anchor else ""
        if a.kind == "field" and a.label:
            lines.append(f"- **{a.label}:** {a.text}{where}{who}")
        else:
            lines.append(f"- {a.text}{where}{who}")
    lines.append("")
    return lines


def with_annotations(args: dict, artifact: object) -> dict:
    """Attach an artifact's annotations to its export arguments, if it has any.

    Omitted when empty rather than emitted as ``[]``. Two reasons, one of each
    kind: it matches ``build_chrome``'s rule that an optional key is absent
    rather than blank, and it keeps every existing export byte-identical, so the
    committed wire fixtures do not move for a document nobody has annotated.
    """
    rows = annotations_payload(getattr(artifact, "annotations", ()))
    if rows:
        report = args.get("report")
        if isinstance(report, dict):
            report["annotations"] = rows
    return args


def edit_map(anchor: str, artifact: object, fields: Sequence[str]) -> dict:
    """The ``{field: {path, value}}`` map that makes a region correctable.

    Emitted only for a document actually being served editable, and absent from
    every file export — so a downloaded report is byte-for-byte what it always
    was, and the ten committed wire fixtures do not move.

    The **raw artifact value** rides beside the path, and that is the point.
    These payloads are one-way projections: a standup's team summary is shredded
    into sentences of link-runs with no inverse, so an editor opened on what is
    drawn could never hand back something the server can store, and every
    compare-and-swap would fail because the two sides would be comparing
    different texts. The editor opens on the string; the server re-derives the
    drawing.

    Non-string fields are skipped rather than coerced. A path that resolves to a
    number is not editable anywhere in the registry, and silently stringifying
    one here would offer an affordance the server would then refuse.
    """
    out: dict = {}
    for field in fields:
        value = getattr(artifact, field, None)
        if isinstance(value, str):
            out[field] = {"path": f"{anchor}.{field}" if anchor else field, "value": value}
    return out


def row_anchor(list_field: str, key: str, value: str) -> str:
    """``member_updates[name=Ada%20Lovelace]`` — a row addressed by its natural key."""
    from yeaboi.artifacts.paths import escape_value

    return f"{list_field}[{key}={escape_value(value)}]"
