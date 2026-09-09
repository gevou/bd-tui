"""Layer 2: the pure board model — grouping, filtering, sorting.

No I/O, no Textual. Everything here is a deterministic function of the Issue
list so it can be unit-tested in isolation.
"""
from __future__ import annotations

import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from beads_tui.data import Comment, Issue


def natural_key(s: str) -> list:
    """Sort key that orders embedded numbers numerically (so `.9` < `.10`).

    Splits into alternating text/number chunks; digit runs compare as ints. The
    alternation always starts with text, so aligned positions never mix str/int.
    """
    return [int(chunk) if chunk.isdigit() else chunk
            for chunk in re.split(r"(\d+)", s)]

DIMENSIONS = ["status", "priority", "label"]

# Canonical left-to-right column order for the status dimension. Any status not
# listed here (custom statuses) sorts after these, alphabetically.
STATUS_ORDER = ["open", "in_progress", "blocked", "deferred", "closed"]

# Statuses hidden by default; the "show/hide" toggle reveals them.
INACTIVE_STATUSES = {"closed", "deferred"}

# "Active" statuses that get surfaced into their own status column even when they
# are a child of a differently-statused parent (see ``promoted_child_ids``). This
# stops an in_progress/blocked child from being buried (nested) under, say, an
# open parent — where it would never appear in the in_progress/blocked column.
PROMOTE_STATUSES = {"in_progress", "blocked"}

UNLABELED = "(unlabeled)"
RETRO_ONLY = "(retro-only)"

HUB_STATES = {
    "needs_user",
    "blocked",
    "active",
    "waiting_agent",
    "waiting_ci",
    "parked",
}
HUB_STATE_ORDER = {
    "needs_user": 0,
    "blocked": 1,
    "active": 2,
    "waiting_agent": 3,
    "waiting_ci": 4,
    "parked": 5,
}


@dataclass(frozen=True)
class HubItem:
    """One human-level thread in the dedicated Hub dashboard."""

    issue: Issue
    state: str
    summary: str
    next_action: str
    waiting_on: str = ""
    owner: str = ""
    prs: tuple[str, ...] = ()
    last_material_at: str = ""
    age: str = ""


def _clean_one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _first_sentence(value: str, limit: int = 150) -> str:
    text = _clean_one_line(value)
    if not text:
        return ""
    match = re.search(r"(?<=[.!?])\s", text)
    sentence = text[: match.start() + 1] if match else text
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


def _sentence_case(value: str) -> str:
    return value[:1].upper() + value[1:] if value else value


def _hub_state(issue: Issue) -> str:
    configured = _clean_one_line(issue.metadata.get("hub_state"))
    if configured in HUB_STATES:
        return configured
    if issue.status == "blocked":
        return "blocked"
    if issue.status in {"closed", "deferred"}:
        return "parked"
    return "active"


def _hub_summary(issue: Issue) -> str:
    configured = _clean_one_line(issue.metadata.get("hub_summary"))
    if configured:
        return configured
    note_summary = _first_sentence(issue.notes)
    if note_summary.lower().startswith(("next:", "next action:")):
        note_summary = ""
    return (
        note_summary
        or _first_sentence(issue.description)
        or "No current summary recorded."
    )


def _hub_next_action(issue: Issue) -> str:
    configured = _clean_one_line(issue.metadata.get("hub_next_action"))
    if configured:
        return configured
    # Notes often contain a coordinator-authored "Next:" checkpoint. Prefer it
    # over a generic fallback without requiring metadata adoption up front.
    match = re.search(r"(?:^|\n)\s*Next(?: action)?\s*:\s*(.+)", issue.notes, re.I)
    if match:
        return _sentence_case(_first_sentence(match.group(1), limit=170))
    sentences = [
        _clean_one_line(part)
        for part in re.split(r"(?<=[.!?])\s+", issue.notes)
        if _clean_one_line(part)
    ]
    action_pattern = re.compile(
        r"\b(must|needs? to|should|wait(?:s|ing)? for|restack|push|publish|"
        r"approve|confirm|resolve|review|open)\b",
        re.I,
    )
    for sentence in reversed(sentences):
        if action_pattern.search(sentence):
            return _first_sentence(sentence, limit=170)
    if issue.status == "blocked":
        return "Resolve the blocker recorded in this thread."
    if issue.status in {"closed", "deferred"}:
        return "No action while parked."
    return "Open details to review the latest checkpoint."


def _hub_prs(issue: Issue) -> tuple[str, ...]:
    raw = issue.metadata.get("hub_prs")
    if not raw:
        for key in ("publication_prs", "pr_number", "github_pr", "pr"):
            if issue.metadata.get(key):
                raw = issue.metadata[key]
                break
    if isinstance(raw, (list, tuple, set)):
        values = [str(value) for value in raw]
    else:
        values = re.findall(r"\d+", str(raw or ""))
    return tuple(dict.fromkeys(values))


def _timestamp_sort_value(iso: str) -> float:
    if not iso:
        return 0
    try:
        value = iso.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0


def relative_age(iso: str, now: Optional[datetime] = None) -> str:
    """Compact age for a material-update timestamp, safe for odd input."""
    if not iso:
        return ""
    value = iso.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        changed = datetime.fromisoformat(value)
    except ValueError:
        return iso
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - changed).total_seconds()))
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    if seconds < 604_800:
        return f"{seconds // 86_400}d"
    return f"{seconds // 604_800}w"


def hub_members(issues: list[Issue], root_id: str) -> list[Issue]:
    """Return only direct ``tracks`` targets and direct children of ``root_id``.

    This deliberately ignores descendants and every other dependency type. A
    thread included through both mechanisms is returned once.
    """
    by_id = {issue.id: issue for issue in issues}
    root = by_id.get(root_id)
    if root is None:
        return []
    ids = {
        dependency.get("depends_on_id")
        for dependency in root.dependencies
        if dependency.get("type") == "tracks"
    }
    ids.update(issue.id for issue in issues if issue.parent == root_id)
    ids.discard(None)
    return [issue for issue in issues if issue.id in ids]


def build_hub_items(
    issues: list[Issue], root_id: str, now: Optional[datetime] = None
) -> list[HubItem]:
    """Build and order the flat, presentation-ready Hub thread list."""
    items = []
    for issue in hub_members(issues, root_id):
        material_at = _clean_one_line(
            issue.metadata.get("hub_last_material_at") or issue.updated_at
        )
        items.append(
            HubItem(
                issue=issue,
                state=_hub_state(issue),
                summary=_hub_summary(issue),
                next_action=_hub_next_action(issue),
                waiting_on=_clean_one_line(issue.metadata.get("hub_waiting_on")),
                owner=_clean_one_line(issue.assignee or issue.owner),
                prs=_hub_prs(issue),
                last_material_at=material_at,
                age=relative_age(material_at, now),
            )
        )
    items.sort(
        key=lambda item: (
            HUB_STATE_ORDER[item.state],
            item.issue.priority,
            -_timestamp_sort_value(item.last_material_at),
            natural_key(item.issue.id),
        )
    )
    return items


def hub_summary(items: list[HubItem]) -> str:
    """Human-readable status rollup for the Hub header."""
    counts = {state: 0 for state in HUB_STATES}
    for item in items:
        counts[item.state] += 1
    waiting = counts["waiting_agent"] + counts["waiting_ci"]
    parts = []
    if counts["needs_user"]:
        parts.append(f"{counts['needs_user']} needs you")
    if counts["blocked"]:
        parts.append(f"{counts['blocked']} blocked")
    if counts["active"]:
        parts.append(f"{counts['active']} active")
    if waiting:
        parts.append(f"{waiting} waiting")
    if counts["parked"]:
        parts.append(f"{counts['parked']} parked")
    return " · ".join(parts) if parts else "No open threads"


@dataclass
class ColumnRow:
    """One rendered row of a board column.

    ``is_context`` marks a dimmed, non-interactive ancestor row: a parent shown
    only to group its active children beneath it (in an active-status column),
    not a real card. Real cards have ``is_context=False``.
    """
    issue: Issue
    indent: int = 0
    is_context: bool = False


@dataclass
class Filters:
    query: str = ""
    # When False (default), closed AND deferred issues are hidden.
    show_inactive: bool = False
    # When set (drill-down mode), only issues whose id is in this set are shown.
    focus_ids: "set[str] | None" = None


def next_dimension(dim: str) -> str:
    return DIMENSIONS[(DIMENSIONS.index(dim) + 1) % len(DIMENSIONS)]


def comments_newest_first(comments: list[Comment]) -> list[Comment]:
    """Newest comment first; entries without a timestamp sort to the end.
    (ISO-8601 UTC strings sort correctly lexicographically.)"""
    return sorted(comments, key=lambda c: c.created_at or "", reverse=True)


def latest_comments(
    comment_map: "dict[str, list[Comment]]", limit: int = 25
) -> "list[tuple[str, Comment]]":
    """Flatten comments across issues into a newest-first (issue_id, comment)
    feed, capped at ``limit``. Used by the drill-in activity pane."""
    flat = [(iid, c) for iid, comments in comment_map.items() for c in comments]
    flat.sort(key=lambda pair: pair[1].created_at or "", reverse=True)
    return flat[:limit]


def is_child(issue: Issue) -> bool:
    return issue.parent is not None


def children_map(
    issues: list[Issue], promoted_ids: "set[str] | None" = None
) -> dict[str, list[Issue]]:
    """parent id -> its child issues (in id order).

    Only nests children whose parent is itself present; children of an absent
    (filtered-out) parent are handled by orphan promotion in ``group_issues``.
    Children in ``promoted_ids`` (active children surfaced into their own status
    column — see ``promoted_child_ids``) are excluded here so they are not also
    nested under their parent (which would double-render them).
    """
    promoted = promoted_ids or set()
    ids = {i.id for i in issues}
    cm: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        if i.parent is not None and i.parent in ids and i.id not in promoted:
            cm[i.parent].append(i)
    for kids in cm.values():
        kids.sort(key=lambda x: natural_key(x.id))
    return dict(cm)


def promoted_child_ids(issues: list[Issue], dimension: str) -> "set[str]":
    """Ids of children surfaced into their own status column instead of nested.

    Only meaningful for the ``status`` dimension (empty otherwise): a child whose
    own status is "active" (``PROMOTE_STATUSES``) and differs from its (present)
    parent's status is promoted to a standalone card in its own column. A child
    whose status matches its parent's column is left nested (it is already
    visible there), and children of a filtered-out parent are handled separately
    by orphan promotion in ``group_issues`` — they are not returned here.
    """
    if dimension != "status":
        return set()
    by_id = {i.id: i for i in issues}
    promoted: "set[str]" = set()
    for i in issues:
        parent = by_id.get(i.parent) if i.parent else None
        if parent is not None and i.status in PROMOTE_STATUSES and i.status != parent.status:
            promoted.add(i.id)
    return promoted


def matches(issue: Issue, query: str) -> bool:
    if not query:
        return True
    q = query.lower()
    if q in issue.id.lower() or q in issue.title.lower():
        return True
    return any(q in label.lower() for label in issue.labels)


def apply_filters(issues: list[Issue], filters: Filters) -> list[Issue]:
    out = []
    for i in issues:
        if filters.focus_ids is not None:
            # Drill-down mode: show exactly the focus set (including inactive items).
            if i.id not in filters.focus_ids:
                continue
        elif not filters.show_inactive and i.status in INACTIVE_STATUSES:
            continue
        if not matches(i, filters.query):
            continue
        out.append(i)
    return out


def _blocks_dep_ids(issue: Issue) -> list[str]:
    """Ids this issue blocks-depends on (excludes parent-child records)."""
    return [
        d.get("depends_on_id")
        for d in issue.dependencies
        if d.get("type") != "parent-child" and d.get("depends_on_id")
    ]


def subtree_ids(issues: list[Issue], root_id: str) -> "set[str]":
    """A ticket and all of its transitive descendants (via the parent chain).
    Dependencies are NOT included — this is the ownership subtree only."""
    kids: dict[str, list[str]] = defaultdict(list)
    for i in issues:
        if i.parent:
            kids[i.parent].append(i.id)
    result: "set[str]" = {root_id}
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for kid in kids.get(cur, []):
            if kid not in result:
                result.add(kid)
                stack.append(kid)
    return result


def related_ids(issues: list[Issue], root_id: str) -> "set[str]":
    """The drill-down set for a ticket: itself, all transitive descendants,
    and its direct blocks-dependencies in both directions."""
    by_id = {i.id: i for i in issues}
    result: "set[str]" = subtree_ids(issues, root_id)

    # direct blocks-dependencies: things root depends on …
    root = by_id.get(root_id)
    if root:
        result.update(_blocks_dep_ids(root))
    # … and things that depend on root
    for i in issues:
        if root_id in _blocks_dep_ids(i):
            result.add(i.id)

    return result


def has_relations(issues: list[Issue], root_id: str) -> bool:
    """True when a ticket has any descendant or dependency to drill into."""
    return len(related_ids(issues, root_id)) > 1


def _sort_key(issue: Issue):
    return (issue.priority, issue.title)


def _status_order_key(status: str) -> tuple[int, str]:
    if status in STATUS_ORDER:
        return (STATUS_ORDER.index(status), "")
    return (len(STATUS_ORDER), status)


def group_issues(issues: list[Issue], dimension: str) -> "OrderedDict[str, list[Issue]]":
    """Group top-level issues into ordered columns for the given dimension.

    Child issues render nested under their parent via ``children_map``; but a
    child whose parent is not in ``issues`` (filtered out) is promoted to a
    top-level card here so it still appears in its own column. For the status
    dimension, active children (``promoted_child_ids``) are likewise promoted to
    their own status column instead of being buried under a differently-statused
    parent.
    """
    ids = {i.id for i in issues}
    promoted = promoted_child_ids(issues, dimension)
    top = [i for i in issues
           if i.parent is None or i.parent not in ids or i.id in promoted]
    buckets: dict[str, list[Issue]] = defaultdict(list)

    if dimension == "status":
        for i in top:
            buckets[i.status].append(i)
        keys = sorted(buckets, key=_status_order_key)
    elif dimension == "priority":
        for i in top:
            buckets[f"P{i.priority}"].append(i)
        keys = sorted(buckets)
    elif dimension == "label":
        for i in top:
            real = [label for label in i.labels if not label.startswith("retro:")]
            if not i.labels:
                buckets[UNLABELED].append(i)
            elif not real:
                buckets[RETRO_ONLY].append(i)
            else:
                for label in real:
                    buckets[label].append(i)
        keys = sorted(buckets)
    else:  # pragma: no cover - guarded by DIMENSIONS
        raise ValueError(f"unknown dimension: {dimension}")

    result: "OrderedDict[str, list[Issue]]" = OrderedDict()
    for k in keys:
        result[k] = sorted(buckets[k], key=_sort_key)
    return result


def _active_column_rows(
    visible: list[Issue], status: str, all_by_id: dict[str, Issue]
) -> list[ColumnRow]:
    """Rows for an ACTIVE-status column, rendered like the ``open`` tree but
    scoped to ``status`` tickets.

    A ``status`` ticket whose parent is a different status (or filtered out) is
    grouped beneath a dimmed CONTEXT row for that parent, so the column reads as
    a hierarchy rather than a flat list. A ``status`` ticket whose parent is
    itself in this column stays a real card nested under it; a ``status`` root is
    a top-level real card. ``all_by_id`` resolves parents (e.g. a closed parent)
    that were filtered out of ``visible``.
    """
    actives = [i for i in visible if i.status == status]
    active_ids = {i.id for i in actives}
    # Nesting among same-status tickets uses the EXACT open-column helper, so the
    # ordering (natural_key on id) and depth are identical to open.
    active_cmap = children_map(actives)

    def emit(rows: list[ColumnRow], issue: Issue, indent: int) -> None:
        rows.append(ColumnRow(issue, indent, is_context=False))
        for kid in active_cmap.get(issue.id, []):  # already natural_key-sorted
            emit(rows, kid, indent + 1)

    # Top-level actives mirror the open column's ``top``: those whose parent is
    # not itself an active card here. Each is either a root (real top-level card)
    # or sits under a dimmed context row for its non-active/filtered parent.
    top_actives = [a for a in actives if a.parent not in active_ids]
    roots = [a for a in top_actives
             if a.parent is None or all_by_id.get(a.parent) is None]
    root_ids = {a.id for a in roots}
    ctx_groups: "OrderedDict[str, list[Issue]]" = OrderedDict()
    for a in top_actives:
        if a.id not in root_ids:
            ctx_groups.setdefault(a.parent, []).append(a)

    # Order top-level units by the anchor's natural id (a root's own id; a context
    # group's parent id), so the whole tree reads in clean numeric id order
    # (gv-2 < gv-3 < gv-10, and .1 < .2 < … < .9 < .10) — matching how the open
    # column's nested children order. Never priority/title here.
    units: list[tuple] = [(natural_key(a.id), "root", a) for a in roots]
    for pid, kids in ctx_groups.items():
        parent = all_by_id[pid]
        units.append((natural_key(pid), "ctx", (parent, kids)))
    units.sort(key=lambda u: u[0])

    rows: list[ColumnRow] = []
    for _, kind, payload in units:
        if kind == "root":
            emit(rows, payload, 0)
        else:
            parent, kids = payload
            rows.append(ColumnRow(parent, 0, is_context=True))
            for kid in sorted(kids, key=lambda x: natural_key(x.id)):
                emit(rows, kid, 1)
    return rows


def build_columns(
    issues: list[Issue], dimension: str, all_issues: "list[Issue] | None" = None
) -> "OrderedDict[str, list[ColumnRow]]":
    """Full ordered row layout per column: real cards (with nesting) plus, for
    active-status columns, dimmed context ancestor rows.

    ``issues`` is the visible (post-filter) set. ``all_issues`` (defaults to
    ``issues``) resolves context ancestors that were filtered out of the visible
    set. Non-active columns and the priority/label dimensions render exactly as
    before: top-level cards with their non-active children nested one level.
    """
    all_by_id = {i.id: i for i in (all_issues or issues)}
    columns = group_issues(issues, dimension)
    promoted = promoted_child_ids(issues, dimension)
    cmap = children_map(issues, promoted)

    result: "OrderedDict[str, list[ColumnRow]]" = OrderedDict()
    for title, items in columns.items():
        if dimension == "status" and title in PROMOTE_STATUSES:
            result[title] = _active_column_rows(issues, title, all_by_id)
            continue
        rows: list[ColumnRow] = []
        for issue in items:
            rows.append(ColumnRow(issue, 0, is_context=False))
            for kid in cmap.get(issue.id, []):
                rows.append(ColumnRow(kid, 1, is_context=False))
        result[title] = rows
    return result
