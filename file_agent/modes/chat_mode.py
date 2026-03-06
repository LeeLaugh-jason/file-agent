"""
Chat 模式 ─ 只读文件探索与 LLM 对话。

在此模式下，agent 可以：
  - 本地计算文件结构统计（无需 LLM）
  - 将文件元数据注入 LLM context，回答用户关于文件的自然语言问题
  - 展示文件类型分布、大小排行、目录树等信息

严格禁止任何文件系统修改操作。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from ..config import AgentConfig
from ..scanner import FileInfo

console = Console()

# ─── 加载提示词模板 ───────────────────────────────────────────────────


def _load_prompt_template() -> str:
    """加载 chat_system.txt 提示词模板。"""
    prompt_path = Path(__file__).parent.parent / "prompts" / "chat_system.txt"
    try:
        return prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "你是一个文件管理助手（只读模式）。"
            "请根据提供的文件信息回答用户问题，不要建议做任何文件操作。\n\n"
            "文件信息：{file_context}"
        )


# ─── 本地统计计算 ──────────────────────────────────────────────────────


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def build_context_summary(files: List[FileInfo]) -> str:
    """生成结构化文件摘要字符串，用于注入 LLM context。

    纯本地计算，不消耗 LLM token。

    Returns
    -------
    str
        多行 JSON-like 结构，包含文件数量、类型分布、大小统计、
        目录分布、最大文件列表、部分内容摘要。
    """
    if not files:
        return "（无文件）"

    total_size = sum(f.size_bytes for f in files)

    # 类型分布
    ext_counter: Counter = Counter(f.ext or "（无扩展名）" for f in files)
    top_exts = ext_counter.most_common(10)

    # 目录分布
    dir_counter: Counter = Counter()
    for f in files:
        parts = Path(f.rel_path).parts
        top_dir = parts[0] if len(parts) > 1 else "（根目录）"
        dir_counter[top_dir] += 1

    # 最大文件 Top 10
    top_files = sorted(files, key=lambda f: f.size_bytes, reverse=True)[:10]

    # 内容摘要（前 15 个有摘要的文件）
    previews = [
        {"path": f.rel_path, "ext": f.ext, "summary": f.content_summary[:150]}
        for f in files
        if f.content_summary
    ][:15]

    data = {
        "总文件数": len(files),
        "总大小": _fmt_size(total_size),
        "文件类型分布（前10）": {ext: cnt for ext, cnt in top_exts},
        "一级目录分布": dict(dir_counter.most_common(15)),
        "最大文件（Top10）": [
            {"路径": f.rel_path, "大小": _fmt_size(f.size_bytes)} for f in top_files
        ],
        "内容摘要片段（前15个）": previews,
    }

    return json.dumps(data, ensure_ascii=False, indent=2)


# ─── ChatMode 主类 ─────────────────────────────────────────────────────


class ChatMode:
    """只读文件探索与对话模式。

    Parameters
    ----------
    files : list[FileInfo]
        已扫描的文件列表（由 CLI 层传入）。
    cfg : AgentConfig
        全局配置。
    """

    def __init__(self, files: List[FileInfo], cfg: AgentConfig) -> None:
        self.files = files
        self.cfg = cfg
        self._history: List[dict] = []
        self._context_summary: Optional[str] = None
        self._prompt_template = _load_prompt_template()

    # ─── 刷新文件列表（模式切换回来时） ───────────────────────────────

    def refresh(self, files: List[FileInfo]) -> None:
        """更新文件列表，并清除缓存的 context summary。"""
        self.files = files
        self._context_summary = None  # 强制重建

    # ─── LLM 对话 ─────────────────────────────────────────────────────

    def _get_context_summary(self) -> str:
        if self._context_summary is None:
            self._context_summary = build_context_summary(self.files)
        return self._context_summary

    def _build_system_message(self) -> dict:
        context = self._get_context_summary()
        content = self._prompt_template.replace("{file_context}", context)
        return {"role": "system", "content": content}

    def ask(
        self, user_input: str
    ) -> Tuple[str, bool]:
        """向 LLM 提问，返回 (回答文本, is_suggest_implement)。

        is_suggest_implement 为 True 时，表示 LLM 建议切换到 Implement 模式。

        Parameters
        ----------
        user_input : str
            用户输入的自然语言问题。
        """
        client = OpenAI(api_key=self.cfg.api_key, base_url=self.cfg.api_base)

        # 首次调用时插入 system 消息
        if not self._history:
            self._history.append(self._build_system_message())

        self._history.append({"role": "user", "content": user_input})

        try:
            response = client.chat.completions.create(
                model=self.cfg.model,
                messages=self._history,
            )
            reply = response.choices[0].message.content or ""
            self._history.append({"role": "assistant", "content": reply})
        except Exception as e:
            reply = f"[red]LLM 调用失败: {e}[/red]"

        # 检测是否建议切换到 Implement 模式
        suggest_implement = (
            ":mode implement" in reply
            or "Implement 模式" in reply
            or "切换到 Implement" in reply
        )

        return reply, suggest_implement

    def clear_history(self) -> None:
        """清空对话历史（保留 system 消息结构，下次重建）。"""
        self._history = []

    # ─── 本地可视化（不调用 LLM） ──────────────────────────────────────

    def show_summary(self) -> None:
        """用 rich 展示文件类型分布、大小排行等统计信息。"""
        if not self.files:
            console.print("[yellow]⚠️ 当前没有已扫描的文件。[/yellow]")
            return

        total = len(self.files)
        total_size = sum(f.size_bytes for f in self.files)

        # ── 类型分布表 ──
        ext_counter: Counter = Counter(f.ext or "（无扩展名）" for f in self.files)
        type_table = Table(
            title=f"📂 文件类型分布  [dim]（共 {total} 个文件 · {_fmt_size(total_size)}）[/dim]",
            box=box.SIMPLE_HEAVY,
            show_lines=False,
        )
        type_table.add_column("扩展名", style="cyan", min_width=10)
        type_table.add_column("数量", justify="right")
        type_table.add_column("占比", justify="right")

        for ext, cnt in ext_counter.most_common(15):
            pct = f"{cnt / total * 100:.1f}%"
            type_table.add_row(ext, str(cnt), pct)

        console.print(type_table)

        # ── 最大文件 Top 10 ──
        top_files = sorted(self.files, key=lambda f: f.size_bytes, reverse=True)[:10]
        size_table = Table(
            title="📦 最大文件 Top 10",
            box=box.SIMPLE_HEAVY,
            show_lines=False,
        )
        size_table.add_column("文件", max_width=50)
        size_table.add_column("大小", justify="right", style="green")
        size_table.add_column("修改时间", style="dim")

        for f in top_files:
            size_table.add_row(
                f.rel_path,
                _fmt_size(f.size_bytes),
                f.modified_at.strftime("%Y-%m-%d"),
            )

        console.print(size_table)

        # ── 一级目录分布 ──
        dir_counter: Counter = Counter()
        for f in self.files:
            parts = Path(f.rel_path).parts
            top_dir = parts[0] if len(parts) > 1 else "（根目录）"
            dir_counter[top_dir] += 1

        if len(dir_counter) > 1:
            dir_table = Table(
                title="📁 一级目录分布",
                box=box.SIMPLE_HEAVY,
                show_lines=False,
            )
            dir_table.add_column("目录名", style="magenta")
            dir_table.add_column("文件数", justify="right")
            for d, cnt in dir_counter.most_common():
                dir_table.add_row(d, str(cnt))
            console.print(dir_table)

    def show_help(self) -> None:
        """打印 Chat 模式可用命令。"""
        console.print(
            "\n[dim]💬 Chat 模式命令：[/dim]\n"
            "  [bold]/summary[/bold]          展示文件统计摘要\n"
            "  [bold]/clear[/bold]            清空对话历史\n"
            "  [bold]:mode implement[/bold]   切换到 Implement 模式\n"
            "  [bold]:exit[/bold]             退出程序\n"
            "  [dim]其他文本               作为问题发给 LLM[/dim]"
        )
