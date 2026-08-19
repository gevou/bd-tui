"""Layer 2: the pure board model — grouping, filtering, sorting.

No I/O, no Textual. Everything here is a deterministic function of the Issue
list so it can be unit-tested in isolation.
"""
from __future__ import annotations

import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

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

UNLABELED = "(unlabeled)"
RETRO_ONLY = "(retro-only)"


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


def children_map(issues: list[Issue]) -> dict[str, list[Issue]]:
    """parent id -> its child issues (in id order).

    Only nests children whose parent is itself present; children of an absent
    (filtered-out) parent are handled by orphan promotion in ``group_issues``.
    """
    ids = {i.id for i in issues}
    cm: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        if i.parent is not None and i.parent in ids:
            cm[i.parent].append(i)
    for kids in cm.values():
        kids.sort(key=lambda x: natural_key(x.id))
    return dict(cm)


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
    top-level card here so it still appears in its own column.
    """
    ids = {i.id for i in issues}
    top = [i for i in issues if i.parent is None or i.parent not in ids]
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
            real = [l for l in i.labels if not l.startswith("retro:")]
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
