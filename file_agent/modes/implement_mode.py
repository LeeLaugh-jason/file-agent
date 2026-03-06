"""
Implement 模式 ─ 文件操作执行（带 dry-run、确认、多层撤销）。

在此模式下，agent 可以：
  - 通过 LLM 生成文件整理方案（ask_llm_for_plan）
  - 展示方案预览（dry-run）
  - 用户确认后执行物理移动
  - 多层撤销（通过 UndoManager）
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel

from ..config import AgentConfig
from ..scanner import FileInfo
from ..classifier import FilePlan, ask_llm_for_plan, normalize_plan
from ..executor import MoveRecord, execute_plan, rollback, remove_empty_dirs
from ..undo_manager import UndoManager

console = Console()

# ─── cli 层的展示函数（将由 cli.py 注入以避免循环 import） ──────────────
# ImplementMode 不直接引用 cli.show_plan_table/show_move_results，
# 而是通过依赖注入接收 show_plan / show_results 回调。


class ImplementMode:
    """文件操作执行模式。

    Parameters
    ----------
    files : list[FileInfo]
        已扫描的文件列表。
    cfg : AgentConfig
        全局配置。
    undo_manager : UndoManager
        共享的撤销历史栈（由 App 层持有，多模式共用）。
    show_plan_fn : callable
        展示分类方案的回调函数 ``(plan, files) -> None``，由 cli 层注入。
    show_results_fn : callable
        展示移动结果的回调函数 ``(records, dry_run) -> None``，由 cli 层注入。
    """

    def __init__(
        self,
        files: List[FileInfo],
        cfg: AgentConfig,
        undo_manager: UndoManager,
        show_plan_fn,
        show_results_fn,
    ) -> None:
        self.files = files
        self.cfg = cfg
        self.undo_manager = undo_manager
        self._show_plan = show_plan_fn
        self._show_results = show_results_fn

        # LLM 多轮对话历史
        self._history: Optional[List[dict]] = None

        # 当前分类方案（{rel_path: target_dir}）
        self.plan: FilePlan = {fi.rel_path: "未分类" for fi in files}

    # ─── 文件列表刷新（/run 或 /undo 后由 App 层调用） ─────────────────

    def refresh(self, files: List[FileInfo]) -> None:
        """更新文件列表，并重建 plan 以反映磁盘真实现状。"""
        self.files = files
        new_plan: FilePlan = {}
        for fi in files:
            parts = Path(fi.rel_path).parts
            current_dir = parts[0] if len(parts) > 1 else "."
            new_plan[fi.rel_path] = current_dir
        self.plan = new_plan

    # ─── LLM 交互 ────────────────────────────────────────────────────

    def ask_for_plan(self, user_input: str) -> str:
        """调用 LLM 更新整理方案，返回 LLM 的文字回复。

        使用多轮对话历史保持上下文。
        若遇到上下文过大或网络中断，捕获异常并返回友好提示，避免程序崩溃。
        """
        from openai import APIConnectionError
        try:
            new_plan, reply, self._history = ask_llm_for_plan(
                files=self.files,
                current_plan=self.plan,
                user_instruction=user_input,
                cfg=self.cfg,
                history=self._history,
            )
            self.plan = new_plan
            return reply
        except (APIConnectionError, RuntimeError) as e:
            return f"❌ 请求失败：{e}\n（提示：可尝试重新输入，或先 /show 确认现有方案）"

    def clear_history(self) -> None:
        """清空 LLM 对话历史。"""
        self._history = None

    # ─── 方案展示 ────────────────────────────────────────────────────

    def show_plan(self) -> None:
        """展示当前分类方案。"""
        self._show_plan(self.plan, self.files)

    # ─── 预演（不修改磁盘） ─────────────────────────────────────────────

    def preview(self) -> List[MoveRecord]:
        """执行 dry-run 预演并展示结果，不修改磁盘。

        Returns
        -------
        list[MoveRecord]
            预演记录列表（success=None）。
        """
        records = execute_plan(self.plan, self.files, self.cfg, dry_run=True)
        self._show_results(records, dry_run=True)
        return records

    # ─── 执行（修改磁盘） ────────────────────────────────────────────────

    def execute(self) -> Tuple[bool, List[MoveRecord]]:
        """执行物理文件移动。

        在执行前会先复述计划并要求用户确认。

        Returns
        -------
        (success: bool, records: list[MoveRecord])
            success=True 表示用户确认并执行；
            success=False 表示用户拒绝或执行中无可移动文件。
        """
        # 1. 展示预览并复述计划
        console.print("\n[bold]📋 执行前预览：[/bold]")
        records = execute_plan(self.plan, self.files, self.cfg, dry_run=True)
        self._show_results(records, dry_run=True)

        movable = [r for r in records if r.success is None and r.error == ""]
        if not movable:
            console.print("[yellow]⚠️ 没有需要移动的文件。[/yellow]")
            return False, records

        # 2. 用户确认
        try:
            confirm = console.input(
                f"\n[bold yellow]确认执行 {len(movable)} 个文件的移动？"
                f"  [bold green][y/N][/bold green][/bold yellow]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]已取消。[/dim]")
            return False, []

        if confirm not in ("y", "yes", "是", "确认"):
            console.print("[dim]已取消，文件未变动。[/dim]")
            return False, []

        # 3. 真实执行
        console.print("\n[bold]🚀 开始执行物理移动...[/bold]")
        real_records = execute_plan(self.plan, self.files, self.cfg, dry_run=False)
        self._show_results(real_records, dry_run=False)

        # 4. 清理空目录并压入 undo 栈
        removed = remove_empty_dirs(self.cfg.scan_dirs)
        if removed > 0:
            console.print(f"🧹 已清理 {removed} 个空文件夹")

        self.undo_manager.push(real_records)
        depth = self.undo_manager.depth()
        console.print(
            f"[dim]✅ 已记录到撤销历史（当前可撤销 {depth} 层）[/dim]"
        )
        console.print("\n[bold green]🎉 整理完成！[/bold green]")

        return True, real_records

    # ─── 撤销 ────────────────────────────────────────────────────────

    def undo(self) -> bool:
        """撤销上一次执行。

        Returns
        -------
        bool
            True=成功撤销，False=无可撤销操作。
        """
        if not self.undo_manager.can_undo():
            console.print("[yellow]⚠️ 没有可撤销的操作记录。[/yellow]")
            return False

        records = self.undo_manager.pop()
        if records is None:
            return False

        console.print(f"\n[bold]⏪ 正在撤销（剩余可撤销 {self.undo_manager.depth()} 层）...[/bold]")
        rb_records = rollback(records)
        ok = sum(1 for r in rb_records if r.success)
        fail = sum(1 for r in rb_records if not r.success)
        console.print(f"✅ 撤销成功 {ok} 个，失败 {fail} 个")

        removed = remove_empty_dirs(self.cfg.scan_dirs)
        if removed > 0:
            console.print(f"🧹 已清理 {removed} 个空文件夹")

        return True

    # ─── 方案 JSON 导入导出（委托给 cli 层工具函数） ─────────────────────

    def show_undo_status(self) -> None:
        """展示当前撤销历史深度。"""
        depth = self.undo_manager.depth()
        if depth == 0:
            console.print("[dim]撤销历史：空（没有可撤销的操作）[/dim]")
        else:
            console.print(f"[dim]撤销历史：可撤销 {depth} 层[/dim]")

    def show_help(self) -> None:
        """打印 Implement 模式可用命令。"""
        console.print(
            "\n[dim]⚙️ Implement 模式命令：[/dim]\n"
            "  [bold]/show[/bold]             展示当前整理方案\n"
            "  [bold]/dryrun[/bold]           预演移动（不修改文件）\n"
            "  [bold]/run[/bold]              执行移动（需确认）\n"
            "  [bold]/undo[/bold]             撤销上次执行\n"
            "  [bold]/undo-status[/bold]      查看撤销历史深度\n"
            "  [bold]/save[/bold] [文件名]    导出方案为 JSON\n"
            "  [bold]/load[/bold] [文件名]    从 JSON 加载方案\n"
            "  [bold]/clear[/bold]            清空 LLM 对话历史\n"
            "  [bold]:mode chat[/bold]        切换到 Chat 模式\n"
            "  [bold]:exit[/bold]             退出程序\n"
            "  [dim]其他文本               作为指令发给 LLM 更新方案[/dim]"
        )
