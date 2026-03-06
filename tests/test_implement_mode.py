"""
tests/test_implement_mode.py — ImplementMode 单元测试
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest

from file_agent.config import AgentConfig
from file_agent.scanner import FileInfo
from file_agent.executor import MoveRecord
from file_agent.undo_manager import UndoManager
from file_agent.modes.implement_mode import ImplementMode


# ─── Fixture helpers ────────────────────────────────────────────────


def _make_file(rel_path: str, ext: str = ".txt", size: int = 1024) -> FileInfo:
    return FileInfo(
        path=Path("/tmp") / rel_path,
        rel_path=rel_path,
        root_dir="/tmp",
        ext=ext,
        size_bytes=size,
        modified_at=datetime(2025, 1, 1),
        content_summary="",
    )


def _make_record(src: str, dst: str, success: bool) -> MoveRecord:
    return MoveRecord(
        src=Path(src),
        dst=Path(dst),
        rel_path=src,
        target_dir="test",
        success=success,
        error="",
    )


@pytest.fixture
def sample_files():
    return [
        _make_file("文档/报告.docx", ".docx", 10240),
        _make_file("代码/main.py", ".py", 2048),
        _make_file("代码/utils.py", ".py", 1024),
        _make_file("图片/photo.jpg", ".jpg", 5120),
    ]


@pytest.fixture
def cfg():
    c = AgentConfig()
    c.api_key = "test_key"
    c.scan_dirs = ["/tmp"]
    return c


@pytest.fixture
def undo_manager():
    return UndoManager(max_depth=10)


@pytest.fixture
def show_plan_fn():
    return MagicMock()


@pytest.fixture
def show_results_fn():
    return MagicMock()


@pytest.fixture
def impl(sample_files, cfg, undo_manager, show_plan_fn, show_results_fn):
    return ImplementMode(
        files=sample_files,
        cfg=cfg,
        undo_manager=undo_manager,
        show_plan_fn=show_plan_fn,
        show_results_fn=show_results_fn,
    )


# ─── 初始化 ─────────────────────────────────────────────────────────


class TestInit:
    def test_files_assigned(self, impl, sample_files):
        assert impl.files == sample_files

    def test_initial_plan_all_uncategorized(self, impl, sample_files):
        assert len(impl.plan) == len(sample_files)
        for v in impl.plan.values():
            assert v == "未分类"

    def test_history_initially_none(self, impl):
        assert impl._history is None


# ─── refresh ────────────────────────────────────────────────────────


class TestRefresh:
    def test_refresh_updates_files(self, impl):
        new_files = [_make_file("新文件夹/a.txt", ".txt")]
        impl.refresh(new_files)
        assert impl.files == new_files

    def test_refresh_rebuilds_plan_from_first_dir(self, impl, sample_files):
        impl.refresh(sample_files)
        # "文档/报告.docx" → 第一级目录 "文档"
        assert impl.plan["文档/报告.docx"] == "文档"
        assert impl.plan["代码/main.py"] == "代码"

    def test_refresh_root_file_maps_to_dot(self, impl):
        """根目录下的文件（无子目录）应映射到 '.'。"""
        files = [_make_file("standalone.txt", ".txt")]
        impl.refresh(files)
        assert impl.plan["standalone.txt"] == "."


# ─── preview (dry-run) ───────────────────────────────────────────────


class TestPreview:
    def test_preview_calls_execute_plan_dry_run(self, impl, show_results_fn):
        mock_records = [_make_record("a", "b", True)]

        with patch("file_agent.modes.implement_mode.execute_plan", return_value=mock_records) as mock_exec:
            result = impl.preview()

        mock_exec.assert_called_once_with(impl.plan, impl.files, impl.cfg, dry_run=True)
        assert result == mock_records

    def test_preview_calls_show_results_with_dry_run_true(self, impl, show_results_fn):
        mock_records = [_make_record("a", "b", True)]

        with patch("file_agent.modes.implement_mode.execute_plan", return_value=mock_records):
            impl.preview()

        show_results_fn.assert_called_once_with(mock_records, dry_run=True)

    def test_preview_does_not_push_undo(self, impl, undo_manager):
        with patch("file_agent.modes.implement_mode.execute_plan", return_value=[]):
            impl.preview()

        assert undo_manager.depth() == 0


# ─── execute ────────────────────────────────────────────────────────


class TestExecute:
    def _make_pending_record(self, src: str, dst: str) -> MoveRecord:
        """模拟 dry_run=True 返回的 pending 记录（success=None, error=''）。"""
        return MoveRecord(
            src=Path(src),
            dst=Path(dst),
            rel_path=src,
            target_dir="test",
            success=None,
            error="",
        )

    def test_execute_returns_false_when_user_cancels(self, impl):
        pending = [self._make_pending_record("a.txt", "tmp/a.txt")]
        real = [_make_record("a.txt", "tmp/a.txt", True)]

        with patch("file_agent.modes.implement_mode.execute_plan", side_effect=[pending, real]):
            with patch("file_agent.modes.implement_mode.remove_empty_dirs", return_value=0):
                with patch.object(impl, "_show_results"):
                    # Mock console.input → 'n'
                    with patch("file_agent.modes.implement_mode.console") as mock_console:
                        mock_console.input.return_value = "n"
                        mock_console.print = MagicMock()
                        success, records = impl.execute()

        assert success is False

    def test_execute_returns_false_when_no_movable_files(self, impl):
        """当 dry-run 结果中没有可移动的文件时，直接返回 False。"""
        with patch("file_agent.modes.implement_mode.execute_plan", return_value=[]):
            with patch.object(impl, "_show_results"):
                with patch("file_agent.modes.implement_mode.console") as mock_console:
                    mock_console.print = MagicMock()
                    success, records = impl.execute()

        assert success is False

    def test_execute_pushes_to_undo_on_success(self, impl, undo_manager):
        pending = [self._make_pending_record("a.txt", "tmp/a.txt")]
        real = [_make_record("a.txt", "tmp/a.txt", True)]

        side_effects = [pending, real]
        with patch("file_agent.modes.implement_mode.execute_plan", side_effect=side_effects):
            with patch("file_agent.modes.implement_mode.remove_empty_dirs", return_value=0):
                with patch.object(impl, "_show_results"):
                    with patch("file_agent.modes.implement_mode.console") as mock_console:
                        mock_console.input.return_value = "y"
                        mock_console.print = MagicMock()
                        success, _ = impl.execute()

        assert success is True
        assert undo_manager.depth() == 1


# ─── undo ───────────────────────────────────────────────────────────


class TestUndo:
    def test_undo_returns_false_when_nothing_to_undo(self, impl):
        result = impl.undo()
        assert result is False

    def test_undo_calls_rollback_and_returns_true(self, impl, undo_manager):
        records = [_make_record("新文件夹/a.txt", "a.txt", True)]
        undo_manager.push(records)

        with patch("file_agent.modes.implement_mode.rollback", return_value=records) as mock_rb:
            with patch("file_agent.modes.implement_mode.remove_empty_dirs", return_value=0):
                with patch("file_agent.modes.implement_mode.console") as mock_console:
                    mock_console.print = MagicMock()
                    result = impl.undo()

        assert result is True
        mock_rb.assert_called_once_with(records)

    def test_undo_decrements_undo_depth(self, impl, undo_manager):
        r1 = [_make_record("a.txt", "tmp/a.txt", True)]
        r2 = [_make_record("b.txt", "tmp/b.txt", True)]
        undo_manager.push(r1)
        undo_manager.push(r2)
        assert undo_manager.depth() == 2

        with patch("file_agent.modes.implement_mode.rollback", return_value=[]):
            with patch("file_agent.modes.implement_mode.remove_empty_dirs", return_value=0):
                with patch("file_agent.modes.implement_mode.console") as mock_console:
                    mock_console.print = MagicMock()
                    impl.undo()

        assert undo_manager.depth() == 1


# ─── ask_for_plan ────────────────────────────────────────────────────


class TestAskForPlan:
    def test_ask_for_plan_updates_plan(self, impl):
        new_plan = {"代码/main.py": "Python", "图片/photo.jpg": "图片"}
        with patch(
            "file_agent.modes.implement_mode.ask_llm_for_plan",
            return_value=(new_plan, "已生成新方案", []),
        ) as mock_fn:
            reply = impl.ask_for_plan("按类型分类文件")

        assert impl.plan == new_plan
        assert reply == "已生成新方案"
        mock_fn.assert_called_once()

    def test_ask_for_plan_updates_history(self, impl):
        new_plan = {}
        history = [{"role": "user", "content": "test"}]
        with patch(
            "file_agent.modes.implement_mode.ask_llm_for_plan",
            return_value=(new_plan, "回复", history),
        ):
            impl.ask_for_plan("test")

        assert impl._history == history


# ─── clear_history ───────────────────────────────────────────────────


class TestClearHistory:
    def test_clear_history_resets_to_none(self, impl):
        impl._history = [{"role": "user", "content": "x"}]
        impl.clear_history()
        assert impl._history is None
