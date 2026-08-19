"""Layer 1: the bd subprocess boundary.

The ONLY module that shells out to `bd`. Pure parsing is split out into
``parse_issues`` / ``parse_comments`` so it can be tested without any process,
and the command wrappers take an injectable ``run`` callable so tests can fake
the single true external dependency (the bd process).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import Callable, Optional


def format_timestamp(iso: Optional[str], tz: Optional[tzinfo] = None) -> str:
    """Render a bd UTC ISO timestamp (e.g. '2026-08-09T08:24:16Z') in local time.

    ``tz=None`` uses the system local timezone. Unparseable/empty input is passed
    through unchanged so display never crashes on odd data.
    """
    if not iso:
        return ""
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)  # tz=None -> system local zone
    return local.strftime("%Y-%m-%d %H:%M %Z").rstrip()


class BeadsError(Exception):
    """Raised when a bd invocation fails or returns unparseable output."""


def is_db_open_error(message: str) -> bool:
    """True when a bd error means it couldn't find/open a beads database
    (usually a missing/misconfigured BEADS_DIR)."""
    m = message.lower()
    return (
        "no beads configuration found" in m
        or "failed to open database" in m
        or "repo_state.json" in m
    )


@dataclass
class Issue:
    id: str
    title: str = ""
    description: str = ""
    status: str = "open"
    priority: int = 2
    issue_type: str = "task"
    owner: Optional[str] = None
    assignee: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    comment_count: int = 0
    parent: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    estimated_minutes: Optional[int] = None
    # Raw dependency records: [{"depends_on_id", "type", ...}, ...].
    dependencies: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        return cls(
            id=d["id"],
            title=d.get("title", ""),
            description=d.get("description", "") or "",
            status=d.get("status", "open"),
            priority=d.get("priority", 2),
            issue_type=d.get("issue_type", "task"),
            owner=d.get("owner"),
            assignee=d.get("assignee"),
            labels=list(d.get("labels") or []),
            comment_count=d.get("comment_count", 0) or 0,
            parent=d.get("parent"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            estimated_minutes=d.get("estimated_minutes"),
            dependencies=list(d.get("dependencies") or []),
        )


@dataclass
class Comment:
    id: str
    issue_id: str
    author: str
    text: str
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Comment":
        return cls(
            id=d.get("id", ""),
            issue_id=d.get("issue_id", ""),
            author=d.get("author", ""),
            text=d.get("text", ""),
            created_at=d.get("created_at"),
        )


def _extract_json_array(out: str) -> list:
    """Pull the first JSON array out of possibly-noisy bd stdout.

    bd prints warnings (e.g. the dolt_server_port deprecation notice) to stdout
    before the JSON, so we scan for the first '[' and parse from there.
    """
    start = out.find("[")
    end = out.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise BeadsError(f"no JSON array in bd output: {out[:200]!r}")
    try:
        return json.loads(out[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BeadsError(f"could not parse bd JSON: {exc}") from exc


def parse_issues(out: str) -> list[Issue]:
    return [Issue.from_dict(d) for d in _extract_json_array(out)]


def parse_comments(out: str) -> list[Comment]:
    return [Comment.from_dict(d) for d in _extract_json_array(out)]


# --- binary / env resolution (mirrors the existing beads-dashboard server) ---

def resolve_bd() -> str:
    """Find the bd binary: $BD_BIN, then PATH, else the bare name."""
    return os.environ.get("BD_BIN") or shutil.which("bd") or "bd"


def default_beads_dir() -> Optional[str]:
    """The BEADS_DIR to pin, or None to let bd auto-discover from the cwd."""
    return os.environ.get("BEADS_DIR")


def _subprocess_run(args: list[str], env: dict):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)


class BeadsClient:
    """Thin wrapper over the bd CLI, with BEADS_DIR pinned."""

    def __init__(
        self,
        bd_bin: Optional[str] = None,
        beads_dir: Optional[str] = None,
        run: Optional[Callable] = None,
    ):
        self.bd_bin = bd_bin or resolve_bd()
        self.beads_dir = beads_dir or default_beads_dir()
        self._run = run or _subprocess_run

    def _exec(self, args: list[str]):
        env = {**os.environ}
        if self.beads_dir:  # else leave unset so bd auto-discovers from cwd
            env["BEADS_DIR"] = self.beads_dir
        try:
            proc = self._run([self.bd_bin, *args], env=env)
        except Exception as exc:  # noqa: BLE001 — surface any failure uniformly
            raise BeadsError(f"failed to run bd: {exc}") from exc
        if proc.returncode != 0:
            raise BeadsError(
                f"bd {' '.join(args[:2])} exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:500]}"
            )
        return proc.stdout

    def list_issues(self) -> list[Issue]:
        out = self._exec(["list", "--all", "--json", "-n", "0", "--no-pager"])
        return parse_issues(out)

    def fetch_comments(self, issue_id: str) -> list[Comment]:
        out = self._exec(["comments", issue_id, "--json"])
        return parse_comments(out)

    def add_comment(self, issue_id: str, text: str) -> None:
        self._exec(["comments", "add", issue_id, text])
        return None

    def set_status(self, issue_ids: list[str], status: str) -> None:
        """Set the status of one or more issues in a single bd call."""
        if not issue_ids:
            return None
        self._exec(["update", "-s", status, *issue_ids])
        return None

    def close(self, issue_ids: list[str], reason: str) -> None:
        """Close one or more issues with a shared reason."""
        if not issue_ids:
            return None
        self._exec(["close", *issue_ids, "-r", reason])
        return None
