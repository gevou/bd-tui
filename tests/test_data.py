"""Tests for the bd subprocess boundary (Layer 1)."""
from dataclasses import replace

import pytest

from datetime import timedelta, timezone

from beads_tui.data import (
    BeadsClient,
    BeadsError,
    Comment,
    Issue,
    format_timestamp,
    is_db_open_error,
    parse_comments,
    parse_issues,
)


class FakeRun:
    """Records the args it was called with and returns a canned result."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []

    def __call__(self, args, env):
        self.calls.append(args)
        self.envs.append(env)

        class Result:
            pass

        r = Result()
        r.stdout = self.stdout
        r.stderr = self.stderr
        r.returncode = self.returncode
        return r


# --- parse_issues ---------------------------------------------------------

def test_parse_issues_reads_json_after_warning_prefix(list_sample_json):
    # bd prints the dolt_server_port deprecation warning before the JSON array.
    noisy = "Warning: dolt_server_port in metadata.json is deprecated\n" + list_sample_json
    issues = parse_issues(noisy)
    assert len(issues) == 8
    assert all(isinstance(i, Issue) for i in issues)


def test_parse_issues_maps_fields(list_sample_json):
    issues = {i.id: i for i in parse_issues(list_sample_json)}
    epic = issues["gv-11929"]
    assert epic.issue_type == "epic"
    assert epic.status == "open"
    assert epic.priority == 0
    assert epic.parent is None
    child = issues["gv-11929.4"]
    assert child.parent == "gv-11929"


def test_parse_issues_null_labels_becomes_empty_list(list_sample_json):
    issues = parse_issues(list_sample_json)
    # fixture issues all have labels: null -> must normalize to []
    assert all(i.labels == [] for i in issues)


def test_parse_issues_raises_when_no_json():
    with pytest.raises(BeadsError):
        parse_issues("Error: something went wrong, no array here")


# --- parse_comments -------------------------------------------------------

def test_parse_comments_maps_fields(comments_sample_json):
    comments = parse_comments(comments_sample_json)
    assert len(comments) == 3
    first = comments[0]
    assert isinstance(first, Comment)
    assert first.issue_id == "gv-crl"
    assert first.author == "Ada Lovelace"
    assert first.created_at == "2026-08-09T08:24:16Z"
    assert first.text.startswith("First comment")


def test_parse_comments_empty(comments_empty_json):
    assert parse_comments(comments_empty_json) == []


# --- format_timestamp -----------------------------------------------------

def test_format_timestamp_converts_utc_to_target_tz():
    # 08:24 UTC is 01:24 at UTC-7
    out = format_timestamp("2026-08-09T08:24:16Z", tz=timezone(timedelta(hours=-7)))
    assert out.startswith("2026-08-09 01:24")


def test_format_timestamp_handles_empty():
    assert format_timestamp("") == ""
    assert format_timestamp(None) == ""


def test_format_timestamp_passes_through_unparseable():
    assert format_timestamp("not a date") == "not a date"


# --- is_db_open_error ------------------------------------------------------

def test_is_db_open_error_detects_missing_database():
    msg = ('failed to open database: embeddeddolt: init schema: '
           'open /src/.beads/embeddeddolt/beads/.dolt/repo_state.json: '
           'no such file or directory')
    assert is_db_open_error(msg) is True


def test_is_db_open_error_detects_no_config():
    assert is_db_open_error("no beads configuration found in /src/.beads") is True


def test_is_db_open_error_false_for_other_errors():
    assert is_db_open_error("comments add: some validation error") is False


# --- BeadsClient (uses an injected runner, the real external boundary) ----

def test_no_beads_dir_leaves_env_unset_for_auto_discovery(monkeypatch, list_sample_json):
    # With no BEADS_DIR configured, bd-tui must NOT force one — bd auto-discovers
    # from the cwd like normal.
    monkeypatch.delenv("BEADS_DIR", raising=False)
    run = FakeRun(stdout=list_sample_json)
    client = BeadsClient(bd_bin="/x/bd", beads_dir=None, run=run)
    client.list_issues()
    assert "BEADS_DIR" not in run.envs[0]


def test_beads_dir_from_env_is_pinned(monkeypatch, list_sample_json):
    monkeypatch.setenv("BEADS_DIR", "/from/env")
    run = FakeRun(stdout=list_sample_json)
    client = BeadsClient(bd_bin="/x/bd", run=run)  # picks up env
    client.list_issues()
    assert run.envs[0]["BEADS_DIR"] == "/from/env"


def test_list_issues_invokes_bd_list_with_beads_dir_pinned(list_sample_json):
    run = FakeRun(stdout=list_sample_json)
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    issues = client.list_issues()
    assert len(issues) == 8
    args = run.calls[0]
    assert args[0] == "/x/bd"
    assert "list" in args and "--json" in args and "--all" in args
    assert run.envs[0]["BEADS_DIR"] == "/beads"


def test_fetch_comments_passes_issue_id(comments_sample_json):
    run = FakeRun(stdout=comments_sample_json)
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    comments = client.fetch_comments("gv-crl")
    assert len(comments) == 3
    args = run.calls[0]
    assert args[:3] == ["/x/bd", "comments", "gv-crl"]
    assert "--json" in args


def test_add_comment_passes_text_and_returns_none():
    run = FakeRun(stdout="Comment added to gv-crl")
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    result = client.add_comment("gv-crl", "hello world")
    assert result is None
    args = run.calls[0]
    assert args[:4] == ["/x/bd", "comments", "add", "gv-crl"]
    assert "hello world" in args


def test_add_comment_raises_beadserror_on_nonzero_exit():
    run = FakeRun(stderr="boom", returncode=1)
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    with pytest.raises(BeadsError) as exc:
        client.add_comment("gv-crl", "hi")
    assert "boom" in str(exc.value)


def test_list_issues_raises_beadserror_on_nonzero_exit():
    run = FakeRun(stderr="db unreachable", returncode=1)
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    with pytest.raises(BeadsError):
        client.list_issues()


def test_set_status_updates_all_ids_in_one_call():
    run = FakeRun(stdout="ok")
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    client.set_status(["gv-1", "gv-2"], "deferred")
    args = run.calls[0]
    assert args[:4] == ["/x/bd", "update", "-s", "deferred"]
    assert args[4:] == ["gv-1", "gv-2"]


def test_set_status_noop_on_empty_ids():
    run = FakeRun(stdout="ok")
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    client.set_status([], "deferred")
    assert run.calls == []  # no bd invocation for an empty set


def test_close_passes_ids_and_reason():
    run = FakeRun(stdout="closed")
    client = BeadsClient(bd_bin="/x/bd", beads_dir="/beads", run=run)
    client.close(["gv-1", "gv-2"], "done via tui")
    args = run.calls[0]
    assert args[:2] == ["/x/bd", "close"]
    assert "gv-1" in args and "gv-2" in args
    assert "-r" in args and "done via tui" in args
