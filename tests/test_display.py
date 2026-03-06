"""
tests/test_display.py — 结果展示分组截断 & dryrun 后提示的单元测试。

覆盖三个方面：
1. _group_records_for_display() 纯函数（边界 + 错误场景）
2. show_move_results() 输出内容（省略行、统计行）
3. _handle_implement /dryrun 分支（调用 preview + 打印提示）
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from file_agent.cli import _EllipsisRow, _group_records_for_display, show_move_results
from file_agent.executor import MoveRecord


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _rec(rel: str, target: str, success=None, error: str = "") -> MoveRecord:
    """快速构造一个 MoveRecord。"""
    name = Path(rel).name
    return MoveRecord(
        src=Path("/src") / rel,
        dst=Path("/dst") / target / name,
        rel_path=rel,
        target_dir=target,
        success=success,
        error=error,
    )


def _jpgs(n: int, target: str = "图片") -> list[MoveRecord]:
    """生成 n 个 .jpg 记录。"""
    return [_rec(f"photo_{i:03d}.jpg", target) for i in range(n)]


# ===========================================================================
# 1. _group_records_for_display() 纯函数测试
# ===========================================================================

class TestGroupRecordsForDisplay:

    def test_empty_records_returns_empty(self):
        """空列表输入应返回空列表。"""
        assert _group_records_for_display([]) == []

    def test_small_group_shows_all(self):
        """3 个 jpg，max=5 → 全部显示，无 _EllipsisRow。"""
        recs = _jpgs(3)
        result = _group_records_for_display(recs, max_per_group=5)
        assert len(result) == 3
        assert not any(isinstance(r, _EllipsisRow) for r in result)

    def test_exactly_at_max_shows_all(self):
        """恰好等于 max_per_group 时，仍全部显示，不折叠。"""
        recs = _jpgs(5)
        result = _group_records_for_display(recs, max_per_group=5)
        assert len(result) == 5
        assert not any(isinstance(r, _EllipsisRow) for r in result)

    def test_one_over_max_triggers_ellipsis(self):
        """比 max 多 1 个时应出现 _EllipsisRow。"""
        recs = _jpgs(6, target="照片库")
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)
        ellipsis_rows = [r for r in result if isinstance(r, _EllipsisRow)]
        assert len(ellipsis_rows) == 1

    def test_large_group_shows_preview_count_plus_ellipsis(self):
        """10 个 jpg，preview=3 → 结果共 4 项（3 条记录 + 1 个省略行）。"""
        recs = _jpgs(10)
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)
        real = [r for r in result if not isinstance(r, _EllipsisRow)]
        ellipsis_rows = [r for r in result if isinstance(r, _EllipsisRow)]
        assert len(real) == 3
        assert len(ellipsis_rows) == 1

    def test_ellipsis_row_count_is_correct(self):
        """10 个 jpg，preview=3 → ellipsis.count 应为 7。"""
        recs = _jpgs(10)
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)
        er = next(r for r in result if isinstance(r, _EllipsisRow))
        assert er.count == 7

    def test_ellipsis_row_carries_ext_and_target(self):
        """省略行应正确携带扩展名和目标文件夹。"""
        recs = _jpgs(10, target="照片库")
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)
        er = next(r for r in result if isinstance(r, _EllipsisRow))
        assert er.ext == ".jpg"
        assert er.target_dir == "照片库"

    def test_mixed_ext_each_group_independent(self):
        """10 jpg + 2 txt：只有 jpg 触发折叠，txt 全部显示。"""
        recs = _jpgs(10) + [_rec(f"doc_{i}.txt", "文档") for i in range(2)]
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)

        ellipsis_rows = [r for r in result if isinstance(r, _EllipsisRow)]
        assert len(ellipsis_rows) == 1
        assert ellipsis_rows[0].ext == ".jpg"

        # txt 文件应全部可见
        txt_rows = [r for r in result if not isinstance(r, _EllipsisRow) and r.rel_path.endswith(".txt")]
        assert len(txt_rows) == 2

    def test_same_ext_different_targets_grouped_independently(self):
        """A 目录 6 jpg + B 目录 6 jpg → 各自独立出现省略行（共 2 个省略行）。"""
        recs_a = _jpgs(6, target="A")
        recs_b = _jpgs(6, target="B")
        result = _group_records_for_display(recs_a + recs_b, max_per_group=5, preview_count=3)
        ellipsis_rows = [r for r in result if isinstance(r, _EllipsisRow)]
        assert len(ellipsis_rows) == 2

    def test_no_extension_files_handled(self):
        """无扩展名文件（如 Makefile），省略行 ext 应为空字符串。"""
        recs = [_rec(f"Makefile_{i}", "构建") for i in range(8)]
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)
        er = next(r for r in result if isinstance(r, _EllipsisRow))
        assert er.ext == ""

    def test_single_file_always_shown(self):
        """1 个文件、max=1 → 不触发折叠。"""
        recs = _jpgs(1)
        result = _group_records_for_display(recs, max_per_group=1)
        assert len(result) == 1
        assert not any(isinstance(r, _EllipsisRow) for r in result)

    def test_preview_count_respected(self):
        """20 个 jpg，preview=2 → 只显示 2 条记录，ellipsis.count == 18。"""
        recs = _jpgs(20)
        result = _group_records_for_display(recs, max_per_group=5, preview_count=2)
        real = [r for r in result if not isinstance(r, _EllipsisRow)]
        er = next(r for r in result if isinstance(r, _EllipsisRow))
        assert len(real) == 2
        assert er.count == 18

    def test_order_of_real_records_preserved(self):
        """结果中可见记录的顺序应与输入一致（取前 preview_count 个）。"""
        recs = [_rec(f"f{i:02d}.jpg", "图片") for i in range(10)]
        result = _group_records_for_display(recs, max_per_group=5, preview_count=3)
        real = [r for r in result if not isinstance(r, _EllipsisRow)]
        assert [r.rel_path for r in real] == ["f00.jpg", "f01.jpg", "f02.jpg"]


# ===========================================================================
# 2. show_move_results() 输出内容测试
# ===========================================================================

class TestShowMoveResultsOutput:

    def _capture_output(self, records, **kwargs) -> str:
        """调用 show_move_results 并捕获 console 输出为字符串。"""
        import io
        from rich.console import Console as _Console
        buf = io.StringIO()
        test_console = _Console(file=buf, no_color=True, width=200)
        with patch("file_agent.cli.console", test_console):
            show_move_results(records, **kwargs)
        return buf.getvalue()

    def test_small_batch_shows_all_files(self):
        """≤ max_per_group 时，文件名应全部出现在输出中。"""
        recs = _jpgs(3, target="图片")
        output = self._capture_output(recs, dry_run=True, max_per_group=5)
        for i in range(3):
            assert f"photo_{i:03d}.jpg" in output

    def test_large_batch_shows_ellipsis(self):
        """100 个 jpg 时，输出应包含折叠提示（'还有'）。"""
        recs = _jpgs(100, target="图片")
        output = self._capture_output(recs, dry_run=True, max_per_group=5, preview_count=3)
        assert "还有" in output

    def test_large_batch_does_not_show_all_filenames(self):
        """100 个 jpg 时，第 50 个文件名不应出现在输出中（已被折叠）。"""
        recs = _jpgs(100, target="图片")
        output = self._capture_output(recs, dry_run=True, max_per_group=5, preview_count=3)
        assert "photo_050.jpg" not in output

    def test_summary_line_still_shows_total(self):
        """即使折叠显示，统计行仍应显示完整总数。"""
        recs = _jpgs(100, target="图片")
        output = self._capture_output(recs, dry_run=True, max_per_group=5, preview_count=3)
        assert "100" in output

    def test_mixed_ext_only_large_groups_truncated(self):
        """10 jpg + 2 txt 且 max=5：txt 文件名应完整出现，jpg 有折叠。"""
        recs = _jpgs(10) + [_rec(f"doc_{i}.txt", "文档") for i in range(2)]
        output = self._capture_output(recs, dry_run=True, max_per_group=5, preview_count=3)
        # txt 文件全部可见
        assert "doc_0.txt" in output
        assert "doc_1.txt" in output
        # jpg 有折叠提示
        assert "还有" in output


# ===========================================================================
# 3. _handle_implement /dryrun 分支测试
# ===========================================================================

class TestDryrunHint:
    """测试 /dryrun 命令在调用 preview() 后打印下一步提示。"""

    def _make_cli(self):
        """构造一个最小化的 App，跳过 __init__ 复杂依赖。"""
        from file_agent.cli import App
        cli = object.__new__(App)
        # 构造伪 _impl
        cli._impl = MagicMock()
        cli._impl.preview = MagicMock()
        return cli

    def test_dryrun_calls_preview(self):
        """执行 /dryrun 时，impl.preview() 必须被调用一次。"""
        cli = self._make_cli()
        with patch("file_agent.cli.console"):
            cli._handle_implement("/dryrun")
        cli._impl.preview.assert_called_once()

    def test_dryrun_hint_mentions_run_command(self):
        """提示文本中应包含 /run 命令名称。"""
        cli = self._make_cli()
        printed_texts = []
        with patch("file_agent.cli.console") as mock_console:
            mock_console.print = lambda *a, **kw: printed_texts.extend(a)
            cli._handle_implement("/dryrun")
        combined = " ".join(str(t) for t in printed_texts)
        assert "/run" in combined

    def test_dryrun_hint_mentions_adjustment(self):
        """提示文本中应含有 '调整' 或 '修改' 等引导用户调整方案的关键字。"""
        cli = self._make_cli()
        printed_texts = []
        with patch("file_agent.cli.console") as mock_console:
            mock_console.print = lambda *a, **kw: printed_texts.extend(a)
            cli._handle_implement("/dryrun")
        combined = " ".join(str(t) for t in printed_texts)
        assert any(kw in combined for kw in ("调整", "修改", "自然语言"))

    def test_dryrun_hint_shown_after_preview_output(self):
        """`console.print` 的调用应在 `preview()` 之后发生（call order）。"""
        cli = self._make_cli()
        call_order = []
        cli._impl.preview = MagicMock(side_effect=lambda: call_order.append("preview"))
        with patch("file_agent.cli.console") as mock_console:
            mock_console.print = MagicMock(side_effect=lambda *a, **kw: call_order.append("print"))
            cli._handle_implement("/dryrun")
        assert call_order.index("preview") < call_order.index("print")
