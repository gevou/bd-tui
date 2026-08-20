"""Interaction tests for the Textual app (Layer 3), driven by the pilot."""
import pytest

from beads_tui.data import Comment, Issue
from beads_tui.app import BoardApp
from beads_tui.widgets import Card, Column


class FakeClient:
    def __init__(self, issues, comments=None):
        self._issues = issues
        self._comments = comments or {}
        self.added: list[tuple[str, str]] = []

    def list_issues(self):
        return list(self._issues)

    def fetch_comments(self, issue_id):
        return list(self._comments.get(issue_id, []))

    def add_comment(self, issue_id, text):
        self.added.append((issue_id, text))

    def set_status(self, ids, status):
        ids = set(ids)
        for i in self._issues:
            if i.id in ids:
                i.status = status

    def close(self, ids, reason):
        self.set_status(ids, "closed")


def mk(id, status="open", priority=2, labels=None, parent=None, title="t"):
    return Issue(id=id, title=title, status=status, priority=priority,
                 labels=labels or [], parent=parent)


def app_with(issues, comments=None, dimension="status"):
    # poll_interval=0 disables background polling for deterministic tests
    return BoardApp(client=FakeClient(issues, comments),
                    dimension=dimension, poll_interval=0)


@pytest.mark.asyncio
async def test_renders_one_column_per_status_group():
    app = app_with([mk("a", status="open"), mk("b", status="closed"),
                    mk("c", status="in_progress")])
    async with app.run_test() as pilot:
        await pilot.pause()
        titles = [c.title for c in app.query(Column)]
        # closed hidden by default -> only open + in_progress
        assert titles == ["open", "in_progress"]


@pytest.mark.asyncio
async def test_g_cycles_grouping_dimension():
    app = app_with([mk("a", status="open", priority=0)])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.dimension == "status"
        await pilot.press("g")
        await pilot.pause()
        assert app.dimension == "priority"
        titles = [c.title for c in app.query(Column)]
        assert titles == ["P0"]


@pytest.mark.asyncio
async def test_toggle_reveals_closed_and_deferred_cards():
    app = app_with([mk("a", status="open"), mk("b", status="closed"),
                    mk("c", status="deferred")])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(Card)) == 1  # only "open" shown by default
        await pilot.press("full_stop")  # the '.' key
        await pilot.pause()
        assert len(app.query(Card)) == 3  # closed + deferred now revealed too


@pytest.mark.asyncio
async def test_enter_on_card_opens_detail_with_comments():
    issues = [mk("gv-1", title="Fix things")]
    comments = {"gv-1": [Comment(id="1", issue_id="gv-1", author="agent-x",
                                 text="looked into it", created_at="2026-08-01T00:00:00Z")]}
    app = BoardApp(client=FakeClient(issues, comments), poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query(Card).first().focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # detail screen is now on top and shows the comment text
        rendered = app.screen.render_comment_texts()
        assert "looked into it" in rendered


@pytest.mark.asyncio
async def test_arrow_navigation_moves_focus_across_columns_and_rows():
    # column "open" (first): a1, a2; column "in_progress" (second): p1
    issues = [mk("p1", status="in_progress"),
              mk("a1", status="open", priority=0, title="aaa"),
              mk("a2", status="open", priority=1, title="bbb")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()

        def focused_id():
            f = app.focused
            return f.issue.id if isinstance(f, Card) else None

        # focus the first card in the first (open) column
        app.query(Card).first().focus()
        await pilot.pause()
        assert focused_id() == "a1"
        await pilot.press("down")
        await pilot.pause()
        assert focused_id() == "a2"
        await pilot.press("right")
        await pilot.pause()
        assert focused_id() == "p1"  # in_progress column, row clamped to 0
        await pilot.press("left")
        await pilot.pause()
        assert focused_id() == "a1"


@pytest.mark.asyncio
async def test_up_down_move_focus_even_when_column_overflows():
    # A tall column that scrolls: arrow keys must move the highlight between
    # cards, not scroll the container past a stationary focus.
    issues = [mk(f"gv-{n}", status="open", priority=2, title=f"ticket {n}")
              for n in range(30)]
    app = app_with(issues)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause()

        def focused_id():
            f = app.focused
            return f.issue.id if isinstance(f, Card) else None

        app.query(Card).first().focus()
        await pilot.pause()
        assert focused_id() == "gv-0"
        await pilot.press("down")
        await pilot.pause()
        assert focused_id() == "gv-1"
        await pilot.press("up")
        await pilot.pause()
        assert focused_id() == "gv-0"


@pytest.mark.asyncio
async def test_mouse_click_focuses_and_opens_detail():
    from beads_tui.app import DetailScreen
    issues = [mk("gv-1", title="clickable")]
    app = BoardApp(client=FakeClient(issues, {"gv-1": []}), poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(Card)
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.issue.id == "gv-1"


@pytest.mark.asyncio
async def test_poll_does_not_rebuild_when_data_unchanged():
    # The periodic refresh must not wipe/remount the board (that causes flicker)
    # when nothing changed.
    app = app_with([mk("a", status="open"), mk("b", status="in_progress")])
    async with app.run_test() as pilot:
        await pilot.pause()
        before = list(app.query(Column))
        await app.reload()  # same underlying data
        await pilot.pause()
        after = list(app.query(Column))
        assert before == after  # identical widget instances -> no rebuild -> no flicker


@pytest.mark.asyncio
async def test_refresh_preserves_focused_card():
    client = FakeClient([mk("a", status="open"), mk("b", status="open"),
                         mk("c", status="open")])
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "b")
        assert app.focused.issue.id == "b"
        # a data change forces a rebuild; focus should stay on "b", not jump to "a"
        client._issues.append(mk("d", status="open"))
        await app.reload()
        await pilot.pause()
        assert isinstance(app.focused, Card) and app.focused.issue.id == "b"


@pytest.mark.asyncio
async def test_refresh_restores_last_card_even_if_focus_drifted_off_cards():
    # In drill-in the panes are focusable scroll containers; if focus drifts onto
    # one, a refresh must still restore the last card the user was on (not card #1).
    issues = [mk("a", status="open"), mk("b", status="open"), mk("c", status="open")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "b")
        app.set_focus(None)              # focus drifts off the cards
        await pilot.pause()
        await app.reload(force=True)
        await pilot.pause()
        assert isinstance(app.focused, Card) and app.focused.issue.id == "b"


@pytest.mark.asyncio
async def test_reload_rebuilds_when_data_changes():
    client = FakeClient([mk("a", status="open")])
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(Card)) == 1
        client._issues.append(mk("b", status="open"))
        await app.reload()
        await pilot.pause()
        assert len(app.query(Card)) == 2


@pytest.mark.asyncio
async def test_f_drills_into_subtree_and_escape_clears():
    issues = [mk("P", status="open"), mk("c1", status="open", parent="P"),
              mk("other", status="open")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        # 2 top-level cards (P, other) + 1 nested (c1) = 3
        assert len(app.query(Card)) == 3
        # focus P and drill in
        p_card = next(c for c in app.query(Card) if c.issue.id == "P")
        p_card.focus()
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        ids = {c.issue.id for c in app.query(Card)}
        assert ids == {"P", "c1"}  # "other" filtered out
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.query(Card)) == 3  # restored


async def _focus(app, pilot, issue_id):
    card = next(c for c in app.query(Card) if c.issue.id == issue_id)
    card.focus()
    await pilot.pause()


@pytest.mark.asyncio
async def test_detail_pane_hidden_outside_drill_in():
    from beads_tui.widgets import DetailPane
    app = app_with([mk("a", status="open"), mk("b", status="open")])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(DetailPane).display is False


@pytest.mark.asyncio
async def test_detail_pane_shows_and_tracks_focused_issue_in_drill_in():
    from beads_tui.widgets import DetailPane
    issues = [mk("P", status="open", title="parent title"),
              mk("c1", status="open", parent="P", title="child title")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "P")
        await pilot.press("f")           # drill in
        await pilot.pause()
        pane = app.query_one(DetailPane)
        assert pane.display is True
        assert pane.issue.id == "P"
        # moving the highlight updates the pane, no Enter needed
        await _focus(app, pilot, "c1")
        await pilot.pause()
        assert pane.issue.id == "c1"
        # leaving drill-in hides the pane again
        await pilot.press("escape")
        await pilot.pause()
        assert pane.display is False


@pytest.mark.asyncio
async def test_d_defers_whole_subtree_after_confirmation():
    client = FakeClient([mk("P", status="open"), mk("c1", status="open", parent="P"),
                         mk("c2", status="closed", parent="P"), mk("other", status="open")])
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "P")
        await pilot.press("d")          # opens confirmation
        await pilot.pause()
        await pilot.press("y")          # confirm
        await pilot.pause()
        byid = {i.id: i.status for i in client._issues}
        assert byid["P"] == "deferred"
        assert byid["c1"] == "deferred"
        assert byid["c2"] == "closed"   # already-closed left alone
        assert byid["other"] == "open"  # outside the subtree


@pytest.mark.asyncio
async def test_d_cancelled_makes_no_change():
    client = FakeClient([mk("P", status="open"), mk("c1", status="open", parent="P")])
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "P")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("n")          # decline
        await pilot.pause()
        assert all(i.status == "open" for i in client._issues)


@pytest.mark.asyncio
async def test_d_on_deferred_subtree_reopens_it():
    client = FakeClient([mk("P", status="deferred"), mk("c1", status="deferred", parent="P")])
    app = BoardApp(client=client, dimension="status", poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("full_stop")  # reveal deferred so we can select P
        await pilot.pause()
        await _focus(app, pilot, "P")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert all(i.status == "open" for i in client._issues)


@pytest.mark.asyncio
async def test_shift_x_closes_whole_subtree_after_confirmation():
    client = FakeClient([mk("P", status="open"), mk("c1", status="open", parent="P"),
                         mk("other", status="open")])
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "P")
        await pilot.press("X")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        byid = {i.id: i.status for i in client._issues}
        assert byid["P"] == "closed"
        assert byid["c1"] == "closed"
        assert byid["other"] == "open"


@pytest.mark.asyncio
async def test_shift_down_extends_selection():
    issues = [mk("a", status="open", priority=0), mk("b", status="open", priority=1),
              mk("c", status="open", priority=2)]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query(Card).first().focus()
        await pilot.pause()
        await pilot.press("shift+down")
        await pilot.pause()
        assert app.selected == {"a", "b"}
        await pilot.press("shift+down")
        await pilot.pause()
        assert app.selected == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_shift_click_toggles_selection():
    issues = [mk("a", status="open"), mk("b", status="open")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        cb = next(c for c in app.query(Card) if c.issue.id == "b")
        await pilot.click(cb, shift=True)
        await pilot.pause()
        assert app.selected == {"b"}
        await pilot.click(cb, shift=True)
        await pilot.pause()
        assert app.selected == set()


@pytest.mark.asyncio
async def test_defer_acts_on_selection_union_when_present():
    issues = [mk("a", status="open"), mk("b", status="open"), mk("c", status="open")]
    client = FakeClient(issues)
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query(Card).first().focus()  # a
        await pilot.pause()
        await pilot.press("shift+down")  # select a + b
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        byid = {i.id: i.status for i in client._issues}
        assert byid["a"] == "deferred" and byid["b"] == "deferred"
        assert byid["c"] == "open"       # not selected
        assert app.selected == set()     # cleared after applying


@pytest.mark.asyncio
async def test_escape_clears_selection_before_drillin():
    issues = [mk("a", status="open"), mk("b", status="open")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        cb = next(c for c in app.query(Card) if c.issue.id == "b")
        await pilot.click(cb, shift=True)
        await pilot.pause()
        assert app.selected == {"b"}
        await pilot.press("escape")
        await pilot.pause()
        assert app.selected == set()


@pytest.mark.asyncio
async def test_activity_pane_visible_in_all_views():
    # The activity feed is always on now (both non-drilled and drilled).
    from beads_tui.widgets import ActivityPane
    issues = [mk("P", status="open"), mk("c1", status="open", parent="P")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(ActivityPane).display is True
        await _focus(app, pilot, "P")
        await pilot.press("f")
        await pilot.pause()
        assert app.query_one(ActivityPane).display is True


@pytest.mark.asyncio
async def test_focus_issue_moves_highlight_to_that_card():
    issues = [mk("P", status="open"), mk("c1", status="open", parent="P")]
    app = app_with(issues)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _focus(app, pilot, "P")
        await pilot.press("f")
        await pilot.pause()
        app.focus_issue("c1")
        await pilot.pause()
        assert isinstance(app.focused, Card) and app.focused.issue.id == "c1"


@pytest.mark.asyncio
async def test_composing_a_comment_calls_add_comment():
    issues = [mk("gv-1", title="Fix things")]
    client = FakeClient(issues, {"gv-1": []})
    app = BoardApp(client=client, poll_interval=0)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query(Card).first().focus()
        await pilot.pause()
        await pilot.press("enter")      # open detail
        await pilot.pause()
        await pilot.press("c")          # open comment composer
        await pilot.pause()
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("ctrl+s")     # submit
        await pilot.pause()
        assert client.added == [("gv-1", "hello")]
