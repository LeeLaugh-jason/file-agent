"""
命令行交互模块 ─ 双模式状态机 + prompt_toolkit 增强型 UX。

模式:
    Chat 模式（只读）   — 自然语言对话，探索文件信息，不修改磁盘
    Implement 模式（执行）— LLM 生成方案，dry-run 预览，确认执行，多层撤销

切换:
    Tab 键              — 在两种模式间快速切换
    :mode chat          — 切换到 Chat 模式
    :mode implement     — 切换到 Implement 模式
    :exit               — 退出程序
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

from .config import AgentConfig
from .scanner import FileInfo, scan_directories, file_list_paths
from .extractors import enrich_file_list
from .types import FilePlan
from .classifier import ask_llm_for_plan
from .plan_validation import validate_loaded_plan
from .executor import MoveRecord, execute_plan, rollback, remove_empty_dirs
from .undo_manager import UndoManager
from .modes import Mode, ChatMode, ImplementMode

console = Console()


# ==========================================
# 分组截断：省略行数据结构与分组逻辑
# ==========================================

@dataclass
class _EllipsisRow:
    """代表被折叠的同类文件，展示时渲染为一行省略提示。

    Attributes
    ----------
    count : int
        被隐藏（折叠）的文件数量。
    ext : str
        文件扩展名（含前导点，如 '.jpg'；无扩展名时为空字符串）。
    target_dir : str
        目标文件夹名称。
    """

    count: int
    ext: str
    target_dir: str


def _group_records_for_display(
    records: List,
    max_per_group: int = 5,
    preview_count: int = 3,
) -> List[Union["MoveRecord", _EllipsisRow]]:
    """按 (target_dir, ext) 分组，超出阈值时折叠为省略行。

    同一 (target_dir, ext) 组内文件数超过 ``max_per_group`` 时，
    只保留前 ``preview_count`` 条，剩余文件用一个 :class:`_EllipsisRow` 代替。
    各分组独立计算，不同扩展名或不同目标目录的文件互不影响。
    输入记录的相对顺序在输出中保持不变。

    Parameters
    ----------
    records : list[MoveRecord]
        原始移动记录列表。
    max_per_group : int
        触发折叠的阈值：同组文件数 **严格大于** 此值才折叠，默认 5。
    preview_count : int
        折叠时保留展示的记录条数（取原始顺序最前面的几条），默认 3。

    Returns
    -------
    list[MoveRecord | _EllipsisRow]
        混合列表：可见的 MoveRecord 按原顺序排列，
        超出部分在最后一条可见记录之后紧跟一个 _EllipsisRow。
    """
    # 按 (target_dir, ext) 收集原始索引，保持插入顺序
    groups: Dict[tuple, List[int]] = defaultdict(list)
    for i, r in enumerate(records):
        ext = Path(r.rel_path).suffix.lower()
        groups[(r.target_dir, ext)].append(i)

    # 确定哪些索引可见，哪些位置插入省略行
    visible: set = set()
    ellipsis_map: Dict[int, _EllipsisRow] = {}  # key = 最后一个可见索引

    for (target_dir, ext), indices in groups.items():
        if len(indices) > max_per_group:
            kept = indices[:preview_count]
            visible.update(kept)
            hidden = len(indices) - preview_count
            ellipsis_map[kept[-1]] = _EllipsisRow(
                count=hidden, ext=ext, target_dir=target_dir
            )
        else:
            visible.update(indices)

    # 按原始顺序输出，在对应位置插入省略行
    result: List[Union[MoveRecord, _EllipsisRow]] = []
    for i, r in enumerate(records):
        if i in visible:
            result.append(r)
            if i in ellipsis_map:
                result.append(ellipsis_map[i])
    return result


# ==========================================
# 流式打字机输出
# ==========================================

def _stream_print(text: str, prefix: str = "\n🤖 ") -> None:
    """逐字符打印文本，模拟打字机流式效果。

    总时长不超过 1.5 秒，每字符最小延迟 5 ms。
    """
    sys.stdout.write(prefix)
    sys.stdout.flush()
    delay = min(0.02, 1.5 / max(len(text), 1))
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


# ==========================================
# 美化输出
# ==========================================

def show_plan_table(plan: FilePlan, files: List[FileInfo]) -> None:
    """用 rich Tree 以文件树格式展示分类方案（按目标文件夹分组）。"""
    fi_map: Dict[str, FileInfo] = {fi.rel_path: fi for fi in files}

    # 按目标文件夹分组
    groups: Dict[str, List[str]] = {}
    for rel_path, target_dir in plan.items():
        groups.setdefault(target_dir, []).append(rel_path)

    tree = Tree(
        f"📋 [bold cyan]整理方案[/bold cyan]"
        f"  [dim]({len(plan)} 个文件 → {len(groups)} 个文件夹)[/dim]"
    )

    for target_dir in sorted(groups.keys()):
        file_paths = sorted(groups[target_dir])
        dir_branch = tree.add(
            f"📁 [bold magenta]{target_dir}[/bold magenta]"
            f"  [dim]({len(file_paths)} 个文件)[/dim]"
        )
        for rel_path in file_paths:
            fi = fi_map.get(rel_path)
            ext = fi.ext if fi else ""
            size = _fmt_size(fi.size_bytes) if fi else "?"
            filename = Path(rel_path).name
            origin = str(Path(rel_path).parent)
            origin_hint = f"  [dim]← {origin}[/dim]" if origin != "." else ""
            dir_branch.add(
                f"📄 {filename}"
                f"  [yellow]{ext}[/yellow]"
                f"  [green]{size}[/green]"
                f"{origin_hint}"
            )

    console.print()
    console.print(tree)


def show_move_results(
    records: List[MoveRecord],
    dry_run: bool = False,
    max_per_group: int = 5,
    preview_count: int = 3,
) -> None:
    """用 rich Table 展示移动结果，大量同类文件时自动折叠。

    同一 (target_dir, ext) 组内文件数超过 ``max_per_group`` 时，
    只显示前 ``preview_count`` 条，其余用省略行代替。
    统计行始终显示完整总数，不受折叠影响。

    Parameters
    ----------
    records : list[MoveRecord]
        移动记录列表（dry-run 或实际执行结果均可）。
    dry_run : bool
        若为 True，显示「预演结果」标题且统计行提示未修改磁盘。
    max_per_group : int
        触发折叠的阈值，默认 5。
    preview_count : int
        折叠时保留显示的条数，默认 3。
    """
    display_items = _group_records_for_display(
        records, max_per_group=max_per_group, preview_count=preview_count
    )

    table = Table(
        title="🧪 预演结果" if dry_run else "🚀 执行结果",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        title_style="bold cyan",
    )
    table.add_column("状态", width=6, justify="center")
    table.add_column("文件", max_width=45)
    table.add_column("目标", max_width=30)
    table.add_column("备注", style="dim", max_width=30)

    for item in display_items:
        if isinstance(item, _EllipsisRow):
            ext_hint = item.ext if item.ext else "（无扩展名）"
            table.add_row(
                "[dim]…[/dim]",
                f"[dim]还有 {item.count} 个 {ext_hint} 文件（已折叠）[/dim]",
                f"[dim]{item.target_dir}[/dim]",
                "",
            )
        else:
            r = item
            if r.success is None:
                status = "[yellow]预演[/yellow]"
            elif r.success:
                status = "[green]✅[/green]"
            else:
                status = "[red]❌[/red]"

            note = r.error if r.error else ""
            # 如果发生了重命名，标注新文件名
            if r.success is not False and r.dst.name != Path(r.rel_path).name:
                note = f"重命名为: {r.dst.name}"

            table.add_row(status, r.rel_path, r.target_dir, note)

    console.print(table)

    # 统计行：总数始终反映完整记录数，不受显示折叠影响
    total = len(records)
    ok = sum(1 for r in records if r.success is True)
    fail = sum(1 for r in records if r.success is False)
    skip = total - ok - fail if not dry_run else 0
    if dry_run:
        console.print(f"\n📊 共 {total} 个文件将被移动（dry-run 未修改磁盘）")
    else:
        console.print(f"\n📊 成功 {ok}  失败 {fail}  跳过 {skip}  共 {total}")


def _fmt_size(size_bytes: int) -> str:
    """格式化文件大小。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


# ==========================================
# 方案导入导出
# ==========================================

def save_plan_json(plan: FilePlan, path: str) -> None:
    """将方案导出为 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    console.print(f"[green]✅ 方案已保存到: {path}[/green]")


def load_plan_json(path: str, files: List[FileInfo]) -> Optional[FilePlan]:
    """从 JSON 文件加载方案。"""
    p = Path(path)
    if not p.is_file():
        console.print(f"[red]❌ 文件不存在: {path}[/red]")
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            plan = json.load(f)
        valid_keys = {fi.rel_path for fi in files}
        validated, errors = validate_loaded_plan(plan, valid_keys)
        if errors:
            summary = "；".join(errors[:5])
            if len(errors) > 5:
                summary += f"；以及其他 {len(errors)-5} 项"
            console.print(f"[red]❌ 方案校验失败: {summary}[/red]")
            return None

        assert validated is not None
        console.print(f"[green]✅ 已加载方案: {path} ({len(validated)} 个文件)[/green]")
        return validated
    except Exception as e:
        console.print(f"[red]❌ 加载失败: {e}[/red]")
        return None


# ==========================================
# 主交互循环（模式状态机）
# ==========================================


# ── 模式对应的 rich 颜色与图标 ──────────────────────────────────────────
_MODE_STYLE: dict = {
    Mode.CHAT: ("cyan", "🔵"),
    Mode.IMPLEMENT: ("red", "🔴"),
}


class App:
    """CLI 应用主类：双模式状态机。

    模式：
      Mode.CHAT       — 只读，文件探索与 LLM 对话
      Mode.IMPLEMENT  — 执行，文件整理 + dry-run + 多层撤销
    """

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.files: List[FileInfo] = []
        self.mode: Mode = Mode.CHAT
        self.undo_manager = UndoManager()
        self._start_mode: str = "chat"  # 可由外部（main.py）在 run() 前覆盖

        # 两个模式实例（在 _init_modes 中初始化）
        self._chat: Optional[ChatMode] = None
        self._impl: Optional[ImplementMode] = None

    # ─── 初始化模式实例 ──────────────────────────────────────────────

    def _init_modes(self) -> None:
        """扫描完成后初始化 ChatMode 和 ImplementMode。"""
        self._chat = ChatMode(self.files, self.cfg)
        self._impl = ImplementMode(
            files=self.files,
            cfg=self.cfg,
            undo_manager=self.undo_manager,
            show_plan_fn=show_plan_table,
            show_results_fn=show_move_results,
        )

    # ─── prompt_toolkit 会话构建 ─────────────────────────────────────

    def _build_key_bindings(self) -> KeyBindings:
        """构建 Tab 键绑定（抽出为独立方法，方便单元测试）。"""
        kb = KeyBindings()

        @kb.add("tab")
        def _toggle_mode(event):
            """Tab 键切换模式。"""
            # 通过 run_in_terminal 让 prompt_toolkit 先释放终端控制权，
            # 再由 rich 写入，避免覆盖提示符中的"你"字。
            def _do_switch():
                self._switch_mode(self.mode.toggle())
            run_in_terminal(_do_switch)
            event.app.current_buffer.reset()

        return kb

    def _build_prompt_session(self) -> PromptSession:
        """构建带 Tab 键绑定的 prompt_toolkit 会话。"""
        return PromptSession(key_bindings=self._build_key_bindings())

    def _get_prompt_html(self) -> HTML:
        """返回当前模式对应的彩色提示符（prompt_toolkit HTML 格式）。"""
        color, icon = _MODE_STYLE[self.mode]
        label = self.mode.label
        return HTML(
            f'<ansigreen><b>[{icon} {label}]</b></ansigreen>'
            f' <ansiwhite>你:</ansiwhite> '
        ) if color == "cyan" else HTML(
            f'<ansired><b>[{icon} {label}]</b></ansired>'
            f' <ansiwhite>你:</ansiwhite> '
        )

    # ─── 模式切换 ────────────────────────────────────────────────────

    def _switch_mode(self, target: Mode) -> None:
        """切换到目标模式并打印反馈面板。"""
        if target == self.mode:
            return
        self.mode = target
        color, icon = _MODE_STYLE[target]
        style = f"bold {color}"
        console.print(Panel(
            f"{icon}  已切换到 [bold]{target.description}[/bold]",
            style=style,
            expand=False,
        ))
        # 打印对应模式的帮助
        if target == Mode.CHAT:
            self._chat.show_help()
        else:
            self._impl.show_help()

    # ─── 扫描与刷新 ──────────────────────────────────────────────────

    def _scan(self) -> bool:
        """扫描目录，返回是否扫描到文件。"""
        roots = self.cfg.scan_dirs
        console.print(f"\n📂 扫描目录: {', '.join(roots)}")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("扫描文件...", total=None)
            self.files = scan_directories(roots, self.cfg)
            progress.update(task, description="提取文件内容...")
            if self.cfg.max_content_chars > 0:
                self.files = enrich_file_list(self.files, self.cfg)
        return bool(self.files)

    def _rescan(self) -> None:
        """重新扫描并刷新两个模式的文件列表。"""
        if not self._scan():
            return
        self._chat.refresh(self.files)
        self._impl.refresh(self.files)
        self.undo_manager.clear()  # 文件结构已变，旧的撤销历史失效
        console.print(f"[dim]🔄 已刷新，发现 {len(self.files)} 个文件[/dim]")

    # ─── 主入口 ──────────────────────────────────────────────────────

    def run(self) -> None:
        """启动 Agent，进入交互循环。"""
        console.print(Panel(
            "[bold cyan]🤖 智能文件夹管家 v3.0[/bold cyan]\n"
            "双模式架构 ─  [cyan]Chat（只读探索）[/cyan]  ·  [red]Implement（执行整理）[/red]\n"
            "[dim]Tab 键切换模式  ·  :help 查看命令[/dim]",
            expand=False,
        ))

        # 初次扫描
        if not self._scan():
            console.print("[yellow]⚠️ 未发现任何文件，请检查扫描目录。[/yellow]")
            return

        console.print(f"📂 共发现 [bold]{len(self.files)}[/bold] 个文件")

        # 初始化模式
        self._init_modes()

        # 如果命令行指定了 --mode implement，直接进入 Implement 模式
        if self._start_mode == "implement":
            self.mode = Mode.IMPLEMENT
            color, icon = _MODE_STYLE[Mode.IMPLEMENT]
            console.print(Panel(
                f"{icon}  已进入 [bold]{Mode.IMPLEMENT.description}[/bold]",
                style=f"bold {color}",
                expand=False,
            ))
            self._impl.show_help()
        else:
            # 进入 Chat 模式，展示统计摘要
            console.print(Panel(
                f"[cyan]🔵 已进入 Chat 模式 — {Mode.CHAT.description}[/cyan]\n"
                "[dim]输入文件相关问题，或输入 :mode implement 切换到执行模式[/dim]",
                expand=False,
            ))
            self._chat.show_summary()
            self._chat.show_help()

        # 构建 prompt_toolkit 会话
        session = self._build_prompt_session()

        # 主循环
        while True:
            try:
                user_text = session.prompt(self._get_prompt_html).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]👋 再见！[/dim]")
                break

            if not user_text:
                continue

            # ── 全局命令（两种模式都支持） ──────────────────────────────
            cmd_lower = user_text.lower().strip()

            if cmd_lower in (":exit", "/exit", "exit"):
                console.print("[dim]👋 再见！[/dim]")
                break

            if cmd_lower in (":mode chat", "/mode chat"):
                self._switch_mode(Mode.CHAT)
                continue

            if cmd_lower in (":mode implement", ":mode impl", "/mode implement"):
                self._switch_mode(Mode.IMPLEMENT)
                continue

            if cmd_lower in (":help", "/help"):
                if self.mode == Mode.CHAT:
                    self._chat.show_help()
                else:
                    self._impl.show_help()
                continue

            if cmd_lower == ":rescan":
                self._rescan()
                continue

            # ── 路由到当前模式 ────────────────────────────────────────
            if self.mode == Mode.CHAT:
                self._handle_chat(user_text)
            else:
                self._handle_implement(user_text)

    # ─── Chat 模式命令处理 ────────────────────────────────────────────

    def _handle_chat(self, user_text: str) -> None:
        cmd = user_text.lower().split()

        if cmd[0] == "/summary":
            self._chat.show_summary()

        elif cmd[0] == "/clear":
            self._chat.clear_history()
            console.print("[dim]💬 对话历史已清空。[/dim]")

        else:
            # 发给 LLM（ask() 内部已流式打印，无需再 print）
            reply, suggest_implement = self._chat.ask(user_text)
            if suggest_implement:
                console.print(
                    "[dim]💡 提示：按 Tab 或输入 :mode implement 切换到执行模式。[/dim]"
                )

    # ─── Implement 模式命令处理 ───────────────────────────────────────

    def _handle_implement(self, user_text: str) -> None:
        cmd = user_text.lower().split()

        if cmd[0] == "/show":
            self._impl.show_plan()

        elif cmd[0] == "/dryrun":
            self._impl.preview()
            console.print(
                "\n[dim]💡 下一步操作："
                "输入 [bold]/run[/bold] 确认执行，"
                "或用自然语言描述调整方案，"
                "或输入 [bold]/show[/bold] 查看完整分类方案。[/dim]"
            )

        elif cmd[0] == "/run":
            success, _ = self._impl.execute()
            if success:
                # 执行后刷新文件列表
                if not self._scan():
                    return
                self._chat.refresh(self.files)
                self._impl.refresh(self.files)
                # 注意：不清空 undo_manager，它由 ImplementMode 自己管理

        elif cmd[0] == "/undo":
            did_undo = self._impl.undo()
            if did_undo:
                if not self._scan():
                    return
                self._chat.refresh(self.files)
                self._impl.refresh(self.files)

        elif cmd[0] == "/undo-status":
            self._impl.show_undo_status()

        elif cmd[0] == "/save":
            path = cmd[1] if len(cmd) > 1 else "plan.json"
            save_plan_json(self._impl.plan, path)

        elif cmd[0] == "/load":
            path = cmd[1] if len(cmd) > 1 else "plan.json"
            loaded = load_plan_json(path, self.files)
            if loaded is not None:
                self._impl.plan = loaded
                self._impl.show_plan()

        elif cmd[0] == "/clear":
            self._impl.clear_history()
            console.print("[dim]💬 LLM 对话历史已清空。[/dim]")

        else:
            # 自然语言指令 → LLM 更新方案
            reply = self._impl.ask_for_plan(user_text)
            _stream_print(reply)
            self._impl.show_plan()
