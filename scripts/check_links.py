"""Verify relative links and heading anchors in the repository's Markdown files.

The checker reads Markdown only from the named paths, ignores fenced and inline
code, HTML comments and footnote definitions, and skips targets that carry a URL
scheme. It resolves each remaining target against the containing file, requires
every path component to match the on-disk name exactly so case-insensitive
filesystems cannot hide a link that breaks elsewhere, and checks ``#fragment``
anchors against headings slugged by GitHub's rules. It uses the standard library
only and never contacts the network. Root-relative targets (``/path``) are not supported and
report as missing files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

DEFAULT_PATHS = ("docs", "README.md", "src")
# Any indentation is accepted so fences nested in list items are masked; an indented
# code block cannot hold a live link either, so masking it as well is harmless.
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?[ \t]*$")
CLOSING_HASHES = re.compile(r"[ \t]+#+$")
COMMENT_OPEN = "<!--"
COMMENT_CLOSE = "-->"
# A destination is either ``<...>``, which may contain spaces, or a bare run without them.
INLINE_LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*(?:<([^<>\n]*)>|([^\s<>()]*))(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
# Labels starting with ``^`` are footnote definitions, whose text is not a destination.
REFERENCE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*(?:<([^<>\n]*)>|([^\s<>]+))")
LINK_TEXT = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
SLUG_DROP = re.compile(r"[^\w\- ]")
SCHEME = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:|//)")


def mask_code(text: str) -> list[str]:
    """Blank fenced code, inline code spans and HTML comments while keeping positions.

    Args:
        text: Complete Markdown document.

    Returns:
        One entry per source line; fenced lines become empty, while inline code
        spans and HTML comment text, which may span lines, become spaces of the
        same width.
    """
    masked: list[str] = []
    fence: str | None = None
    in_comment = False
    # Walk lines in order while tracking whether a fence or a comment is open.
    for line in text.splitlines():
        match = FENCE.match(line)
        # Hide every line inside an open fence, including its closing marker.
        if fence is not None:
            # Close the fence only with the same marker character at least as long.
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            masked.append("")
            continue
        # Open a new fence and hide its marker line unless a comment hides the marker.
        if match and not in_comment:
            fence = match.group(1)
            masked.append("")
            continue
        # Replace inline code spans so their contents cannot form links or headings.
        line = INLINE_CODE.sub(lambda span: " " * len(span.group(0)), line)
        # Blank each comment span, continuing one left open by an earlier line.
        start = 0 if in_comment else line.find(COMMENT_OPEN)
        while start != -1:
            # A newly opened comment cannot close inside its own opening marker.
            close = line.find(COMMENT_CLOSE, start if in_comment else start + len(COMMENT_OPEN))
            stop = len(line) if close == -1 else close + len(COMMENT_CLOSE)
            line = line[:start] + " " * (stop - start) + line[stop:]
            in_comment = close == -1
            start = -1 if in_comment else line.find(COMMENT_OPEN, stop)
        masked.append(line)
    return masked


def slugify(heading: str) -> str:
    """Convert rendered heading text to its GitHub anchor without a duplicate suffix.

    Args:
        heading: Heading text after Markdown link syntax is reduced to its label.

    Returns:
        Lowercase text with punctuation other than hyphens and underscores
        removed and each space replaced by a hyphen.
    """
    return SLUG_DROP.sub("", heading.strip().lower()).replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Collect the anchors GitHub generates for a document's ATX headings.

    Args:
        text: Complete Markdown document.

    Returns:
        Every heading slug, with ``-1``, ``-2`` suffixes for repeated slugs.
    """
    seen: dict[str, int] = {}
    result: set[str] = set()
    # Read headings from the original lines so inline code text stays in the slug.
    for original, masked in zip(text.splitlines(), mask_code(text), strict=True):
        heading = HEADING.match(original)
        # Skip fenced or blank lines and lines that are not headings.
        if not masked or not heading:
            continue
        raw = heading.group(1) or ""
        # Reduce closing hashes, link syntax and code backticks to rendered text.
        raw = CLOSING_HASHES.sub("", raw)
        raw = LINK_TEXT.sub(lambda link: link.group(1), raw).replace("`", "")
        slug = slugify(raw)
        # Suffix repeated slugs in document order as GitHub does.
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if count == 0 else f"{slug}-{count}")
    return result


def links(text: str) -> list[tuple[int, str]]:
    """Extract inline link targets and reference definitions outside code.

    Args:
        text: Complete Markdown document.

    Returns:
        ``(line number, target)`` pairs in document order.
    """
    found: list[tuple[int, str]] = []
    # Scan each code-masked line so code examples never yield targets.
    for number, line in enumerate(mask_code(text), 1):
        # Collect inline links and images first, from whichever destination form matched.
        found.extend(
            (number, match.group(1) if match.group(1) is not None else match.group(2))
            for match in INLINE_LINK.finditer(line)
        )
        definition = REFERENCE.match(line)
        # Add a reference definition's destination when the line declares one.
        if definition:
            bracketed = definition.group(1)
            found.append((number, bracketed if bracketed is not None else definition.group(2)))
    return found


def check_target(
    source: Path, target: str, cache: dict[Path, set[str]], listings: dict[Path, set[str]]
) -> str | None:
    """Verify one link target relative to the file that contains it.

    Every path component must match an on-disk name exactly, so a link that only
    resolves on a case-insensitive filesystem fails on every platform.

    Args:
        source: Markdown file containing the link.
        target: Raw link destination.
        cache: Anchor sets already computed, keyed by resolved path; updated in place.
        listings: Entry names of each directory walked so far, keyed by resolved
            directory; updated in place so each directory is listed once per run.

    Returns:
        A fault message, or ``None`` when the target is valid or external.
    """
    # Skip external schemes and protocol-relative URLs entirely.
    if SCHEME.match(target):
        return None
    path_part, _, fragment = target.partition("#")
    # Resolve the file part; an empty file part refers to the containing file.
    destination = source.parent / unquote(path_part) if path_part else source
    relative = Path(unquote(path_part))
    directory = source.parent / relative.anchor
    # Walk each component below any root; ``Path.exists`` ignores letter case on macOS
    # and Windows, so only exact directory entries prove the link works everywhere.
    for part in relative.parts[1:] if relative.anchor else relative.parts:
        key = directory.resolve()
        # List each directory once per run; a file cannot contain another component.
        if key not in listings:
            if not key.is_dir():
                return f"broken link: {target} (file not found)"
            listings[key] = {entry.name for entry in key.iterdir()}
        # Step to the parent of a directory without matching a name.
        if part == "..":
            directory = directory / part
            continue
        # Report a missing component, naming the on-disk spelling when only case differs.
        if part not in listings[key]:
            folded = part.casefold()
            actual = next(
                (name for name in sorted(listings[key]) if name.casefold() == folded), None
            )
            if actual is None:
                return f"broken link: {target} (file not found)"
            kind = "directory" if (directory / actual).is_dir() else "file"
            return f"broken link: {target} (case mismatch; {kind} is {actual})"
        directory = directory / part
    # Verify anchors only for fragments that point into a Markdown file.
    if not fragment or destination.suffix.lower() != ".md" or not destination.is_file():
        return None
    key = destination.resolve()
    # Parse each destination's headings once per run.
    if key not in cache:
        cache[key] = anchors(destination.read_text(encoding="utf-8"))
    # Report a fragment that matches no generated heading anchor.
    if unquote(fragment) not in cache[key]:
        return f"broken anchor: {target} (no heading #{fragment})"
    return None


def markdown_files(paths: list[str]) -> tuple[list[Path], list[str]]:
    """Expand command-line paths into Markdown files.

    Args:
        paths: Files or directories to scan.

    Returns:
        Markdown files sorted by POSIX path text, which orders identically on every
        platform, and a fault for each argument that does not exist.
    """
    files: set[Path] = set()
    faults: list[str] = []
    # Classify each argument as a directory, a file or a missing path.
    for name in paths:
        path = Path(name)
        # Walk a directory for every Markdown file beneath it.
        if path.is_dir():
            files.update(path.rglob("*.md"))
        # Accept an explicit file argument as given.
        elif path.is_file():
            files.add(path)
        else:
            # Report a missing argument instead of silently scanning less.
            faults.append(f"{path.as_posix()}:0: path not found")
    # Sort by path text rather than Path objects, whose order is case-insensitive on Windows.
    return sorted(files, key=Path.as_posix), faults


def main(argv: list[str] | None = None) -> int:
    """Check every link in the selected Markdown files and report faults.

    Args:
        argv: Paths to scan; defaults to ``docs``, ``README.md`` and ``src``.

    Returns:
        Zero when every link resolves, one when any fault is found.
    """
    # Select the requested paths or the repository defaults.
    arguments = sys.argv[1:] if argv is None else argv
    files, faults = markdown_files(arguments or list(DEFAULT_PATHS))
    cache: dict[Path, set[str]] = {}
    listings: dict[Path, set[str]] = {}
    checked = 0
    # Check each link in each file and record faults with their location.
    for path in files:
        # Validate every extracted target in document order.
        for number, target in links(path.read_text(encoding="utf-8")):
            checked += 1
            fault = check_target(path, target, cache, listings)
            # Keep only failing targets, prefixed with their source location.
            if fault:
                faults.append(f"{path.as_posix()}:{number}: {fault}")
    # Print faults first, then a single summary line and the exit status.
    for fault in faults:
        print(fault)
    print(f"Checked {checked} link(s) in {len(files)} Markdown file(s); {len(faults)} fault(s).")
    return 1 if faults else 0


if __name__ == "__main__":
    raise SystemExit(main())
