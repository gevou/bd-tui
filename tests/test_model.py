"""Tests for the pure board model (Layer 2)."""
from beads_tui.data import Comment, Issue
from beads_tui.model import (
    DIMENSIONS,
    Filters,
    apply_filters,
    children_map,
    group_issues,
    comments_newest_first,
    has_relations,
    latest_comments,
    next_dimension,
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
