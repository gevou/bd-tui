"""Layer 3: the Textual application, screens, and key bindings."""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Static, TextArea

from beads_tui.data import (
    BeadsClient,
    BeadsError,
    Comment,
    Issue,
    format_timestamp,
    is_db_open_error,
)
from beads_tui.model import (
    Filters,
    apply_filters,
    children_map,
    comments_newest_first,
    group_issues,
    has_relations,
    latest_comments,
    next_dimension,
    related_ids,
    subtree_ids,
)
from beads_tui.widgets import ActivityPane, STATUS_ICONS, Card, Column, DetailPane

CSS_PATH = Path(__file__).parent / "styles.tcss"


class CommentScreen(ModalScreen):
    """Modal composer for adding a comment to an issue."""

    BINDINGS = [
        ("ctrl+s", "submit", "Save"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, issue_id: str):
        super().__init__()
        self.issue_id = issue_id

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="comment-box"):
            yield Static(f"Comment on {self.issue_id}  (Ctrl+S save, Esc cancel)",
                         id="comment-header")
            yield TextArea(id="comment-input")
            with Horizontal(id="comment-buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#comment-input", TextArea).focus()

    def action_submit(self) -> None:
        text = self.query_one("#comment-input", TextArea).text.strip()
        if not text:
            self.app.notify("Empty comment — not saved.", severity="warning")
            return
        try:
            self.app.client.add_comment(self.issue_id, text)
        except BeadsError as exc:
            self.app.notify(f"Failed to add comment: {exc}", severity="error")
            return
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_submit()
        else:
            self.action_cancel()


class ConfirmScreen(ModalScreen):
    """Yes/No confirmation. Dismisses with True (confirmed) or False."""

    BINDINGS = [
        ("y", "yes", "Yes"),
        ("enter", "yes", "Yes"),
        ("n", "no", "No"),
        ("escape", "no", "No"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="confirm-box"):
            yield Static(self.message, id="confirm-message")
            yield Static("[b]y[/b]es  /  [b]n[/b]o", id="confirm-hint")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id="yes", variant="warning")
                yield Button("No", id="no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class DetailScreen(ModalScreen):
    """Modal read-only detail view: description + comment thread."""

    BINDINGS = [
        ("c", "compose_comment", "Comment"),
        ("escape", "dismiss_detail", "Close"),
    ]

    def __init__(self, issue: Issue, comments: list[Comment]):
        super().__init__()
        self.issue = issue
        self.comments = comments_newest_first(comments)

    def compose(self) -> ComposeResult:
        i = self.issue
        icon = STATUS_ICONS.get(i.status, "?")
        with VerticalScroll(id="detail-box"):
            yield Static(f"{icon} {i.id}  P{i.priority}  {i.status}", id="detail-title")
            yield Static(f"[b]{i.title}[/b]", id="detail-heading")
            if i.labels:
                yield Static(f"labels: {', '.join(i.labels)}", classes="detail-meta")
            yield Static(i.description or "(no description)", id="detail-desc")
            header = f"Comments ({len(self.comments)}, newest first)" if self.comments \
                else "Comments (0)"
            yield Static(header, id="detail-comments-header")
            if not self.comments:
                yield Static("(no comments)", classes="comment")
            for c in self.comments:
                when = format_timestamp(c.created_at)
                yield Static(f"[b]{c.author}[/b]  {when}\n{c.text}", classes="comment")

    def render_comment_texts(self) -> str:
        """Concatenated comment bodies currently shown (used by tests)."""
        return "\n".join(c.text for c in self.comments)

    def action_compose_comment(self) -> None:
        def after(saved: Optional[bool]) -> None:
            if saved:
                self.app.notify("Comment added.")
        self.app.push_screen(CommentScreen(self.issue.id), after)

    def action_dismiss_detail(self) -> None:
        self.dismiss()


class BoardApp(App):
    CSS_PATH = CSS_PATH
    TITLE = "beads-tui"

    BINDINGS = [
        ("g", "cycle_group", "Group by"),
        ("r", "refresh_board", "Refresh"),
        ("f", "focus_subtree", "Drill in"),
        ("d", "defer_subtree", "Defer subtree"),
        ("X", "close_subtree", "Close subtree"),
        ("full_stop", "toggle_inactive", "Show/hide closed+deferred"),
        ("slash", "focus_search", "Search"),
        ("enter", "open_detail", "Open"),
        ("escape", "clear_focus", "Clear drill-in"),
        ("left", "nav('left')", "←"),
        ("right", "nav('right')", "→"),
        ("up", "nav('up')", "↑"),
        ("down", "nav('down')", "↓"),
        Binding("shift+left", "select_nav('left')", "Select ←", show=False),
        Binding("shift+right", "select_nav('right')", "Select →", show=False),
        Binding("shift+up", "select_nav('up')", "Select ↑", show=False),
        Binding("shift+down", "select_nav('down')", "Select ↓", show=False),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, client: Optional[BeadsClient] = None,
                 dimension: str = "status", poll_interval: float = 15.0):
        super().__init__()
        self.client = client or BeadsClient()
        self.dimension = dimension
        self.filters = Filters()
        self.poll_interval = poll_interval
        self.issues: list[Issue] = []
        self.grid: list[list[Card]] = []
        self._last_sig: Optional[tuple] = None
        self._pane_issue_id: Optional[str] = None
        self.selected: set[str] = set()
        self._drill_root: Optional[str] = None
        self._last_card_id: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search id, title, label…  (Esc to leave)", id="search")
        with Horizontal(id="main"):
            yield Horizontal(id="board")
            yield DetailPane()
            yield ActivityPane()
        yield Footer()

    async def on_mount(self) -> None:
        search = self.query_one("#search", Input)
        search.display = False
        search.can_focus = False  # only focusable while actively searching
        self.query_one(DetailPane).display = False
        self.query_one(ActivityPane).display = False
        await self.reload()
        self._restore_focus()
        if self.poll_interval:
            self.set_interval(self.poll_interval, self.poll)

    def _restore_focus(self, prefer_id: Optional[str] = None) -> None:
        """Keep focus on a card (or nowhere) — never on the hidden search box,
        which would otherwise swallow key bindings when the board is empty.

        ``prefer_id`` re-focuses that ticket if it's still present (used across
        rebuilds so a refresh doesn't jump focus back to the first card)."""
        search = self.query_one("#search", Input)
        if search.display:
            return  # user is actively searching
        if prefer_id is not None:
            for cards in self.grid:
                for card in cards:
                    if card.issue.id == prefer_id:
                        card.focus()
                        return
        if isinstance(self.focused, Card):
            return
        if self.grid and self.grid[0]:
            self.grid[0][0].focus()
        else:
            self.set_focus(None)

    # --- data ---------------------------------------------------------------

    def _load_issues(self) -> None:
        try:
            self.issues = self.client.list_issues()
        except BeadsError as exc:
            if is_db_open_error(str(exc)):
                self.notify(
                    "Couldn't open a beads database here. If your DB isn't under "
                    "the current directory, set BEADS_DIR to its .beads folder and "
                    "restart bd-tui.",
                    title="No beads database",
                    severity="error",
                    timeout=15,
                )
            else:
                self.notify(f"bd error: {exc}", severity="error", timeout=8)

    @staticmethod
    def _signature(issues: list[Issue]) -> tuple:
        # Everything the board renders; if unchanged, there's nothing to redraw.
        return tuple(
            (i.id, i.status, i.priority, i.title, i.comment_count,
             tuple(i.labels), i.parent)
            for i in issues
        )

    async def reload(self, force: bool = False) -> None:
        self._load_issues()
        sig = self._signature(self.issues)
        if not force and sig == self._last_sig:
            return  # data unchanged -> skip rebuild so the poll doesn't flicker
        self._last_sig = sig
        await self.rebuild_board()

    def poll(self) -> None:
        self.run_worker(self.reload(), exclusive=True)

    async def rebuild_board(self) -> None:
        board = self.query_one("#board", Horizontal)
        # Restore to the last card the user was on, even if focus has since drifted
        # onto a scroll container (falls back to whatever card is focused now).
        prev = self.focused
        prev_id = self._last_card_id or (prev.issue.id if isinstance(prev, Card) else None)
        await board.remove_children()

        visible = apply_filters(self.issues, self.filters)
        columns = group_issues(visible, self.dimension)
        # Nest only children that are themselves visible (consistent with filters).
        cmap = children_map(visible)

        self.grid = []
        built: list[Column] = []
        for ci, (title, items) in enumerate(columns.items()):
            cards: list[Card] = []
            row = 0
            for issue in items:
                cards.append(Card(issue, ci, row))
                row += 1
                for kid in cmap.get(issue.id, []):
                    cards.append(Card(kid, ci, row, indent=1))
                    row += 1
            self.grid.append(cards)
            built.append(Column(title, cards, ci))

        if built:
            await board.mount(*built)

        # The live detail + activity panes are shown only while drilled in.
        drilled = self.filters.focus_ids is not None
        self.query_one(DetailPane).display = drilled
        self.query_one(ActivityPane).display = drilled
        if drilled:
            group = frozenset(self.filters.focus_ids)
            ids = [i.id for i in self.issues
                   if i.id in group and i.comment_count]
            self.run_worker(self._load_activity(group, ids),
                            exclusive=True, group="activity")

        # Re-apply selection markers to the freshly-built cards.
        self._apply_selection_classes()

        # Keep the previously-focused card highlighted after a rebuild.
        self._restore_focus(prev_id)

    # --- actions ------------------------------------------------------------

    async def action_cycle_group(self) -> None:
        self.dimension = next_dimension(self.dimension)
        await self.rebuild_board()

    async def action_toggle_inactive(self) -> None:
        self.filters.show_inactive = not self.filters.show_inactive
        await self.rebuild_board()

    async def action_refresh_board(self) -> None:
        await self.reload(force=True)

    async def action_focus_subtree(self) -> None:
        card = self.focused
        if not isinstance(card, Card):
            return
        if not has_relations(self.issues, card.issue.id):
            self.notify(f"{card.issue.id} has no descendants or dependencies.")
            return
        self.filters.focus_ids = related_ids(self.issues, card.issue.id)
        self._drill_root = card.issue.id
        await self.rebuild_board()
        self._update_subtitle()

    async def action_clear_focus(self) -> None:
        # Esc clears a selection first, then (on a second press) exits drill-in.
        if self.selected:
            self._clear_selection()
            return
        if self.filters.focus_ids is not None:
            self.filters.focus_ids = None
            self._drill_root = None
            await self.rebuild_board()
            self._update_subtitle()

    # --- multi-selection ----------------------------------------------------

    def toggle_select(self, issue_id: str) -> None:
        self.selected.discard(issue_id) if issue_id in self.selected else self.selected.add(issue_id)
        self._apply_selection_classes()
        self._update_subtitle()

    def action_select_nav(self, direction: str) -> None:
        card = self.focused
        if not isinstance(card, Card):
            self._restore_focus()
            card = self.focused
            if not isinstance(card, Card):
                return
        self.selected.add(card.issue.id)   # keep the origin selected
        target = self._nav_target(card, direction)
        if target is not None:
            self.selected.add(target.issue.id)
            target.focus()
        self._apply_selection_classes()
        self._update_subtitle()

    def _apply_selection_classes(self) -> None:
        for cards in self.grid:
            for card in cards:
                card.set_class(card.issue.id in self.selected, "selected")
                card.refresh()

    def _clear_selection(self) -> None:
        if self.selected:
            self.selected.clear()
            self._apply_selection_classes()
            self._update_subtitle()

    def _update_subtitle(self) -> None:
        parts = []
        if self.filters.focus_ids is not None and self._drill_root:
            parts.append(f"drill-in: {self._drill_root} — Esc to clear")
        if self.selected:
            parts.append(f"{len(self.selected)} selected — d/X to act, Esc to clear")
        self.sub_title = "  |  ".join(parts)

    # --- bulk subtree writes (defer / close) --------------------------------

    def _action_roots(self) -> list[str]:
        if self.selected:
            return list(self.selected)
        card = self.focused
        return [card.issue.id] if isinstance(card, Card) else []

    def _subtree_union(self, roots: list[str]) -> "set[str]":
        ids: "set[str]" = set()
        for r in roots:
            ids |= subtree_ids(self.issues, r)
        return ids

    def _confirm_and_apply(self, targets: list[str], label: str, apply_fn) -> None:
        if not targets:
            self.notify("Nothing to change.")
            return

        def after(ok: Optional[bool]) -> None:
            if not ok:
                return
            try:
                apply_fn(targets)
            except BeadsError as exc:
                self.notify(f"bd error: {exc}", severity="error")
                return
            self.notify(f"{label}: {len(targets)} issue(s).")
            self._clear_selection()
            self.run_worker(self.reload(force=True), exclusive=True)

        self.push_screen(ConfirmScreen(f"{label} {len(targets)} issue(s)?"), after)

    def action_defer_subtree(self) -> None:
        roots = self._action_roots()
        if not roots:
            return
        # Single focused ticket with no selection keeps the defer/reopen toggle.
        if not self.selected and len(roots) == 1:
            root = next((i for i in self.issues if i.id == roots[0]), None)
            if root is not None and root.status == "deferred":
                ids = subtree_ids(self.issues, root.id)
                targets = [i.id for i in self.issues if i.id in ids and i.status == "deferred"]
                self._confirm_and_apply(
                    targets, "Reopen", lambda t: self.client.set_status(t, "open"))
                return
        ids = self._subtree_union(roots)
        targets = [i.id for i in self.issues
                   if i.id in ids and i.status not in ("closed", "deferred")]
        self._confirm_and_apply(targets, "Defer", lambda t: self.client.set_status(t, "deferred"))

    def action_close_subtree(self) -> None:
        roots = self._action_roots()
        if not roots:
            return
        ids = self._subtree_union(roots)
        targets = [i.id for i in self.issues if i.id in ids and i.status != "closed"]
        reason = f"Closed via bd-tui ({len(roots)} subtree(s))"
        self._confirm_and_apply(targets, "Close", lambda t: self.client.close(t, reason))

    def action_focus_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.can_focus = True
        search.focus()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.filters.query = event.value
            await self.rebuild_board()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self._restore_focus()

    def open_detail_for(self, issue: Issue) -> None:
        try:
            comments = self.client.fetch_comments(issue.id)
        except BeadsError as exc:
            self.notify(f"bd error: {exc}", severity="error")
            comments = []
        self.push_screen(DetailScreen(issue, comments))

    def action_open_detail(self) -> None:
        card = self.focused
        if isinstance(card, Card):
            self.open_detail_for(card.issue)

    def on_card_focused(self, message: Card.Focused) -> None:
        # Remember the last card so a refresh can restore it even if focus later
        # drifts onto a scroll container (the detail/activity panes are focusable).
        self._last_card_id = message.issue.id
        # In drill-in mode the right-hand pane mirrors the highlighted card.
        if self.filters.focus_ids is None:
            return
        pane = self.query_one(DetailPane)
        pane.show_issue(message.issue)
        self._pane_issue_id = message.issue.id
        if message.issue.comment_count:
            self.run_worker(
                partial(self._load_pane_comments, message.issue.id),
                exclusive=True, group="pane-comments", thread=True,
            )

    def _load_pane_comments(self, issue_id: str) -> None:
        try:
            comments = self.client.fetch_comments(issue_id)
        except BeadsError:
            comments = []
        self.call_from_thread(self._apply_pane_comments, issue_id, comments)

    def _apply_pane_comments(self, issue_id: str, comments) -> None:
        # Ignore stale results if the user has moved on to another card.
        if self.filters.focus_ids is not None and self._pane_issue_id == issue_id:
            self.query_one(DetailPane).show_comments(comments)

    def focus_issue(self, issue_id: str) -> None:
        """Move the highlight to a specific ticket (used by activity-feed clicks)."""
        for cards in self.grid:
            for card in cards:
                if card.issue.id == issue_id:
                    card.focus()
                    return

    async def _load_activity(self, group: "frozenset[str]", ids: list[str]) -> None:
        """Aggregate comments across the drilled-in group into the activity pane."""
        comment_map: dict = {}
        for iid in ids:
            try:
                comment_map[iid] = await asyncio.to_thread(self.client.fetch_comments, iid)
            except BeadsError:
                continue
        if self.filters.focus_ids != group:
            return  # user changed/left the drill-in group while we were loading
        await self.query_one(ActivityPane).show(latest_comments(comment_map))

    def _nav_target(self, card: Card, direction: str) -> Optional[Card]:
        if not self.grid:
            return None
        col, row = card.col_index, card.row_index
        if direction in ("left", "right"):
            col = max(0, min(len(self.grid) - 1, col + (1 if direction == "right" else -1)))
            row = min(row, len(self.grid[col]) - 1)
        else:
            row = max(0, min(len(self.grid[col]) - 1, row + (1 if direction == "down" else -1)))
        if 0 <= col < len(self.grid) and 0 <= row < len(self.grid[col]):
            return self.grid[col][row]
        return None

    def action_nav(self, direction: str) -> None:
        card = self.focused
        if not isinstance(card, Card) or not self.grid:
            if self.grid and self.grid[0]:
                self.grid[0][0].focus()
            return
        target = self._nav_target(card, direction)
        if target is not None:
            target.focus()
