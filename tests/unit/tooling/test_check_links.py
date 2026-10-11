"""Check the Markdown link checker against disposable documentation trees."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import check_links

pytestmark = pytest.mark.unit


def tree(root: Path, files: dict[str, str]) -> None:
    """Write Markdown fixture files beneath a directory.

    Args:
        root: Directory that receives the files.
        files: Relative paths and their text.
    """
    # Create each parent before writing the file text.
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def test_mask_code_blanks_fences_and_inline_code_but_keeps_line_count() -> None:
    """Hide fenced blocks until a matching fence and replace inline spans with spaces."""
    text = "\n".join(
        [
            "before `[a](x.md)` after",
            "```text",
            "[b](y.md)",
            "~~~",
            "``",
            "````",
            "[c](z.md)",
            "~~~~",
            "[d](w.md)",
            "~~~",
            "plain",
        ]
    )
    masked = check_links.mask_code(text)
    # A fence ignores other markers and shorter runs; only a long enough match closes it.
    assert masked == [
        "before " + " " * 11 + " after",
        "",
        "",
        "",
        "",
        "",
        "[c](z.md)",
        "",
        "",
        "",
        "",
    ]


def test_slugify_lowercases_strips_punctuation_and_hyphenates_spaces() -> None:
    """Keep word characters, hyphens and underscores; drop other punctuation."""
    assert check_links.slugify("  Hello, World! (v2.0) ") == "hello-world-v20"
    assert check_links.slugify("PR preparation & verification") == "pr-preparation--verification"
    assert check_links.slugify("snake_case - mixed") == "snake_case---mixed"


def test_anchors_follow_github_rules_for_duplicates_links_and_code() -> None:
    """Generate suffixed duplicates and ignore fenced pseudo-headings."""
    text = "\n".join(
        [
            "# Setup",
            "## Setup ##",
            "### Setup",
            "## The [guide](g.md) and `run()`",
            "#",
            "#nospace",
            "",
            "```",
            "# Hidden",
            "```",
            "Plain text",
        ]
    )
    assert check_links.anchors(text) == {
        "setup",
        "setup-1",
        "setup-2",
        "the-guide-and-run",
        "",
    }


def test_links_extract_inline_images_titles_and_definitions_outside_code() -> None:
    """Return each target with its line number and skip code-only targets."""
    text = "\n".join(
        [
            'See [one](a.md#top) and ![img](pic.png "Title").',
            "`[code](nope.md)`",
            "```",
            "[fenced](nope.md)",
            "```",
            "[ref]: <b.md>",
            "[two](<c.md>) [three](d.md 'single')",
        ]
    )
    assert check_links.links(text) == [
        (1, "a.md#top"),
        (1, "pic.png"),
        (6, "b.md"),
        (7, "c.md"),
        (7, "d.md"),
    ]


def test_check_target_accepts_valid_files_anchors_and_external_targets(tmp_path: Path) -> None:
    """Return no fault for valid relative, anchored, encoded and external targets.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
    """
    tree(
        tmp_path,
        {
            "docs/a.md": "# Title\n## Rules: v1.2\n## Rules: v1.2\n",
            "docs/sub/my file.md": "# Deep\n",
            "docs/code.py": "x = 1\n",
        },
    )
    source = tmp_path / "docs/a.md"
    cache: dict[Path, set[str]] = {}
    # Accept each valid target, naming the target if one is rejected.
    for target in (
        "https://example.com/x",
        "mailto:someone@example.invalid",
        "//cdn.example.com/a.md",
        "sub/my%20file.md#deep",
        "#rules-v12-1",
        "a.md#title",
        "",
        "sub",
        "code.py#L1",
        "sub/my file.md",
    ):
        assert check_links.check_target(source, target, cache) is None, target
    # Anchors for both Markdown destinations were parsed once and cached.
    assert cache == {
        (tmp_path / "docs/a.md").resolve(): {"title", "rules-v12", "rules-v12-1"},
        (tmp_path / "docs/sub/my file.md").resolve(): {"deep"},
    }


def test_check_target_reports_missing_files_and_unknown_anchors(tmp_path: Path) -> None:
    """Name the missing file or anchor in the fault message.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
    """
    tree(tmp_path, {"a.md": "# Only\n", "b.md": "# Other\n"})
    source = tmp_path / "a.md"
    cache: dict[Path, set[str]] = {}
    # A missing file is reported before any fragment is considered.
    assert check_links.check_target(source, "gone.md", cache) == (
        "broken link: gone.md (file not found)"
    )
    assert check_links.check_target(source, "gone.md#only", cache) == (
        "broken link: gone.md#only (file not found)"
    )
    # Bare and file-qualified fragments must name an existing heading.
    assert check_links.check_target(source, "#missing", cache) == (
        "broken anchor: #missing (no heading #missing)"
    )
    assert check_links.check_target(source, "b.md#only", cache) == (
        "broken anchor: b.md#only (no heading #only)"
    )
    # Root-relative targets are unsupported and report as missing files.
    assert check_links.check_target(source, "/a.md", cache) == (
        "broken link: /a.md (file not found)"
    )


def test_markdown_files_expand_directories_and_report_missing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collect sorted Markdown files from directories and explicit files.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Restores the working directory after the case.
    """
    tree(tmp_path, {"docs/b.md": "", "docs/x/a.md": "", "docs/note.txt": "", "README.md": ""})
    # Expand relative arguments from the fixture root and report the absent one.
    monkeypatch.chdir(tmp_path)
    files, faults = check_links.markdown_files(["docs", "README.md", "absent"])
    assert [path.as_posix() for path in files] == ["README.md", "docs/b.md", "docs/x/a.md"]
    assert faults == ["absent:0: path not found"]


def test_main_passes_clean_tree_and_reports_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit zero for a clean tree scanned through the default paths.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Restores the working directory and argument vector.
        capsys: Captures the printed report.
    """
    tree(
        tmp_path,
        {
            "README.md": "[docs](docs/guide.md#start-here) [web](https://example.com)\n",
            "docs/guide.md": "# Start here\n```\n[x](missing.md)\n```\n[back](../README.md)\n",
            "src/pkg/notes.md": "No links.\n",
        },
    )
    # Run without arguments so the default paths resolve against the fixture root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_links.py"])
    assert check_links.main() == 0
    assert capsys.readouterr().out == "Checked 3 link(s) in 3 Markdown file(s); 0 fault(s).\n"


def test_main_reports_each_fault_with_location_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit one and print ``path:line: message`` for every broken link and anchor.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Restores the working directory after the case.
        capsys: Captures the printed report.
    """
    tree(
        tmp_path,
        {
            "docs/a.md": "# A\n\n[ok](b.md)\n[bad](nope.md)\n[anchor](b.md#nowhere)\n",
            "docs/b.md": "# B\n",
        },
    )
    # Scan explicit paths, one of which does not exist, and require ordered faults.
    monkeypatch.chdir(tmp_path)
    assert check_links.main(["docs", "missing-dir"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "missing-dir:0: path not found",
        "docs/a.md:4: broken link: nope.md (file not found)",
        "docs/a.md:5: broken anchor: b.md#nowhere (no heading #nowhere)",
        "Checked 3 link(s) in 2 Markdown file(s); 3 fault(s).",
    ]
