"""Tests for the @mention adapter (Layer 2.5) and its reuse of the A-phase
`recent-bead-comments.py` contract.

These tests exercise the pure adapter that imports the CLI module by path so the
`mention_key` algorithm and the shared state file stay identical between the CLI
(`recent-bead-comments --mentions`) and the bd-tui activity feed's `@me` filter.
"""
import json
from pathlib import Path

import pytest

from beads_tui.data import Comment
from beads_tui.mentions import (
    MentionsUnavailable,
    annotate_activity,
    comment_to_dict,
    load_rbc,
)


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Point the shared mention-state file at a throwaway path."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("MENTION_STATE_FILE", str(path))
    return path


def _c(cid, issue_id, author, text, created_at):
    return Comment(id=cid, issue_id=issue_id, author=author, text=text,
                   created_at=created_at)


def _flat(*comments):
    """Newest-first (issue_id, Comment) feed, mirroring latest_comments()."""
    ordered = sorted(comments, key=lambda c: c.created_at or "", reverse=True)
    return [(c.issue_id, c) for c in ordered]


def test_load_rbc_resolves(state_file):
    rbc = load_rbc()
    assert hasattr(rbc, "detect_mentions")
    assert hasattr(rbc, "mention_key")
    assert hasattr(rbc, "mark_read")


def test_load_rbc_missing_source_raises(monkeypatch):
    monkeypatch.setenv("RBC_SOURCE", "/nonexistent/path/recent-bead-comments.py")
    with pytest.raises(MentionsUnavailable):
        load_rbc()


def test_annotate_flags_mentions_and_preserves_order(state_file):
    rbc = load_rbc()
    flat = _flat(
        _c("c1", "gv-1", "agent:impl", "hey @george look at this",
           "2026-08-19T10:00:00Z"),
        _c("c2", "gv-3", "agent:lead", "no handle here", "2026-08-19T09:00:00Z"),
        _c("c3", "gv-2", "agent:tester", "ping @gv please", "2026-08-19T12:00:00Z"),
    )
    entries = annotate_activity(rbc, flat)
    # Every row is present (newest first); only the two with a handle are mentions.
    assert [e.issue_id for e in entries] == ["gv-2", "gv-1", "gv-3"]
    by_id = {e.issue_id: e for e in entries}
    assert by_id["gv-2"].is_mention and by_id["gv-2"].unread
    assert by_id["gv-1"].is_mention and by_id["gv-1"].unread
    assert not by_id["gv-3"].is_mention
    # Keys must match the CLI's algorithm exactly (shared read-state).
    gv1_comment = next(c for _, c in flat if c.issue_id == "gv-1")
    assert by_id["gv-1"].mention_key == rbc.mention_key(comment_to_dict(gv1_comment))


def test_annotate_respects_prior_read_state(state_file):
    rbc = load_rbc()
    c1 = _c("c1", "gv-1", "agent:impl", "hey @george", "2026-08-19T10:00:00Z")
    c2 = _c("c2", "gv-2", "agent:lead", "also @george", "2026-08-19T11:00:00Z")
    read_key = rbc.mention_key(comment_to_dict(c1))
    state_file.write_text(json.dumps({"read_keys": [read_key],
                                      "last_notified_count": 0}))
    entries = {e.issue_id: e for e in annotate_activity(rbc, _flat(c1, c2))}
    assert entries["gv-1"].is_mention and entries["gv-1"].unread is False
    assert entries["gv-2"].is_mention and entries["gv-2"].unread is True


def test_annotate_excludes_self_authored_from_mentions(state_file):
    rbc = load_rbc()
    flat = _flat(
        _c("c1", "gv-1", "George Voulgaris", "note to @gv self",
           "2026-08-19T10:00:00Z"),
        _c("c2", "gv-2", "agent:impl", "@george please review",
           "2026-08-19T11:00:00Z"),
    )
    entries = {e.issue_id: e for e in annotate_activity(rbc, flat)}
    # George mentioning himself is not a mention (still shown as a plain row).
    assert entries["gv-1"].is_mention is False
    assert entries["gv-2"].is_mention is True


def test_annotate_marks_self_authored_rows(state_file):
    rbc = load_rbc()
    flat = _flat(
        _c("c1", "gv-1", "George Voulgaris", "my own status note",
           "2026-08-19T10:00:00Z"),
        _c("c2", "gv-2", "agent:impl", "@george please review",
           "2026-08-19T11:00:00Z"),
        _c("c3", "gv-3", "agent:lead", "unrelated", "2026-08-19T09:00:00Z"),
    )
    by_id = {e.issue_id: e for e in annotate_activity(rbc, flat)}
    # My own comment: self-authored, not a mention, but part of "involves me".
    assert by_id["gv-1"].self_authored is True
    assert by_id["gv-1"].is_mention is False
    assert by_id["gv-1"].involves_me is True
    # A mention of me: involves me via the mention path.
    assert by_id["gv-2"].is_mention and by_id["gv-2"].involves_me
    # Unrelated agent comment: neither.
    assert by_id["gv-3"].self_authored is False
    assert by_id["gv-3"].involves_me is False


def test_mark_read_writes_shared_state_file(state_file):
    rbc = load_rbc()
    c1 = _c("c1", "gv-1", "agent:impl", "@george hi", "2026-08-19T10:00:00Z")
    entry = annotate_activity(rbc, _flat(c1))[0]
    rbc.mark_read(entry.mention_key)
    saved = json.loads(Path(state_file).read_text())
    assert entry.mention_key in saved["read_keys"]
