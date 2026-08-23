"""What ship can be pointed at: any node of a saved plan, at any level.

A plan is four flat lists joined by parent id — ``features``, ``stories``,
``tasks``, ``sprints`` (``agent/state.py``) — and every consumer in the tree
re-groups them inline. This is the one shared walker, written for ship's two
needs: render the plan as a pickable outline, and resolve one id to the work
behind it.

Vocabulary: the level token is ``"epic"``, because that is the word the TUI and
the user use. It resolves against ``state["features"]`` — ``Feature`` is the
dataclass name the rename to "epic" never reached.

Ids are LLM-supplied strings and the task-decomposer prompt still teaches the
pre-rename ``T-US-E1-001-01`` shape while the story writer emits ``US-F1-001``,
so a real plan can hold both. Nothing here parses a parent out of an id: the
links come from the ``feature_id`` / ``story_id`` fields only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from yeaboi.agent.state import Feature, Task, UserStory

logger = logging.getLogger(__name__)

LEVELS: tuple[str, ...] = ("epic", "story", "task")

# Where orphans go. The planner's own nodes skip a story whose feature_id names
# no feature (and a task whose story_id names no story), so a saved plan can
# carry them — and work the user can see in Planning must not vanish from the
# picker.
UNGROUPED_ID = "__ungrouped__"
UNGROUPED_TITLE = "Ungrouped"


@dataclass(frozen=True)
class ShipTarget:
    """One shippable unit of a plan, at any level."""

    level: str  # one of LEVELS
    id: str
    title: str
    summary: str  # the unit's own prose
    stories: tuple[UserStory, ...] = ()  # () for a task
    tasks: tuple[Task, ...] = ()
    parent_title: str = ""  # the epic behind a story, the story behind a task
    parent_summary: str = ""  # a task needs its story's text to know what "correct" means


@dataclass(frozen=True)
class OutlineRow:
    """One line of the picker tree."""

    key: str  # "epic:F1" / "story:US-F1-001" / "task:T-US-F1-001-01"
    level: str
    id: str
    title: str
    detail: str  # "3 stories" / "5 pts" / the task label
    depth: int  # 0 | 1 | 2
    parent_key: str = ""


def _items(state: dict | None, key: str) -> list:
    return list((state or {}).get(key) or [])


def _title_of(item: object) -> str:
    """The best short label an item has, whatever level it is."""
    return str(getattr(item, "title", "") or getattr(item, "goal", "") or getattr(item, "id", ""))


def _story_summary(story: UserStory) -> str:
    text = getattr(story, "text", "")
    return str(text or _title_of(story))


def _group(state: dict | None) -> tuple[list[Feature], dict[str, list[UserStory]], dict[str, list[Task]]]:
    """Epics in plan order, plus stories-by-epic and tasks-by-story.

    Orphans land under UNGROUPED_ID, which is appended as a synthetic epic only
    when something is actually in it.
    """
    features: list[Feature] = _items(state, "features")
    known_features = {getattr(f, "id", "") for f in features}
    stories_by_epic: dict[str, list[UserStory]] = {}
    for story in _items(state, "stories"):
        parent = str(getattr(story, "feature_id", "") or "")
        stories_by_epic.setdefault(parent if parent in known_features else UNGROUPED_ID, []).append(story)

    known_stories = {getattr(s, "id", "") for s in _items(state, "stories")}
    tasks_by_story: dict[str, list[Task]] = {}
    for task in _items(state, "tasks"):
        parent = str(getattr(task, "story_id", "") or "")
        tasks_by_story.setdefault(parent if parent in known_stories else UNGROUPED_ID, []).append(task)
    return features, stories_by_epic, tasks_by_story


def outline(state: dict | None) -> list[OutlineRow]:
    """The whole plan as a flat, ordered row list: epic → its stories → their tasks.

    Depth carries the nesting; the caller filters by whichever rows it has
    expanded. Never raises — a malformed plan yields the rows it can build.
    """
    features, stories_by_epic, tasks_by_story = _group(state)
    rows: list[OutlineRow] = []

    def _emit_epic(epic_id: str, title: str) -> None:
        stories = stories_by_epic.get(epic_id, [])
        epic_key = f"epic:{epic_id}"
        task_count = sum(len(tasks_by_story.get(getattr(s, "id", ""), [])) for s in stories)
        rows.append(
            OutlineRow(
                key=epic_key,
                level="epic",
                id=epic_id,
                title=title,
                detail=f"{_plural(len(stories), 'story', 'stories')} · {_plural(task_count, 'task', 'tasks')}",
                depth=0,
            )
        )
        for story in stories:
            story_id = str(getattr(story, "id", ""))
            story_key = f"story:{story_id}"
            tasks = tasks_by_story.get(story_id, [])
            rows.append(
                OutlineRow(
                    key=story_key,
                    level="story",
                    id=story_id,
                    title=_title_of(story),
                    detail=_story_detail(story, len(tasks)),
                    depth=1,
                    parent_key=epic_key,
                )
            )
            for task in tasks:
                rows.append(
                    OutlineRow(
                        key=f"task:{getattr(task, 'id', '')}",
                        level="task",
                        id=str(getattr(task, "id", "")),
                        title=_title_of(task),
                        detail=str(getattr(getattr(task, "label", ""), "value", "") or getattr(task, "label", "")),
                        depth=2,
                        parent_key=story_key,
                    )
                )

    for feature in features:
        _emit_epic(str(getattr(feature, "id", "")), _title_of(feature))
    if stories_by_epic.get(UNGROUPED_ID) or tasks_by_story.get(UNGROUPED_ID):
        _emit_epic(UNGROUPED_ID, UNGROUPED_TITLE)
        for task in tasks_by_story.get(UNGROUPED_ID, []):
            rows.append(
                OutlineRow(
                    key=f"task:{getattr(task, 'id', '')}",
                    level="task",
                    id=str(getattr(task, "id", "")),
                    title=_title_of(task),
                    detail="orphan task",
                    depth=1,
                    parent_key=f"epic:{UNGROUPED_ID}",
                )
            )
    return rows


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _story_detail(story: UserStory, task_count: int) -> str:
    points = getattr(story, "story_points", None)
    bits = []
    if points is not None:
        try:
            bits.append(f"{int(points)} pts")
        except (TypeError, ValueError):
            pass
    bits.append(_plural(task_count, "task", "tasks"))
    return " · ".join(bits)


def find_target(state: dict | None, item_id: str, *, level: str = "") -> ShipTarget:
    """The work behind *item_id*, at whichever level it lives.

    Searches epics → stories → tasks; an explicit *level* short-circuits so a
    colliding id can be disambiguated. Raises ValueError naming the available
    ids per level — the caller turns that into a failed artifact, never a
    traceback.
    """
    if level and level not in LEVELS:
        raise ValueError(f"unknown level {level!r} (expected one of {', '.join(LEVELS)})")
    features, stories_by_epic, tasks_by_story = _group(state)

    if level in ("", "epic"):
        for feature in features:
            if str(getattr(feature, "id", "")) == item_id:
                return _epic_target(feature, stories_by_epic, tasks_by_story)
    if level in ("", "story"):
        for story in _items(state, "stories"):
            if str(getattr(story, "id", "")) == item_id:
                return _story_target(story, features, tasks_by_story)
    if level in ("", "task"):
        for task in _items(state, "tasks"):
            if str(getattr(task, "id", "")) == item_id:
                return _task_target(task, state)
    raise ValueError(f"{item_id!r} is not in this plan ({_available(state, level)})")


def _available(state: dict | None, level: str) -> str:
    """The ids the user could have meant, per level — the whole point of the error."""
    wanted = (level,) if level else LEVELS
    parts = []
    for name, key in (("epics", "features"), ("stories", "stories"), ("tasks", "tasks")):
        token = {"epics": "epic", "stories": "story", "tasks": "task"}[name]
        if token not in wanted:
            continue
        ids = [str(getattr(i, "id", "?")) for i in _items(state, key)]
        parts.append(f"{name}: {', '.join(ids) if ids else 'none'}")
    return "available — " + "; ".join(parts)


def _epic_target(
    feature: Feature,
    stories_by_epic: dict[str, list[UserStory]],
    tasks_by_story: dict[str, list[Task]],
) -> ShipTarget:
    stories = tuple(stories_by_epic.get(str(getattr(feature, "id", "")), []))
    tasks: list[Task] = []
    for story in stories:
        tasks.extend(tasks_by_story.get(str(getattr(story, "id", "")), []))
    return ShipTarget(
        level="epic",
        id=str(getattr(feature, "id", "")),
        title=_title_of(feature),
        summary=str(getattr(feature, "description", "") or ""),
        stories=stories,
        tasks=tuple(tasks),
    )


def _story_target(
    story: UserStory,
    features: list[Feature],
    tasks_by_story: dict[str, list[Task]],
) -> ShipTarget:
    parent = next((f for f in features if str(getattr(f, "id", "")) == str(getattr(story, "feature_id", ""))), None)
    return ShipTarget(
        level="story",
        id=str(getattr(story, "id", "")),
        title=_title_of(story),
        summary=_story_summary(story),
        stories=(story,),
        tasks=tuple(tasks_by_story.get(str(getattr(story, "id", "")), [])),
        parent_title=_title_of(parent) if parent is not None else "",
        parent_summary=str(getattr(parent, "description", "") or "") if parent is not None else "",
    )


def _task_target(task: Task, state: dict | None) -> ShipTarget:
    parent = next(
        (s for s in _items(state, "stories") if str(getattr(s, "id", "")) == str(getattr(task, "story_id", ""))),
        None,
    )
    return ShipTarget(
        level="task",
        id=str(getattr(task, "id", "")),
        title=_title_of(task),
        summary=str(getattr(task, "description", "") or ""),
        stories=(parent,) if parent is not None else (),
        tasks=(task,),
        parent_title=_title_of(parent) if parent is not None else "",
        parent_summary=_story_summary(parent) if parent is not None else "",
    )


def split_story_ids(target: ShipTarget) -> tuple[str, ...]:
    """The stories a "one PR per story" batch would run, in plan order.

    Only an epic splits: a story and a task have no child stories.
    """
    if target.level != "epic":
        return ()
    return tuple(str(getattr(s, "id", "")) for s in target.stories if getattr(s, "id", ""))
