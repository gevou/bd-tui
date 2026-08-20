"""Tests for the pure board model (Layer 2)."""
from beads_tui.data import Comment, Issue
from beads_tui.model import (
    DIMENSIONS,
    PROMOTE_STATUSES,
    Filters,
    apply_filters,
    build_columns,
    children_map,
    group_issues,
    comments_newest_first,
    has_relations,
    latest_comments,
    natural_key,
    next_dimension,
    promoted_child_ids,
    related_ids,
    subtree_ids,
)


def mk(id, status="open", priority=2, labels=None, parent=None, title="t", itype="task",
       dependencies=None):
    return Issue(
        id=id, title=title, status=status, priority=priority,
        labels=labels or [], parent=parent, issue_type=itype,
        dependencies=dependencies or [],
    )


def dep(depends_on_id, type="blocks"):
    return {"depends_on_id": depends_on_id, "type": type}


# --- grouping by status ---------------------------------------------------

def test_group_by_status_uses_canonical_column_order():
    issues = [mk("a", status="closed"), mk("b", status="in_progress"), mk("c", status="open")]
    cols = group_issues(issues, "status")
    assert list(cols.keys()) == ["open", "in_progress", "closed"]


def test_group_by_status_only_includes_present_statuses():
    cols = group_issues([mk("a", status="open")], "status")
    assert list(cols.keys()) == ["open"]


# --- grouping by priority -------------------------------------------------

def test_group_by_priority_ascending_p_labels():
    issues = [mk("a", priority=3), mk("b", priority=0), mk("c", priority=0)]
    cols = group_issues(issues, "priority")
    assert list(cols.keys()) == ["P0", "P3"]
    assert [i.id for i in cols["P0"]] == ["b", "c"]


# --- grouping by label ----------------------------------------------------

def test_group_by_label_multilabel_appears_in_each_column():
    issues = [mk("a", labels=["x", "y"]), mk("b", labels=["y"])]
    cols = group_issues(issues, "label")
    assert cols["x"] == [issues[0]]
    assert {i.id for i in cols["y"]} == {"a", "b"}


def test_group_by_label_unlabeled_bucket_and_retro_skipped():
    issues = [mk("a"), mk("b", labels=["retro:foo"]), mk("c", labels=["real"])]
    cols = group_issues(issues, "label")
    assert cols["(unlabeled)"] == [issues[0]]
    assert "retro:foo" not in cols
    # an issue with only retro labels is not lost
    assert issues[1] in cols["(retro-only)"]
    assert cols["real"] == [issues[2]]


# --- within-column sort ---------------------------------------------------

def test_within_column_sorted_by_priority_then_title():
    issues = [mk("a", priority=2, title="zeta"), mk("b", priority=0, title="beta"),
              mk("c", priority=0, title="alpha")]
    cols = group_issues(issues, "status")
    assert [i.id for i in cols["open"]] == ["c", "b", "a"]


# --- children -------------------------------------------------------------

def test_group_promotes_orphan_whose_parent_is_not_visible():
    # An in_progress child whose parent was filtered out (e.g. closed) must still
    # appear — promoted to a top-level card in its own status column.
    issues = [mk("gv-4sb.1", status="in_progress", parent="gv-4sb")]  # parent absent
    cols = group_issues(issues, "status")
    assert [i.id for i in cols["in_progress"]] == ["gv-4sb.1"]


# --- active-child promotion (status dimension) ----------------------------

def test_promote_statuses_are_the_active_ones():
    assert PROMOTE_STATUSES == {"in_progress", "blocked"}


def test_group_promotes_active_child_of_open_parent_to_its_own_column():
    # An in_progress child of an OPEN root must surface in the in_progress
    # column as its own card, NOT be buried (nested) under the open parent.
    parent = mk("gv-69z", status="open")
    child = mk("gv-69z.2", status="in_progress", parent="gv-69z")
    cols = group_issues([parent, child], "status")
    assert [i.id for i in cols["open"]] == ["gv-69z"]           # parent alone
    assert [i.id for i in cols["in_progress"]] == ["gv-69z.2"]  # child promoted


def test_promoted_child_is_not_also_nested_under_parent():
    # No duplication: a promoted child is excluded from the parent's nest list.
    parent = mk("gv-69z", status="open")
    child = mk("gv-69z.2", status="in_progress", parent="gv-69z")
    issues = [parent, child]
    promoted = promoted_child_ids(issues, "status")
    assert promoted == {"gv-69z.2"}
    cm = children_map(issues, promoted)
    assert "gv-69z" not in cm  # nothing left to nest under the parent


def test_group_also_promotes_blocked_child_of_open_parent():
    parent = mk("p", status="open")
    child = mk("p.1", status="blocked", parent="p")
    cols = group_issues([parent, child], "status")
    assert [i.id for i in cols["blocked"]] == ["p.1"]


def test_group_does_not_promote_open_child_of_open_parent():
    # A non-active (open) child still nests under its parent, as before.
    parent = mk("p", status="open")
    child = mk("p.1", status="open", parent="p")
    issues = [parent, child]
    assert promoted_child_ids(issues, "status") == set()
    cols = group_issues(issues, "status")
    assert [i.id for i in cols["open"]] == ["p"]  # only the parent is top-level
    assert [c.id for c in children_map(issues)["p"]] == ["p.1"]  # child nested


def test_group_keeps_active_child_nested_when_same_status_as_parent():
    # An in_progress child of an in_progress parent is already visible in that
    # column (nested) — don't promote-and-duplicate it within the same column.
    parent = mk("p", status="in_progress")
    child = mk("p.1", status="in_progress", parent="p")
    issues = [parent, child]
    assert promoted_child_ids(issues, "status") == set()
    cols = group_issues(issues, "status")
    assert [i.id for i in cols["in_progress"]] == ["p"]          # parent only
    assert [c.id for c in children_map(issues)["p"]] == ["p.1"]  # child nested


def test_promotion_only_applies_to_status_dimension():
    # Under priority/label, an in_progress child of an open parent stays nested.
    parent = mk("p", status="open", priority=1, labels=["x"])
    child = mk("p.1", status="in_progress", parent="p", priority=1, labels=["x"])
    issues = [parent, child]
    assert promoted_child_ids(issues, "priority") == set()
    assert promoted_child_ids(issues, "label") == set()
    pcols = group_issues(issues, "priority")
    assert [i.id for i in pcols["P1"]] == ["p"]  # child not promoted -> nested
    lcols = group_issues(issues, "label")
    assert [i.id for i in lcols["x"]] == ["p"]


def test_orphan_promotion_still_works_alongside_active_promotion():
    # Orphan (parent filtered out) AND active-child promotion coexist.
    orphan = mk("gv-4sb.1", status="in_progress", parent="gv-4sb")  # parent absent
    parent = mk("gv-69z", status="open")
    child = mk("gv-69z.2", status="in_progress", parent="gv-69z")
    cols = group_issues([orphan, parent, child], "status")
    assert {i.id for i in cols["in_progress"]} == {"gv-4sb.1", "gv-69z.2"}
    assert [i.id for i in cols["open"]] == ["gv-69z"]


def test_children_map_default_excludes_nothing():
    # Backwards-compatible: without an explicit promoted set, all present-parent
    # children still nest.
    issues = [mk("p"), mk("p.1", parent="p")]
    assert [c.id for c in children_map(issues)["p"]] == ["p.1"]


# --- build_columns: active-status columns as trees with context rows -------

def rowspec(cols, title):
    """(issue_id, indent, is_context) tuples for a column, in render order."""
    return [(r.issue.id, r.indent, r.is_context) for r in cols[title]]


def test_build_columns_non_active_column_is_flat_cards_and_nesting():
    # open column: top-level card + nested (non-active) child, no context rows.
    parent = mk("p", status="open")
    child = mk("p.1", status="open", parent="p")
    cols = build_columns([parent, child], "status")
    assert rowspec(cols, "open") == [("p", 0, False), ("p.1", 1, False)]


def test_build_columns_active_child_of_open_parent_nests_under_context_row():
    # in_progress child of an OPEN parent: parent shown as a dimmed CONTEXT row,
    # child a real card beneath it; parent's real card stays in the open column.
    parent = mk("gv-69z", status="open")
    child = mk("gv-69z.2", status="in_progress", parent="gv-69z")
    cols = build_columns([parent, child], "status")
    assert rowspec(cols, "open") == [("gv-69z", 0, False)]
    assert rowspec(cols, "in_progress") == [("gv-69z", 0, True), ("gv-69z.2", 1, False)]


def test_build_columns_context_row_carries_parents_real_status():
    parent = mk("gv-69z", status="open")
    child = mk("gv-69z.2", status="in_progress", parent="gv-69z")
    cols = build_columns([parent, child], "status")
    ctx = cols["in_progress"][0]
    assert ctx.is_context and ctx.issue.id == "gv-69z" and ctx.issue.status == "open"


def test_build_columns_multiple_active_children_share_one_context_row():
    parent = mk("gv-69z", status="open")
    c1 = mk("gv-69z.1", status="in_progress", parent="gv-69z", priority=1)
    c2 = mk("gv-69z.2", status="in_progress", parent="gv-69z", priority=1)
    cols = build_columns([parent, c1, c2], "status")
    assert rowspec(cols, "in_progress") == [
        ("gv-69z", 0, True), ("gv-69z.1", 1, False), ("gv-69z.2", 1, False)]


def test_build_columns_filtered_out_parent_becomes_context_row():
    # gv-4sb is closed -> filtered from the visible set, but resolvable via
    # all_issues; it still appears as a dimmed context row over its active kids.
    closed_parent = mk("gv-4sb", status="closed")
    k1 = mk("gv-4sb.1", status="in_progress", parent="gv-4sb")
    k2 = mk("gv-4sb.2", status="in_progress", parent="gv-4sb")
    visible = [k1, k2]  # closed parent filtered out
    cols = build_columns(visible, "status", all_issues=[closed_parent, k1, k2])
    assert rowspec(cols, "in_progress") == [
        ("gv-4sb", 0, True), ("gv-4sb.1", 1, False), ("gv-4sb.2", 1, False)]
    assert cols["in_progress"][0].issue.status == "closed"


def test_build_columns_active_child_of_active_parent_is_real_card_no_context():
    # in_progress child of in_progress parent nests under the parent's REAL card.
    parent = mk("p", status="in_progress")
    child = mk("p.1", status="in_progress", parent="p")
    cols = build_columns([parent, child], "status")
    assert rowspec(cols, "in_progress") == [("p", 0, False), ("p.1", 1, False)]


def test_build_columns_active_root_is_top_level_real_card():
    root = mk("solo", status="in_progress")
    cols = build_columns([root], "status")
    assert rowspec(cols, "in_progress") == [("solo", 0, False)]


def test_build_columns_context_rows_not_counted_as_cards():
    # Only real (non-context) rows count toward a column's ticket total.
    parent = mk("gv-69z", status="open")
    child = mk("gv-69z.2", status="in_progress", parent="gv-69z")
    cols = build_columns([parent, child], "status")
    real = [r for r in cols["in_progress"] if not r.is_context]
    assert [r.issue.id for r in real] == ["gv-69z.2"]


def test_build_columns_priority_dimension_has_no_context_rows():
    parent = mk("p", status="open", priority=1)
    child = mk("p.1", status="in_progress", parent="p", priority=1)
    cols = build_columns([parent, child], "priority")
    assert all(not r.is_context for rows in cols.values() for r in rows)
    assert rowspec(cols, "P1") == [("p", 0, False), ("p.1", 1, False)]


def test_build_columns_active_children_sort_by_natural_numeric_id():
    # .1 < .2 < .9 < .10 < .11 — same natural ordering as the open column, NOT
    # lexicographic (.1, .10, .11, .2).
    parent = mk("gv-69z", status="open")
    kids = [mk(f"gv-69z.{n}", status="in_progress", parent="gv-69z")
            for n in (2, 10, 1, 11, 9)]
    cols = build_columns([parent, *kids], "status")
    rendered = [r.issue.id for r in cols["in_progress"] if not r.is_context]
    assert rendered == ["gv-69z.1", "gv-69z.2", "gv-69z.9", "gv-69z.10", "gv-69z.11"]


def test_build_columns_active_order_is_natural_id_not_priority_or_alpha():
    # Children whose natural-id order DISAGREES with both alphabetical and
    # priority/title order. Natural id wins: .1, .2, .10.
    #   .2  -> P1, title "z"
    #   .10 -> P0, title "a"   (would come FIRST by priority, and by title)
    #   .1  -> P2, title "m"
    parent = mk("gv-69z", status="open")
    kids = [
        mk("gv-69z.2", status="in_progress", parent="gv-69z", priority=1, title="z"),
        mk("gv-69z.10", status="in_progress", parent="gv-69z", priority=0, title="a"),
        mk("gv-69z.1", status="in_progress", parent="gv-69z", priority=2, title="m"),
    ]
    cols = build_columns([parent, *kids], "status")
    rendered = [r.issue.id for r in cols["in_progress"] if not r.is_context]
    assert rendered == ["gv-69z.1", "gv-69z.2", "gv-69z.10"]  # natural id order
    assert rendered != ["gv-69z.10", "gv-69z.2", "gv-69z.1"]  # NOT priority/alpha


def test_build_columns_top_level_units_ordered_by_natural_id():
    # Two context parents + a root; top-level units must order by natural id
    # (gv-2 before gv-10), not by priority/title.
    p2 = mk("gv-2", status="open", priority=2, title="zzz")
    p10 = mk("gv-10", status="open", priority=0, title="aaa")
    root = mk("gv-3", status="in_progress", priority=1, title="mmm")
    c2 = mk("gv-2.1", status="in_progress", parent="gv-2")
    c10 = mk("gv-10.1", status="in_progress", parent="gv-10")
    cols = build_columns([p2, p10, root, c2, c10], "status")
    # top-level anchors = context parents + the root real card (indent 0), in
    # render order; natural id order is gv-2 < gv-3 < gv-10 (NOT priority/title).
    anchors = [r.issue.id for r in cols["in_progress"]
               if r.is_context or r.indent == 0]
    assert anchors == ["gv-2", "gv-3", "gv-10"]


def test_build_columns_active_nesting_matches_open_nesting_structure():
    # The in_progress tree must have the SAME indentation shape as the open tree:
    # a top row at indent 0 with its children at indent 1.
    open_parent = mk("op", status="open")
    open_kids = [mk("op.2", status="open", parent="op"),
                 mk("op.1", status="open", parent="op")]
    ip_parent = mk("ip", status="open")
    ip_kids = [mk("ip.2", status="in_progress", parent="ip"),
               mk("ip.1", status="in_progress", parent="ip")]
    cols = build_columns([open_parent, *open_kids, ip_parent, *ip_kids], "status")
    open_indents = [r.indent for r in cols["open"] if r.issue.id.startswith("op")]
    ip_indents = [r.indent for r in cols["in_progress"]]
    # open: parent(0) child(1) child(1); in_progress: context(0) child(1) child(1)
    assert open_indents == [0, 1, 1]
    assert ip_indents == [0, 1, 1]
    # and natural ordering holds in both
    assert [r.issue.id for r in cols["open"] if r.issue.id.startswith("op")] == \
        ["op", "op.1", "op.2"]
    assert [r.issue.id for r in cols["in_progress"]] == ["ip", "ip.1", "ip.2"]


def test_natural_key_sorts_numeric_suffixes_numerically():
    ids = ["p.10", "p.2", "p.1", "p.11", "p.9"]
    assert sorted(ids, key=natural_key) == ["p.1", "p.2", "p.9", "p.10", "p.11"]


def test_children_map_orders_children_numerically():
    issues = [mk("p"), mk("p.2", parent="p"), mk("p.10", parent="p"),
              mk("p.1", parent="p"), mk("p.9", parent="p")]
    cm = children_map(issues)
    assert [c.id for c in cm["p"]] == ["p.1", "p.2", "p.9", "p.10"]


def test_children_map_only_nests_children_with_a_visible_parent():
    # child whose parent is absent is NOT nested (it gets promoted instead)
    issues = [mk("c1", parent="ghost")]
    assert children_map(issues) == {}


def test_children_map_keys_by_parent_and_group_uses_only_top_level():
    parent = mk("p")
    child = mk("p.1", parent="p")
    cols = group_issues([parent, child], "status")
    # only the parent is a top-level card
    assert [i.id for i in cols["open"]] == ["p"]
    cm = children_map([parent, child])
    assert [c.id for c in cm["p"]] == ["p.1"]


# --- filters --------------------------------------------------------------

def test_apply_filters_hides_closed_and_deferred_by_default():
    issues = [mk("a", status="open"), mk("b", status="closed"), mk("c", status="deferred")]
    assert [i.id for i in apply_filters(issues, Filters())] == ["a"]


def test_apply_filters_show_inactive_includes_closed_and_deferred():
    issues = [mk("a", status="open"), mk("b", status="closed"), mk("c", status="deferred")]
    out = apply_filters(issues, Filters(show_inactive=True))
    assert {i.id for i in out} == {"a", "b", "c"}


def test_apply_filters_query_matches_id_title_label_case_insensitive():
    issues = [
        mk("gv-1", title="Fix export"),
        mk("gv-2", title="unrelated", labels=["EXPORT"]),
        mk("gv-3", title="nope"),
    ]
    out = apply_filters(issues, Filters(query="export"))
    assert {i.id for i in out} == {"gv-1", "gv-2"}


# --- dimension cycling ----------------------------------------------------

# --- related_ids (drill-down) ---------------------------------------------

def test_related_ids_includes_transitive_descendants():
    issues = [mk("P"), mk("c1", parent="P"), mk("c1a", parent="c1"), mk("other")]
    assert related_ids(issues, "P") == {"P", "c1", "c1a"}


def test_related_ids_includes_blocks_dependencies_both_directions():
    # A depends on B (blocks); C depends on A (blocks)
    issues = [mk("A", dependencies=[dep("B")]), mk("B"), mk("C", dependencies=[dep("A")]),
              mk("Z")]
    assert related_ids(issues, "A") == {"A", "B", "C"}


def test_related_ids_ignores_parent_child_dep_records_for_deps():
    # a parent-child dep record should be covered by descendants logic, not dep logic
    issues = [mk("P"), mk("c1", parent="P", dependencies=[dep("P", type="parent-child")])]
    assert related_ids(issues, "P") == {"P", "c1"}


def test_subtree_ids_is_root_plus_transitive_descendants_only():
    # dependencies must NOT be included in a subtree (unlike related_ids)
    issues = [mk("P"), mk("c1", parent="P"), mk("c1a", parent="c1"),
              mk("dep", dependencies=[]), mk("blocks_p", dependencies=[dep("P")]),
              mk("other")]
    assert subtree_ids(issues, "P") == {"P", "c1", "c1a"}


def test_has_relations_true_only_when_more_than_self():
    issues = [mk("lonely"), mk("P"), mk("c1", parent="P")]
    assert has_relations(issues, "lonely") is False
    assert has_relations(issues, "P") is True


# --- focus filter ---------------------------------------------------------

def test_apply_filters_focus_ids_restricts_to_set_and_shows_closed():
    issues = [mk("a"), mk("b", status="closed"), mk("c")]
    out = apply_filters(issues, Filters(focus_ids={"a", "b"}))
    # focus mode keeps closed items that are part of the focus set
    assert {i.id for i in out} == {"a", "b"}


def _c(id, when):
    return Comment(id=id, issue_id="x", author="a", text=id, created_at=when)


def test_comments_newest_first_orders_descending():
    out = comments_newest_first([_c("old", "2026-08-01T00:00:00Z"),
                                 _c("new", "2026-08-10T00:00:00Z"),
                                 _c("mid", "2026-08-05T00:00:00Z")])
    assert [c.id for c in out] == ["new", "mid", "old"]


def test_comments_newest_first_puts_missing_timestamps_last():
    out = comments_newest_first([_c("has", "2026-08-01T00:00:00Z"), _c("none", "")])
    assert [c.id for c in out] == ["has", "none"]


def test_latest_comments_flattens_across_issues_newest_first_and_caps():
    cmap = {
        "a": [_c("a1", "2026-08-01T00:00:00Z")],
        "b": [_c("b1", "2026-08-05T00:00:00Z"), _c("b2", "2026-08-03T00:00:00Z")],
    }
    out = latest_comments(cmap, limit=2)
    assert [(iid, c.id) for iid, c in out] == [("b", "b1"), ("b", "b2")]


def test_latest_comments_includes_issue_id_for_each_entry():
    cmap = {"x": [_c("x1", "2026-08-10T00:00:00Z")], "y": [_c("y1", "2026-08-09T00:00:00Z")]}
    out = latest_comments(cmap)
    assert [(iid, c.id) for iid, c in out] == [("x", "x1"), ("y", "y1")]


def test_next_dimension_cycles():
    assert DIMENSIONS == ["status", "priority", "label"]
    assert next_dimension("status") == "priority"
    assert next_dimension("priority") == "label"
    assert next_dimension("label") == "status"
