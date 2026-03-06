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
from pathlib import Path
from typing import Dict, List, Optional

from prompt_toolkit import PromptSession
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
from .classifier import FilePlan, ask_llm_for_plan
from .executor import MoveRecord, execute_plan, rollback, remove_empty_dirs
from .undo_manager import UndoManager
from .modes import Mode, ChatMode, ImplementMode

console = Console()


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


def show_move_results(records: List[MoveRecord], dry_run: bool = False) -> None:
    """用 rich Table 展示移动结果。"""
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

    for r in records:
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

    # 统计
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


def load_plan_json(path: str) -> Optional[FilePlan]:
    """从 JSON 文件加载方案。"""
    p = Path(path)
    if not p.is_file():
        console.print(f"[red]❌ 文件不存在: {path}[/red]")
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            plan = json.load(f)
        if not isinstance(plan, dict):
            console.print("[red]❌ JSON 格式不正确，需要是一个对象[/red]")
            return None
        console.print(f"[green]✅ 已加载方案: {path} ({len(plan)} 个文件)[/green]")
        return plan
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

    def _build_prompt_session(self) -> PromptSession:
        """构建带 Tab 键绑定的 prompt_toolkit 会话。"""
        kb = KeyBindings()

        @kb.add("tab")
        def _toggle_mode(event):
            """Tab 键切换模式。"""
            self._switch_mode(self.mode.toggle())
            # 清空当前输入行并刷新提示符
            event.app.current_buffer.reset()

        return PromptSession(key_bindings=kb)

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
            # 发给 LLM
            reply, suggest_implement = self._chat.ask(user_text)
            console.print(f"\n🤖 {reply}")
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
            loaded = load_plan_json(path)
            if loaded is not None:
                self._impl.plan = loaded
                self._impl.show_plan()

        elif cmd[0] == "/clear":
            self._impl.clear_history()
            console.print("[dim]💬 LLM 对话历史已清空。[/dim]")

        else:
            # 自然语言指令 → LLM 更新方案
            reply = self._impl.ask_for_plan(user_text)
            console.print(f"\n🤖 {reply}")
            self._impl.show_plan()
