"""
modes — 双模式架构包。

暴露:
    Mode          枚举：Chat / Implement
    ChatMode      只读文件探索与对话模式
    ImplementMode 文件操作执行模式
"""

from __future__ import annotations

from enum import Enum, auto


class Mode(Enum):
    """Agent 工作模式枚举。"""

    CHAT = auto()       # 只读探索与对话
    IMPLEMENT = auto()  # 文件操作执行

    @property
    def label(self) -> str:
        """用于 UI 显示的短标签。"""
        return "CHAT" if self is Mode.CHAT else "IMPL"

    @property
    def description(self) -> str:
        return (
            "只读模式 — 探索与对话，不修改文件"
            if self is Mode.CHAT
            else "执行模式 — 支持文件整理、移动与回滚"
        )

    def toggle(self) -> "Mode":
        """在两个模式间切换。"""
        return Mode.IMPLEMENT if self is Mode.CHAT else Mode.CHAT


from .chat_mode import ChatMode
from .implement_mode import ImplementMode

__all__ = ["Mode", "ChatMode", "ImplementMode"]
