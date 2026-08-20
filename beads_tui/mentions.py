"""Layer 2.5: adapter to the A-phase @mention contract.

The pure @mention logic (detection, the stable per-comment key, and the shared
read-state file) already lives in the CLI script ``recent-bead-comments.py``.
We import that module *by path* and call its functions directly rather than
re-implementing them, so the ``mention_key`` algorithm and the on-disk state
file stay byte-for-byte identical between the CLI (``recent-bead-comments
--mentions``) and this TUI's Mentions view. Reusing the code — not copying it —
is what keeps the two front-ends in sync.

The source path is env-overridable via ``RBC_SOURCE`` (mainly for tests); a
moved/missing source yields a clear :class:`MentionsUnavailable` rather than an
import crash on launch.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Optional

from beads_tui.data import Comment

DEFAULT_RBC_SOURCE = os.path.expanduser(
    "~/dev-in-docker-shared-files/workflow/bin/recent-bead-comments.py"
)


class MentionsUnavailable(Exception):
    """Raised when the A-phase mention module cannot be located or loaded."""


def _rbc_source() -> str:
    return os.environ.get("RBC_SOURCE") or DEFAULT_RBC_SOURCE


def load_rbc():
    """Import ``recent-bead-comments.py`` by path.

    Returns the loaded module (exposing ``detect_mentions``, ``mention_key``,
    ``load_mention_state``, ``mark_read`` …). Raises :class:`MentionsUnavailable`
    if the source is missing or fails to import — callers surface that in-app
    instead of crashing.
    """
    path = _rbc_source()
    if not os.path.exists(path):
        raise MentionsUnavailable(f"mention source not found: {path}")
    try:
        spec = importlib.util.spec_from_file_location("recent_bead_comments", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — any import failure -> clear in-app error
        raise MentionsUnavailable(f"could not load mention module: {exc}") from exc
    return module


@dataclass
class ActivityEntry:
    """One row in the always-on activity feed.

    ``mention_key`` is set (the shared A-phase read-state key, ``id:<uuid>``)
    iff this comment is an @mention of George; ``unread`` is only meaningful for
    mention rows. ``self_authored`` marks George's own comments (A's detector
    excludes those from mentions, but the ``@me`` view still surfaces them as
    plain rows). Non-mention rows carry ``mention_key=None`` and render plainly.
    """

    issue_id: str
    comment: Comment
    mention_key: Optional[str] = None
    unread: bool = False
    self_authored: bool = False

    @property
    def is_mention(self) -> bool:
        return self.mention_key is not None

    @property
    def involves_me(self) -> bool:
        """True for the ``@me`` view: George is mentioned OR wrote the comment."""
        return self.is_mention or self.self_authored


def comment_to_dict(comment: Comment) -> dict:
    """Adapt a bd-tui :class:`Comment` to the plain dict the A functions expect."""
    return {
        "id": comment.id,
        "issue_id": comment.issue_id,
        "author": comment.author,
        "text": comment.text,
        "created_at": comment.created_at,
    }


def annotate_activity(rbc, flat: list[tuple[str, Comment]]) -> list[ActivityEntry]:
    """Turn a newest-first ``[(issue_id, Comment), …]`` feed into ActivityEntry
    rows, tagging the ones that are @mentions (via A's ``detect_mentions``) with
    their shared ``mention_key`` and unread state.

    ``rbc`` is the module returned by :func:`load_rbc`. Reusing A's detector +
    key here keeps the ``@me`` filter byte-for-byte consistent with
    ``recent-bead-comments --mentions``.
    """
    dicts = [comment_to_dict(c) for _, c in flat]
    mention_keys = {rbc.mention_key(d) for d in rbc.detect_mentions(dicts)}
    read_keys = set(rbc.load_mention_state()["read_keys"])
    # A's own notion of "George writing as himself" (no agent: prefix). Reused
    # from the shared module so we don't hardcode a second copy of the name.
    self_author = rbc.SELF_AUTHOR
    entries: list[ActivityEntry] = []
    for issue_id, comment in flat:
        key = rbc.mention_key(comment_to_dict(comment))
        is_mention = key in mention_keys
        author = comment.author or ""
        is_self = (not author.startswith("agent:")) and author.strip() == self_author
        entries.append(
            ActivityEntry(
                issue_id=issue_id,
                comment=comment,
                mention_key=key if is_mention else None,
                unread=(key not in read_keys) if is_mention else False,
                self_authored=is_self,
            )
        )
    return entries
