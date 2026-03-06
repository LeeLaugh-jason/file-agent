"""
tests/test_undo_manager.py — UndoManager 单元测试
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from file_agent.undo_manager import UndoManager
from file_agent.executor import MoveRecord


def _make_record(src: str, dst: str, success: bool = True) -> MoveRecord:
    return MoveRecord(
        src=Path(src),
        dst=Path(dst),
        rel_path=src,
        target_dir="test",
        success=success,
    )


class TestUndoManager:

    def test_initially_empty(self):
        um = UndoManager()
        assert not um.can_undo()
        assert um.depth() == 0
        assert um.pop() is None
        assert um.peek() is None

    def test_push_and_pop(self):
        um = UndoManager()
        record = _make_record("a.txt", "dest/a.txt")
        um.push([record])
        assert um.can_undo()
        assert um.depth() == 1
        popped = um.pop()
        assert popped == [record]
        assert um.depth() == 0
        assert not um.can_undo()

    def test_push_ignores_failed_records(self):
        """只压入 success=True 且 src != dst 的记录。"""
        um = UndoManager()
        failed = _make_record("a.txt", "dest/a.txt", success=False)
        same_path = _make_record("a.txt", "a.txt", success=True)  # src == dst
        um.push([failed, same_path])
        # 没有可撤销记录，不入栈
        assert not um.can_undo()
        assert um.depth() == 0

    def test_push_multiple_layers(self):
        um = UndoManager()
        r1 = _make_record("a.txt", "dir1/a.txt")
        r2 = _make_record("b.txt", "dir2/b.txt")
        um.push([r1])
        um.push([r2])
        assert um.depth() == 2
        # LIFO 顺序
        assert um.pop() == [r2]
        assert um.pop() == [r1]
        assert not um.can_undo()

    def test_peek_does_not_pop(self):
        um = UndoManager()
        r = _make_record("a.txt", "dest/a.txt")
        um.push([r])
        peeked = um.peek()
        assert peeked == [r]
        assert um.depth() == 1  # 未弹出

    def test_max_depth_truncation(self):
        """超出 max_depth 时，最旧的记录被丢弃。"""
        um = UndoManager(max_depth=3)
        for i in range(5):
            r = _make_record(f"file{i}.txt", f"dest/file{i}.txt")
            um.push([r])
        assert um.depth() == 3
        # 最新的 3 个留下（file2, file3, file4）
        top = um.pop()
        assert top[0].src == Path("dest/file4.txt") or top[0].rel_path == "file4.txt"

    def test_clear(self):
        um = UndoManager()
        um.push([_make_record("a.txt", "dest/a.txt")])
        um.push([_make_record("b.txt", "dest/b.txt")])
        um.clear()
        assert um.depth() == 0
        assert not um.can_undo()
