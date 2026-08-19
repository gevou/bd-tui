"""Layer 3 widgets: Card and Column."""
from __future__ import annotations

from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from beads_tui.data import Comment, Issue, format_timestamp
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
        i = self.issue
        icon = STATUS_ICONS.get(i.status, "?")
        pad = "  " * self.indent
        mark = "◉ " if self.has_class("selected") else "  "  # 2-col selection gutter
        labels = f"  [{', '.join(i.labels)}]" if i.labels else ""
        title = i.title if len(i.title) <= 52 else i.title[:51] + "…"
        return f"{mark}{pad}{icon} {i.id}  P{i.priority}  {title}{_fmt_estimate(i.estimated_minutes)}{labels}"


class Column(VerticalScroll):
    """A kanban column: a titled, scrollable stack of cards."""

    def __init__(self, title: str, cards: list[Card], col_index: int):
        super().__init__(classes="column")
        self.title = title
        self.cards = cards
        self.count = len(cards)
        self.col_index = col_index
        self.border_title = f"{title} ({self.count})"

    def compose(self):
        yield from self.cards


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


class ActivityItem(Static):
    """A clickable row in the activity feed; clicking jumps focus to its issue."""

    def __init__(self, issue_id: str, comment: Comment):
        super().__init__(classes="activity-item")
        self.issue_id = issue_id
        self.comment = comment

    def render(self):
        c = self.comment
        when = format_timestamp(c.created_at)
        snippet = " ".join(c.text.split())
        if len(snippet) > 64:
            snippet = snippet[:63] + "…"
        return f"{when}  [b]{self.issue_id}[/b]\n{c.author}: {snippet}"

    def on_click(self) -> None:
        self.app.focus_issue(self.issue_id)


class ActivityPane(VerticalScroll):
    """Right-most pane in drill-in: latest comments across the focused group."""

    def __init__(self):
        super().__init__(id="activity-pane")

    def compose(self):
        yield Static("Recent activity", id="activity-header")
        yield VerticalScroll(id="activity-list")

    async def show(self, entries: "list[tuple[str, Comment]]") -> None:
        box = self.query_one("#activity-list", VerticalScroll)
        await box.remove_children()
        if not entries:
            await box.mount(Static("(no comments in this group)", classes="activity-item"))
            return
        await box.mount(*[ActivityItem(iid, c) for iid, c in entries])
