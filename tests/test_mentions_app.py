"""Interaction tests for the always-on activity feed + @me filter (Layer 3)."""
import json

import pytest

from textual.widgets import Static

from beads_tui.app import BoardApp, DetailScreen
from beads_tui.data import Comment, Issue
from beads_tui.widgets import ActivityItem, ActivityPane, DetailPane


class FakeClient:
    """Serves issues + per-issue comments without touching a real bd DB."""

    def __init__(self, issues, comments=None):
        self._issues = issues
        self._comments = comments or {}

    def list_issues(self):
        return list(self._issues)

    def fetch_comments(self, issue_id):
        return list(self._comments.get(issue_id, []))

    def add_comment(self, issue_id, text):
        pass


def mk(id, comment_count=0, status="open", title="t", parent=None):
    return Issue(id=id, title=title, status=status, comment_count=comment_count,
                 parent=parent)


def _c(cid, issue_id, author, text, created_at):
    return Comment(id=cid, issue_id=issue_id, author=author, text=text,
                   created_at=created_at)


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setenv("MENTION_STATE_FILE", str(path))
    return path


async def _settle(app, pilot):
    """Let the activity worker (bd fetch) finish and render."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _flat_app():
    """Non-nested board: gv-2 (@gv, newest), gv-1 (@george), gv-3 (plain)."""
    issues = [mk("gv-1", comment_count=1), mk("gv-2", comment_count=1),
              mk("gv-3", comment_count=1)]
    comments = {
        "gv-1": [_c("c1", "gv-1", "agent:impl", "@george review this",
                    "2026-08-19T10:00:00Z")],
        "gv-2": [_c("c2", "gv-2", "agent:tester", "@gv ping", "2026-08-19T12:00:00Z")],
        "gv-3": [_c("c3", "gv-3", "agent:lead", "just a status update",
                    "2026-08-19T09:00:00Z")],
    }
    return BoardApp(client=FakeClient(issues, comments), poll_interval=0)


def _rows(app):
    return list(app.query(ActivityItem))


@pytest.mark.asyncio
async def test_activity_pane_populates_in_non_drilled_view(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        # Feed loaded via the worker (no blocking call) — all three comments show.
        assert {r.issue_id for r in _rows(app)} == {"gv-1", "gv-2", "gv-3"}
        # newest-first ordering
        assert [r.issue_id for r in _rows(app)] == ["gv-2", "gv-1", "gv-3"]


@pytest.mark.asyncio
async def test_m_toggles_all_and_me_and_narrows_rows(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        assert app._activity_mode == "all"
        assert len(_rows(app)) == 3
        await pilot.press("m")
        await pilot.pause()
        assert app._activity_mode == "me"
        # @me keeps only the two mention rows.
        assert {r.issue_id for r in _rows(app)} == {"gv-1", "gv-2"}
        header = str(app.query_one("#activity-header").render())
        assert "‹@me›" in header  # active filter marked in the pane header
        await pilot.press("m")
        await pilot.pause()
        assert app._activity_mode == "all"
        assert len(_rows(app)) == 3


@pytest.mark.asyncio
async def test_unread_mention_rows_are_styled_plain_rows_are_not(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        by_id = {r.issue_id: r for r in _rows(app)}
        # mentions unread -> both mention + unread classes
        assert by_id["gv-1"].has_class("mention") and by_id["gv-1"].has_class("unread")
        assert by_id["gv-2"].has_class("mention") and by_id["gv-2"].has_class("unread")
        # plain comment -> neither
        assert not by_id["gv-3"].has_class("mention")
        assert not by_id["gv-3"].has_class("unread")


@pytest.mark.asyncio
async def test_each_row_has_a_header_line_and_unread_marker_only_on_unread(state_file):
    # Every row carries a colored header/meta line (separation); the ● unread
    # marker is only on unread mentions, not on read/plain rows.
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        by_id = {r.issue_id: r for r in _rows(app)}
        for row in by_id.values():
            header = str(row.query_one(".activity-line", Static).render())
            assert row.issue_id in header  # header/meta line present on every row
        assert "●" in str(by_id["gv-2"].query_one(".activity-line", Static).render())
        assert "●" in str(by_id["gv-1"].query_one(".activity-line", Static).render())
        assert "●" not in str(by_id["gv-3"].query_one(".activity-line", Static).render())


@pytest.mark.asyncio
async def test_marking_read_removes_unread_marker_from_header(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        row = next(r for r in _rows(app) if r.issue_id == "gv-2")
        assert "●" in str(row.query_one(".activity-line", Static).render())
        await pilot.click(row)
        await pilot.pause()
        assert "●" not in str(row.query_one(".activity-line", Static).render())


@pytest.mark.asyncio
async def test_read_mention_is_dimmed_not_unread(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        # Pre-mark gv-1's comment read via the shared A key, then reload the
        # feed so the persisted read-state re-applies.
        rbc = app._ensure_rbc()
        key = next(r for r in _rows(app) if r.issue_id == "gv-1").entry.mention_key
        rbc.mark_read(key)
        app._refresh_activity()
        await _settle(app, pilot)
        gv1 = next(r for r in _rows(app) if r.issue_id == "gv-1")
        assert gv1.has_class("mention") is True
        assert gv1.has_class("unread") is False


@pytest.mark.asyncio
async def test_activating_mention_opens_modal_and_marks_read_non_drilled(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        row = next(r for r in _rows(app) if r.issue_id == "gv-2")
        key = row.entry.mention_key
        await pilot.click(row)
        await pilot.pause()
        # Non-drilled activation opens the thread as a modal for the right issue.
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.issue.id == "gv-2"
        # Marking read is a SIDE EFFECT: state file written, row flipped.
        saved = json.loads(state_file.read_text())
        assert key in saved["read_keys"]
        assert row.entry.unread is False
        assert row.has_class("unread") is False


@pytest.mark.asyncio
async def test_enter_activates_focused_row(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        row = next(r for r in _rows(app) if r.issue_id == "gv-1")
        row.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.issue.id == "gv-1"


@pytest.mark.asyncio
async def test_badge_reflects_unread_and_decrements_after_read(state_file):
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        assert "@you: 2" in app.title
        row = next(r for r in _rows(app) if r.issue_id == "gv-2")
        await pilot.click(row)
        await pilot.pause()
        assert "@you: 1" in app.title


@pytest.mark.asyncio
async def test_activating_in_drill_mirrors_into_detail_pane_and_marks_read(state_file):
    issues = [mk("P", comment_count=1), mk("c1", comment_count=1, parent="P"),
              mk("other", comment_count=1)]
    comments = {
        "P": [_c("cp", "P", "agent:impl", "parent note", "2026-08-19T09:00:00Z")],
        "c1": [_c("cc", "c1", "agent:tester", "@george on the child",
                  "2026-08-19T11:00:00Z")],
        "other": [_c("co", "other", "agent:lead", "@gv elsewhere",
                     "2026-08-19T12:00:00Z")],
    }
    app = BoardApp(client=FakeClient(issues, comments), poll_interval=0)
    # A realistic terminal width: the feed pane is a fixed 20%, so at the tiny
    # default 80-col size it's too narrow for a headless click to land reliably.
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        # drill into P (group {P, c1}); "other" drops out of the feed
        p_card = next(c for c in app.query("Card") if getattr(c, "issue", None)
                      and c.issue.id == "P")
        p_card.focus()
        await pilot.pause()
        await pilot.press("f")
        await _settle(app, pilot)
        ids = {r.issue_id for r in _rows(app)}
        assert "other" not in ids
        row = next(r for r in _rows(app) if r.issue_id == "c1")
        key = row.entry.mention_key
        await pilot.click(row)
        await pilot.pause()
        # Drilled: no modal — the side DetailPane mirrors the issue instead.
        assert not isinstance(app.screen, DetailScreen)
        assert app.query_one(DetailPane).issue.id == "c1"
        # Still marks read as a side effect.
        saved = json.loads(state_file.read_text())
        assert key in saved["read_keys"]
        assert row.has_class("unread") is False


@pytest.mark.asyncio
async def test_me_includes_self_authored_as_plain_row(state_file):
    # @me = mentions-of-me ∪ my-own-comments; George's own comment shows plainly.
    issues = [mk("gv-1", comment_count=1), mk("gv-9", comment_count=1)]
    comments = {
        "gv-1": [_c("c1", "gv-1", "agent:impl", "@george please look",
                    "2026-08-19T10:00:00Z")],
        "gv-9": [_c("c9", "gv-9", "George Voulgaris", "ARTIFACT-SYNC ROUTINE done",
                    "2026-08-19T13:00:00Z")],
    }
    app = BoardApp(client=FakeClient(issues, comments), poll_interval=0)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("m")
        await pilot.pause()
        by_id = {r.issue_id: r for r in _rows(app)}
        assert set(by_id) == {"gv-1", "gv-9"}
        # my own comment: plain row, not a mention, not unread, no read-state key
        assert not by_id["gv-9"].has_class("mention")
        assert not by_id["gv-9"].has_class("unread")
        assert by_id["gv-9"].entry.mention_key is None
        assert by_id["gv-9"].entry.self_authored is True
        # the mention still gets the unread treatment
        assert by_id["gv-1"].has_class("unread")
        # badge counts only the true unread mention, not my own comment
        assert "@you: 1" in app.title


@pytest.mark.asyncio
async def test_me_spans_closed_beads_without_show_inactive(state_file):
    issues = [mk("gv-open", comment_count=1, status="open"),
              mk("gv-closed", comment_count=1, status="closed")]
    comments = {
        "gv-open": [_c("co", "gv-open", "agent:impl", "@george on the open one",
                       "2026-08-19T10:00:00Z")],
        "gv-closed": [_c("cc", "gv-closed", "agent:lead", "@gv on the closed one",
                         "2026-08-19T11:00:00Z")],
    }
    app = BoardApp(client=FakeClient(issues, comments), poll_interval=0)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        # all-mode mirrors the visible board -> the closed bead is hidden.
        assert {r.issue_id for r in _rows(app)} == {"gv-open"}
        await pilot.press("m")
        await pilot.pause()
        # @me spans the whole DB -> the closed bead's mention appears without `.`.
        assert {r.issue_id for r in _rows(app)} == {"gv-open", "gv-closed"}


@pytest.mark.asyncio
async def test_activating_self_comment_opens_thread_without_marking_read(state_file):
    issues = [mk("gv-9", comment_count=1)]
    comments = {"gv-9": [_c("c9", "gv-9", "George Voulgaris", "my own note",
                            "2026-08-19T13:00:00Z")]}
    app = BoardApp(client=FakeClient(issues, comments), poll_interval=0)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("m")
        await pilot.pause()
        row = next(r for r in _rows(app) if r.issue_id == "gv-9")
        await pilot.click(row)
        await pilot.pause()
        # Opening still works for my own comment...
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.issue.id == "gv-9"
        # ...but there's nothing to mark read: no state file write happened.
        assert not state_file.exists() or json.loads(state_file.read_text())["read_keys"] == []


@pytest.mark.asyncio
async def test_activating_closed_bead_mention_from_me_opens_modal_and_marks_read(state_file):
    issues = [mk("gv-open", comment_count=1, status="open"),
              mk("gv-closed", comment_count=1, status="closed")]
    comments = {
        "gv-open": [_c("co", "gv-open", "agent:impl", "@george open",
                       "2026-08-19T10:00:00Z")],
        "gv-closed": [_c("cc", "gv-closed", "agent:lead", "@gv closed",
                         "2026-08-19T11:00:00Z")],
    }
    app = BoardApp(client=FakeClient(issues, comments), poll_interval=0)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("m")
        await pilot.pause()
        row = next(r for r in _rows(app) if r.issue_id == "gv-closed")
        key = row.entry.mention_key
        await pilot.click(row)
        await pilot.pause()
        # Off-board (closed) bead reached via @me still opens its thread as a modal.
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.issue.id == "gv-closed"
        saved = json.loads(state_file.read_text())
        assert key in saved["read_keys"]
        assert row.has_class("unread") is False


@pytest.mark.asyncio
async def test_me_filter_reports_error_when_source_missing(state_file, monkeypatch):
    monkeypatch.setenv("RBC_SOURCE", "/nonexistent/recent-bead-comments.py")
    app = _flat_app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        assert app._mentions_error is not None
        # all mode still lists the plain rows (no mention annotation)
        assert len(_rows(app)) == 3
        await pilot.press("m")
        await pilot.pause()
        # @me surfaces the unavailability rather than crashing; no rows.
        assert len(_rows(app)) == 0
        header = str(app.query_one(ActivityPane).query_one("#activity-header").render())
        assert "‹@me›" in header
