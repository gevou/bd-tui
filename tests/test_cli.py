"""Tests for CLI argument parsing."""
import pytest

from beads_tui.__main__ import parse_args


def test_default_group_is_status():
    assert parse_args([]).group == "status"


def test_group_flag_accepts_valid_dimensions():
    assert parse_args(["--group", "priority"]).group == "priority"
    assert parse_args(["--group", "label"]).group == "label"


def test_group_flag_rejects_unknown_dimension():
    with pytest.raises(SystemExit):
        parse_args(["--group", "bogus"])
