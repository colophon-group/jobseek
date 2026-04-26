"""Tests for labeller path sandboxing (#2669)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.labeller.paths import PathSandboxError, assert_under_data_root


@pytest.fixture
def sandbox_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LABELLER_DATA_ROOT", str(tmp_path))
    return tmp_path


def test_existing_path_inside_root_is_accepted(sandbox_root: Path) -> None:
    p = sandbox_root / "postings" / "2026-04-25" / "abc.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    assert assert_under_data_root(p) == p.resolve()


def test_nonexistent_path_inside_root_is_accepted(sandbox_root: Path) -> None:
    """Output paths don't exist yet — sandbox check must work pre-creation."""
    p = sandbox_root / "_runs" / "2026-04-25" / "abc" / "out.json"
    assert assert_under_data_root(p) == p.resolve()


def test_path_above_root_is_rejected(sandbox_root: Path) -> None:
    p = sandbox_root / ".." / "etc" / "passwd"
    with pytest.raises(PathSandboxError, match="escapes LABELLER_DATA_ROOT"):
        assert_under_data_root(p)


def test_absolute_path_outside_root_is_rejected(sandbox_root: Path) -> None:
    p = Path("/tmp/totally-not-under-root.json")
    with pytest.raises(PathSandboxError, match="escapes LABELLER_DATA_ROOT"):
        assert_under_data_root(p)


def test_symlink_traversal_is_rejected(sandbox_root: Path, tmp_path: Path) -> None:
    """A symlink whose target lives outside the root must be rejected."""
    target = tmp_path.parent / "outside.json"
    target.write_text("secret")
    link = sandbox_root / "shortcut.json"
    link.symlink_to(target)
    with pytest.raises(PathSandboxError, match="escapes LABELLER_DATA_ROOT"):
        assert_under_data_root(link)


def test_dotdot_segment_normalized_before_check(sandbox_root: Path) -> None:
    """`a/b/../c` resolves to `a/c` — that one stays in-root."""
    inside = sandbox_root / "_runs" / ".." / "postings" / "x.json"
    expected = (sandbox_root / "postings" / "x.json").resolve()
    assert assert_under_data_root(inside) == expected


def test_root_itself_is_accepted(sandbox_root: Path) -> None:
    assert assert_under_data_root(sandbox_root) == sandbox_root.resolve()


# ---------- CLI integration: _sandbox_paths blocks at parse boundary ----


def test_cli_sandbox_blocks_out_of_root_out(
    sandbox_root: Path, capsys: pytest.CaptureFixture
) -> None:
    """A `--out` pointing outside the data root must exit non-zero before
    dispatch — the CLI should not invoke the underlying handler at all."""
    from src.labeller.cli import _sandbox_paths, build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "sample",
            "--count",
            "1",
            "--out",
            "/tmp/escape-attempt.json",
        ]
    )
    rc = _sandbox_paths(args)
    assert rc == 4
    err = capsys.readouterr().err
    assert "path sandbox" in err
    assert "escape-attempt.json" in err


def test_cli_sandbox_allows_in_root_paths(sandbox_root: Path) -> None:
    from src.labeller.cli import _sandbox_paths, build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "sample",
            "--count",
            "1",
            "--out",
            str(sandbox_root / "_runs" / "2026-04-25" / "sample.json"),
        ]
    )
    assert _sandbox_paths(args) == 0


def test_cli_sandbox_validates_render_task_paths(sandbox_root: Path) -> None:
    """`render-task` takes both an --input read path and an --out write path —
    both must be sandboxed."""
    from src.labeller.cli import _sandbox_paths, build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "render-task",
            "--task",
            "split_sections",
            "--input",
            "/etc/passwd",
            "--out",
            str(sandbox_root / "out.md"),
        ]
    )
    assert _sandbox_paths(args) == 4


def test_cli_sandbox_covers_every_path_typed_subcommand_arg() -> None:
    """Guard rail: when a new subcommand adds a path arg, the
    `_SANDBOXED_PATH_ARGS` registry must list it. Cross-check the parser
    against the registry; any path-typed dest not in the registry is a
    silent sandbox bypass."""
    from src.labeller.cli import _SANDBOXED_PATH_ARGS, build_parser

    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    for cmd_name, sub in subparsers_action.choices.items():
        sandboxed = set(_SANDBOXED_PATH_ARGS.get(cmd_name, ()))
        path_dests = {a.dest for a in sub._actions if a.type is Path}
        missing = path_dests - sandboxed
        assert not missing, (
            f"subcommand `{cmd_name}` has Path-typed args {missing} "
            f"that aren't in _SANDBOXED_PATH_ARGS — silent sandbox bypass"
        )
