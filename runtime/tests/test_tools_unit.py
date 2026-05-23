"""Per-tool unit tests — hermetic, no HTTP, no daemon."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  — pytest fixture annotation

import pytest

from gapt_runtime.tools import (
    GaptEdit,
    GaptGlob,
    GaptGrep,
    GaptRead,
    ToolError,
    ToolInvocation,
)


def _inv(workspace: Path, name: str, **args: object) -> ToolInvocation:
    return ToolInvocation(name=name, arguments=args, workspace_root=str(workspace))


# ─────────────────────────────────────────────────────── gapt_read ──


@pytest.mark.asyncio
async def test_read_full_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("line1\nline2\nline3\n")
    result = await GaptRead().execute(_inv(tmp_path, "gapt_read", path="a.txt"))
    assert result.content == "line1\nline2\nline3"
    assert result.metadata == {
        "total_lines": 3,
        "returned_lines": 3,
        "line_offset": 0,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_read_line_window(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("\n".join(str(i) for i in range(100)))
    result = await GaptRead().execute(
        _inv(tmp_path, "gapt_read", path="long.txt", line_offset=10, limit=3)
    )
    assert result.content == "10\n11\n12"
    assert result.metadata is not None
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_read_path_traversal_refused(tmp_path: Path) -> None:
    (tmp_path / "in.txt").write_text("ok")
    with pytest.raises(ToolError) as exc:
        await GaptRead().execute(_inv(tmp_path, "gapt_read", path="../../etc/passwd"))
    assert exc.value.code == "exec.tool.access_denied"


@pytest.mark.asyncio
async def test_read_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await GaptRead().execute(_inv(tmp_path, "gapt_read", path="nope.txt"))
    assert exc.value.code == "exec.tool.invalid_input"


# ─────────────────────────────────────────────────────── gapt_glob ──


@pytest.mark.asyncio
async def test_glob_basic(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("")
    (tmp_path / "src" / "b.py").write_text("")
    (tmp_path / "README.md").write_text("")
    result = await GaptGlob().execute(_inv(tmp_path, "gapt_glob", pattern="src/*.py"))
    lines = result.content.splitlines()
    assert lines == ["src/a.py", "src/b.py"]
    assert result.metadata is not None
    assert result.metadata["count"] == 2


@pytest.mark.asyncio
async def test_glob_recursive(tmp_path: Path) -> None:
    (tmp_path / "a/b/c").mkdir(parents=True)
    (tmp_path / "a/x.py").write_text("")
    (tmp_path / "a/b/y.py").write_text("")
    (tmp_path / "a/b/c/z.py").write_text("")
    result = await GaptGlob().execute(_inv(tmp_path, "gapt_glob", pattern="**/*.py"))
    assert "a/x.py" in result.content
    assert "a/b/y.py" in result.content
    assert "a/b/c/z.py" in result.content


@pytest.mark.asyncio
async def test_glob_truncated(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    result = await GaptGlob().execute(_inv(tmp_path, "gapt_glob", pattern="*.txt", limit=5))
    assert result.metadata is not None
    assert result.metadata["count"] == 5
    assert result.metadata["truncated"] is True


# ─────────────────────────────────────────────────────── gapt_grep ──


@pytest.mark.asyncio
async def test_grep_basic(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def foo():\n    return 42\n")
    (tmp_path / "lib.py").write_text("def foo():\n    pass\n")
    result = await GaptGrep().execute(_inv(tmp_path, "gapt_grep", pattern=r"def foo"))
    lines = result.content.splitlines()
    assert any(line.startswith("main.py:1:") for line in lines)
    assert any(line.startswith("lib.py:1:") for line in lines)
    assert result.metadata is not None
    assert result.metadata["match_count"] == 2


@pytest.mark.asyncio
async def test_grep_path_scope(tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("FINDME\n")
    (tmp_path / "scope").mkdir()
    (tmp_path / "scope" / "inside.py").write_text("FINDME\n")
    result = await GaptGrep().execute(_inv(tmp_path, "gapt_grep", pattern="FINDME", path="scope"))
    assert "scope/inside.py" in result.content
    assert "outside.py" not in result.content


@pytest.mark.asyncio
async def test_grep_skips_binary(tmp_path: Path) -> None:
    (tmp_path / "img.bin").write_bytes(b"\x00\x01\x02HIT")
    (tmp_path / "code.py").write_text("HIT\n")
    result = await GaptGrep().execute(_inv(tmp_path, "gapt_grep", pattern="HIT"))
    assert "code.py" in result.content
    assert "img.bin" not in result.content
    assert result.metadata is not None
    assert result.metadata["files_skipped_binary"] == 1


@pytest.mark.asyncio
async def test_grep_invalid_regex(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await GaptGrep().execute(_inv(tmp_path, "gapt_grep", pattern="("))
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_grep_path_traversal_refused(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await GaptGrep().execute(_inv(tmp_path, "gapt_grep", pattern="x", path="../etc"))
    assert exc.value.code == "exec.tool.access_denied"


# ─────────────────────────────────────────────────────── gapt_edit ──


@pytest.mark.asyncio
async def test_edit_single_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\n")
    await GaptEdit().execute(_inv(tmp_path, "gapt_edit", path="a.py", old="x = 1", new="x = 99"))
    assert target.read_text() == "x = 99\ny = 2\n"


@pytest.mark.asyncio
async def test_edit_refuses_multi_without_all(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x\nx\nx\n")
    with pytest.raises(ToolError) as exc:
        await GaptEdit().execute(_inv(tmp_path, "gapt_edit", path="a.py", old="x", new="y"))
    assert exc.value.code == "exec.tool.invalid_input"
    # File untouched on refusal.
    assert target.read_text() == "x\nx\nx\n"


@pytest.mark.asyncio
async def test_edit_replace_all(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x\nx\nx\n")
    result = await GaptEdit().execute(
        _inv(tmp_path, "gapt_edit", path="a.py", old="x", new="y", all=True)
    )
    assert target.read_text() == "y\ny\ny\n"
    assert result.metadata == {"replaced": 3, "all": True}


@pytest.mark.asyncio
async def test_edit_missing_old(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n")
    with pytest.raises(ToolError) as exc:
        await GaptEdit().execute(_inv(tmp_path, "gapt_edit", path="a.py", old="MISSING", new="z"))
    assert exc.value.code == "exec.tool.invalid_input"


@pytest.mark.asyncio
async def test_edit_old_equals_new(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(ToolError) as exc:
        await GaptEdit().execute(_inv(tmp_path, "gapt_edit", path="a.py", old="x", new="x"))
    assert exc.value.code == "exec.tool.invalid_input"
