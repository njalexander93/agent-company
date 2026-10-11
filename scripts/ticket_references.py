"""Fail when tracked files cite Linear issue identifiers or issue URLs.

Issue tickets are mutable, so code, tests and documentation describe the event,
date and host instead of naming the ticket. Test fixtures use the ``TEST`` key,
which is not a Linear team key in this workspace.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Team keys whose issue identifiers are mutable references; extend when a team is added.
TEAM_KEYS = ("AGENT",)


def pattern(keys: tuple[str, ...]) -> re.Pattern[str]:
    """Build the reference pattern for the named Linear team keys.

    Args:
        keys: Team keys whose ``KEY-<number>`` identifiers are forbidden.

    Returns:
        A pattern matching those identifiers or any Linear issue URL.
    """
    alternatives = "|".join(re.escape(key) for key in keys)
    return re.compile(rf"\b(?:{alternatives})-[0-9]+\b|linear\.app/[^\s/]+/issue/")


def tracked_files(root: Path) -> list[Path]:
    """List the files Git tracks in a checkout.

    Args:
        root: Checkout root to inspect.

    Returns:
        Absolute paths of every tracked file, including deleted-but-tracked ones.

    Raises:
        subprocess.CalledProcessError: If the directory is not a Git checkout.
    """
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True
    ).stdout
    return [root / name for name in listing.decode("utf-8").split("\0") if name]


def findings(root: Path, keys: tuple[str, ...] = TEAM_KEYS) -> list[str]:
    """Locate every tracked line that references a Linear issue.

    Args:
        root: Checkout root to inspect.
        keys: Team keys whose identifiers count as references.

    Returns:
        ``path:line: text`` entries in tracked-file order.
    """
    regex = pattern(keys)
    hits: list[str] = []
    for path in tracked_files(root):
        # Skip binary content and tracked files that are missing from the worktree.
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()[:120]}")
    return hits


def main(argv: list[str] | None = None) -> int:
    """Report issue references in the checkout and fail when any exist.

    Args:
        argv: Optional single checkout root; defaults to this repository.

    Returns:
        Zero without references, one otherwise.
    """
    arguments = sys.argv[1:] if argv is None else argv
    root = Path(arguments[0]).resolve() if arguments else ROOT
    hits = findings(root)
    for hit in hits:
        print(hit)
    # Name the rule so the fix is clear without opening the guide.
    if hits:
        print(f"{len(hits)} Linear issue reference(s) found; describe the event instead.")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
