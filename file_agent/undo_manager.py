"""
撤销管理模块 ─ 多层历史栈，支持连续多次撤销。

每次 execute_plan 成功执行后，将操作记录压栈；
每次 /undo 弹出栈顶，执行 rollback。
"""

from __future__ import annotations

from typing import List, Optional

from .executor import MoveRecord


class UndoManager:
    """维护文件操作的多层撤销历史栈。

    每次成功执行（/run）后调用 push；
    每次撤销（/undo）时调用 pop，将返回值传给 rollback()。
    """

    def __init__(self, max_depth: int = 20) -> None:
        """
        Parameters
        ----------
        max_depth : int
            最大历史深度，超出时丢弃最旧的记录。默认 20 层。
        """
        self._stack: List[List[MoveRecord]] = []
        self._max_depth = max_depth

    # ───────────────────────────── 写操作 ─────────────────────────────

    def push(self, records: List[MoveRecord]) -> None:
        """将本次执行的成功记录压入历史栈。

        只保留 ``success is True`` 且 ``src != dst`` 的记录，
        确保 rollback 时只移动真正被移动过的文件。

        Parameters
        ----------
        records : list[MoveRecord]
            来自 execute_plan 的完整记录列表。
        """
        reversible = [r for r in records if r.success is True and r.src != r.dst]
        if not reversible:
            return  # 没有可撤销操作，不压栈

        self._stack.append(reversible)

        # 超出最大深度时，丢弃最旧的记录
        if len(self._stack) > self._max_depth:
            self._stack.pop(0)

    def pop(self) -> Optional[List[MoveRecord]]:
        """弹出栈顶记录，用于传入 rollback()。

        Returns
        -------
        list[MoveRecord] | None
            若栈不为空，返回栈顶记录列表；否则返回 None。
        """
        if not self._stack:
            return None
        return self._stack.pop()

    def clear(self) -> None:
        """清空所有历史记录（例如重新扫描后上下文失效时使用）。"""
        self._stack.clear()

    # ───────────────────────────── 查询 ─────────────────────────────

    def can_undo(self) -> bool:
        """是否有可撤销的操作。"""
        return bool(self._stack)

    def depth(self) -> int:
        """当前历史栈深度（可撤销层数）。"""
        return len(self._stack)

    def peek(self) -> Optional[List[MoveRecord]]:
        """查看栈顶记录而不弹出（用于 UI 提示）。"""
        if not self._stack:
            return None
        return self._stack[-1]
