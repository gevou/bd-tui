"""Entry point: `python -m beads_tui`."""
from __future__ import annotations

import argparse
from typing import Optional

from beads_tui.model import DIMENSIONS


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bd-tui",
        description="Keyboard-driven kanban TUI for the bd (beads) issue tracker.",
    )
    parser.add_argument(
        "--group",
        choices=DIMENSIONS,
        default="status",
        help="Initial column grouping dimension (default: status). "
        "Cycle at runtime with 'g'.",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=15.0,
        help="Auto-refresh interval in seconds (0 to disable). Default: 15.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    # Imported here so `parse_args` stays import-light for tests.
    from beads_tui.app import BoardApp

    BoardApp(dimension=args.group, poll_interval=args.poll).run()


if __name__ == "__main__":
    main()
