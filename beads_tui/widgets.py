"""Layer 3 widgets: Card and Column."""
from __future__ import annotations

from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from beads_tui.data import Comment, Issue, format_timestamp
from beads_tui.mentions import ActivityEntry
from beads_tui.model import comments_newest_first

STATUS_ICONS = {
    "open": "○",         # ○
    "in_progress": "◐",  # ◐
    "blocked": "●",      # ●
    "deferred": "❄",     # ❄
    "closed": "✓",       # ✓
}

PRIORITY_CLASS = {0: "p0", 1: "p1", 2: "p2", 3: "p3", 4: "p4"}


def _fmt_estimate(minutes) -> str:
    if not minutes:
        return ""
    if minutes >= 60:
        return f"  ~{minutes // 60}h"
    return f"  ~{minutes}m"


def card_line(issue: Issue, indent: int = 0, selected: bool = False) -> str:
    """The single-line rendering of an issue on the board.

    Shared by ``Card`` and ``ContextRow`` so a dimmed context ancestor is
    visually identical to a real card (same glyph, id, priority, title, indent),
    differing only by the dim CSS class."""
    icon = STATUS_ICONS.get(issue.status, "?")
    pad = "  " * indent
    mark = "◉ " if selected else "  "  # 2-col selection gutter
    labels = f"  [{', '.join(issue.labels)}]" if issue.labels else ""
    title = issue.title if len(issue.title) <= 52 else issue.title[:51] + "…"
    return (f"{mark}{pad}{icon} {issue.id}  P{issue.priority}  "
            f"{title}{_fmt_estimate(issue.estimated_minutes)}{labels}")


class Card(Static):
    """A single focusable issue card."""

    can_focus = True

    class Focused(Message):
        """Posted when a card gains focus (drives the live detail pane)."""

        def __init__(self, issue: Issue):
            self.issue = issue
            super().__init__()

    def on_focus(self) -> None:
        self.post_message(self.Focused(self.issue))

    # Bound on the card itself so they take precedence over the ancestor
    # VerticalScroll's scroll bindings — arrows move the highlight, never scroll.
    BINDINGS = [
        ("up", "nav('up')", "↑"),
        ("down", "nav('down')", "↓"),
        ("left", "nav('left')", "←"),
        ("right", "nav('right')", "→"),
    ]

    def action_nav(self, direction: str) -> None:
        self.app.action_nav(direction)

    def on_click(self, event) -> None:
        # Shift+click toggles multi-selection; a plain click focuses + opens detail.
        if getattr(event, "shift", False):
            self.app.toggle_select(self.issue.id)
        else:
            self.focus()
            self.app.open_detail_for(self.issue)

    def __init__(self, issue: Issue, col_index: int, row_index: int, indent: int = 0):
        super().__init__(classes="card")
        self.issue = issue
        self.col_index = col_index
        self.row_index = row_index
        self.indent = indent
        self.add_class(PRIORITY_CLASS.get(issue.priority, "p2"))

    def render(self):
        return card_line(self.issue, self.indent, self.has_class("selected"))


class ContextRow(Static):
    """A dimmed, non-interactive ancestor row in an active-status column.

    Renders with the SAME ``card_line`` as a real card so the active-status
    column reads as a tree visually identical to ``open`` — the only difference
    is the dim (``context-row``) style. It gives active child cards their parent
    context without being a real card: not focusable, not part of the navigation
    grid, and not counted in the column's ticket total.
    """

    can_focus = False

    def __init__(self, issue: Issue, indent: int = 0):
        super().__init__(classes="card context-row")
        self.issue = issue
        self.indent = indent

    def render(self):
        return card_line(self.issue, self.indent)


class Column(VerticalScroll):
    """A kanban column: a titled, scrollable stack of rows.

    ``rows`` may interleave real ``Card`` widgets with dimmed ``ContextRow``
    ancestor headers. ``cards`` exposes just the real cards (for navigation and
    the ticket-count badge); context rows are display-only.
    """

    def __init__(self, title: str, rows: "list[Card | ContextRow]", col_index: int):
        super().__init__(classes="column")
        self.title = title
        self.rows = rows
        self.cards = [r for r in rows if isinstance(r, Card)]
        self.count = len(self.cards)
        self.col_index = col_index
        self.border_title = f"{title} ({self.count})"

    def compose(self):
        yield from self.rows


class DetailPane(VerticalScroll):
    """Persistent right-hand pane showing the focused ticket (drill-in mode).

    Issue fields update instantly from the already-loaded Issue; comments are
    filled in separately by the app (a bd call) so navigation stays responsive.
    """

    def __init__(self):
        super().__init__(id="detail-pane")
        self.issue: "Issue | None" = None

    def compose(self):
        yield Static(id="pane-title")
        yield Static(id="pane-heading")
        yield Static("", id="pane-labels")
        yield Static(id="pane-desc")
        yield Static(id="pane-comments-header")
        yield Static("", id="pane-comments")

    def show_issue(self, issue: Issue) -> None:
        self.issue = issue
        icon = STATUS_ICONS.get(issue.status, "?")
        self.query_one("#pane-title", Static).update(
            f"{icon} {issue.id}  P{issue.priority}  {issue.status}")
        self.query_one("#pane-heading", Static).update(f"[b]{issue.title}[/b]")
        self.query_one("#pane-labels", Static).update(
            f"labels: {', '.join(issue.labels)}" if issue.labels else "")
        self.query_one("#pane-desc", Static).update(issue.description or "(no description)")
        self.query_one("#pane-comments-header", Static).update(
            f"Comments ({issue.comment_count})")
        self.query_one("#pane-comments", Static).update("loading…" if issue.comment_count else "")
        self.scroll_home(animate=False)

    def show_comments(self, comments: "list[Comment]") -> None:
        if not comments:
            self.query_one("#pane-comments", Static).update("(no comments)")
            return
        ordered = comments_newest_first(comments)
        self.query_one("#pane-comments-header", Static).update(
            f"Comments ({len(ordered)}, newest first)")
        blocks = [f"[b]{c.author}[/b]  {format_timestamp(c.created_at)}\n{c.text}"
                  for c in ordered]
        self.query_one("#pane-comments", Static).update("\n\n".join(blocks))


# A comment longer than this is trimmed in the feed; the full text lives in the
# thread you reach by activating the row.
_ACTIVITY_PREVIEW_CHARS = 220


class ActivityItem(Vertical):
    """A focusable activity-feed row: a colored header/meta line above the body.

    The header line (issue-id · author · time) carries a distinct background so
    every message's start is obvious. Activating the row (Enter when focused, or
    a click) opens the bead's thread and, if it's an unread @mention, marks it
    read. Unread mentions still stand out via the ``●`` marker + bold + a
    stronger (warning) header accent; read mentions and plain rows use the
    normal header accent.
    """

    can_focus = True

    BINDINGS = [("enter", "activate", "Open")]

    def __init__(self, entry: ActivityEntry):
        super().__init__(classes="activity-item")
        self.entry = entry
        self.issue_id = entry.issue_id
        self.comment = entry.comment
        if entry.is_mention:
            self.add_class("mention")
        self.set_class(entry.is_mention and entry.unread, "unread")

    def compose(self):
        yield Static(self._header_line(), classes="activity-line", markup=False)
        yield Static(self._body_text(), classes="activity-body", markup=False)

    def _header_line(self) -> str:
        c = self.comment
        marker = "● " if self.has_class("unread") else ""
        when = format_timestamp(c.created_at)
        return f"{marker}{self.issue_id}  ·  {c.author}  ·  {when}"

    def _body_text(self) -> str:
        # Whitespace-normalised but NOT collapsed to one line — the pane wraps it
        # so a fuller preview is visible without opening the thread.
        text = " ".join((self.comment.text or "").split())
        if len(text) > _ACTIVITY_PREVIEW_CHARS:
            text = text[: _ACTIVITY_PREVIEW_CHARS - 1] + "…"
        return text

    def mark_read(self) -> None:
        """Flip this row to the read style (state persistence is the app's job)."""
        self.entry.unread = False
        self.set_class(False, "unread")
        # Re-render the header so the ● marker disappears with the unread class.
        self.query_one(".activity-line", Static).update(self._header_line())
        self.refresh()

    def action_activate(self) -> None:
        self.app.activate_activity(self)

    def on_click(self) -> None:
        self.focus()
        self.app.activate_activity(self)


class ActivityPane(VerticalScroll):
    """Always-on right-hand feed of recent activity across the visible board.

    A header shows the current filter — ``RECENT ACTIVITY   [ all | @me ]`` —
    where ``@me`` narrows the feed to @mentions of George.
    """

    def __init__(self):
        super().__init__(id="activity-pane")

    def compose(self):
        # markup disabled so the literal [ … ] header brackets aren't parsed as
        # Rich console markup.
        yield Static(self.header_text("all"), id="activity-header", markup=False)
        yield VerticalScroll(id="activity-list")

    @staticmethod
    def header_text(mode: str) -> str:
        # Active filter is wrapped in guillemets (‹ ›); avoids Rich markup tags.
        all_s = "‹all›" if mode == "all" else "all"
        me_s = "‹@me›" if mode == "me" else "@me"
        return f"RECENT ACTIVITY   [ {all_s} | {me_s} ]"

    async def show(
        self, entries: "list[ActivityEntry]", mode: str = "all",
        error: "str | None" = None,
    ) -> None:
        self.query_one("#activity-header", Static).update(self.header_text(mode))
        box = self.query_one("#activity-list", VerticalScroll)
        await box.remove_children()
        if mode == "me" and error:
            await box.mount(
                Static(f"[b]@me unavailable:[/b] {error}", classes="activity-empty"))
            return
        if not entries:
            empty = "(no @mentions in view)" if mode == "me" else "(no recent activity)"
            await box.mount(Static(empty, classes="activity-empty"))
            return
        await box.mount(*[ActivityItem(e) for e in entries])
