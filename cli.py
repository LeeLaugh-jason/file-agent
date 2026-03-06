"""
命令行交互模块 ─ 使用 rich 美化输出，提供完整交互循环。

交互命令:
    /show       显示当前分类方案表格
    /dryrun     预演移动（不修改磁盘）
    /run        执行移动
    /undo       回滚上次执行
    /save <f>   导出方案为 JSON
    /load <f>   从 JSON 加载方案
    /exit       退出
    其他文本     作为自然语言指令更新方案
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

from config import AgentConfig
from scanner import FileInfo, scan_directories, file_list_paths
from extractors import enrich_file_list
from classifier import FilePlan, ask_llm_for_plan
from executor import MoveRecord, execute_plan, rollback, remove_empty_dirs

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
# 主交互循环
# ==========================================

class App:
    """CLI 应用主类，管理整个交互生命周期。"""

    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.files: List[FileInfo] = []
        self.plan: FilePlan = {}
        self.history: Optional[List[dict]] = None
        self.last_records: List[MoveRecord] = []

    def run(self) -> None:
        """主入口。"""
        console.print(Panel(
            "[bold cyan]🤖 智能文件夹管家 v2.0[/bold cyan]\n"
            "模块化重构版 ─ 支持多目录、内容提取、回滚",
            expand=False,
        ))

        # --- 扫描 ---
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
            self.files = enrich_file_list(self.files, self.cfg)

        if not self.files:
            console.print("[yellow]⚠️ 未发现任何文件，请检查扫描目录。[/yellow]")
            return

        console.print(f"📂 共发现 [bold]{len(self.files)}[/bold] 个文件")

        # --- 初始分类 ---
        self.plan = {fi.rel_path: "未分类" for fi in self.files}

        first_instruction = console.input(
            "\n[bold]请输入你希望的整理方式（例如：按课程名分类）：[/bold] "
        ).strip()
        if not first_instruction:
            first_instruction = "请先给出一个合理的初始分类方案。"

        self.plan, reply, self.history = ask_llm_for_plan(
            self.files, self.plan, first_instruction, self.cfg, self.history,
        )
        console.print(f"\n🤖 {reply}")
        show_plan_table(self.plan, self.files)

        # --- 多轮交互 ---
        self._print_help()

        while True:
            try:
                user_text = console.input("\n[bold green]你:[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]退出[/dim]")
                break

            if not user_text:
                continue

            cmd = user_text.lower().split()

            if cmd[0] == "/show":
                show_plan_table(self.plan, self.files)

            elif cmd[0] == "/exit":
                console.print("\n🛑 已取消，文件保持原位。")
                break

            elif cmd[0] == "/dryrun":
                records = execute_plan(self.plan, self.files, self.cfg, dry_run=True)
                show_move_results(records, dry_run=True)

            elif cmd[0] == "/run":
                self._do_run()

            elif cmd[0] == "/undo":
                self._do_undo()

            elif cmd[0] == "/save":
                path = cmd[1] if len(cmd) > 1 else "plan.json"
                save_plan_json(self.plan, path)

            elif cmd[0] == "/load":
                path = cmd[1] if len(cmd) > 1 else "plan.json"
                loaded = load_plan_json(path)
                if loaded is not None:
                    self.plan = loaded
                    show_plan_table(self.plan, self.files)

            elif cmd[0] == "/help":
                self._print_help()

            else:
                # 自然语言指令 → 更新方案
                self.plan, reply, self.history = ask_llm_for_plan(
                    self.files, self.plan, user_text, self.cfg, self.history,
                )
                console.print(f"\n🤖 {reply}")
                show_plan_table(self.plan, self.files)

    def _do_run(self) -> None:
        """执行移动并清理空目录。"""
        console.print("\n[bold]🚀 开始执行物理移动...[/bold]")
        self.last_records = execute_plan(self.plan, self.files, self.cfg, dry_run=False)
        show_move_results(self.last_records, dry_run=False)

        # 清理空目录
        removed = remove_empty_dirs(self.cfg.scan_dirs)
        if removed > 0:
            console.print(f"🧹 已清理 {removed} 个空文件夹")

        console.print("\n[bold green]🎉 整理完成！[/bold green]")

        # 刷新文件列表（文件已移动到新位置，旧路径失效）
        self._rescan(silent=True)

    def _do_undo(self) -> None:
        """回滚上次执行，并清理回滚后产生的空文件夹。"""
        if not self.last_records:
            console.print("[yellow]⚠️ 没有可回滚的操作记录[/yellow]")
            return

        console.print("\n[bold]⏪ 正在回滚...[/bold]")
        rb_records = rollback(self.last_records)
        ok = sum(1 for r in rb_records if r.success)
        fail = sum(1 for r in rb_records if not r.success)
        console.print(f"✅ 回滚成功 {ok} 个，失败 {fail} 个")

        # 清理回滚后残留的空文件夹
        removed = remove_empty_dirs(self.cfg.scan_dirs)
        if removed > 0:
            console.print(f"🧹 已清理 {removed} 个空文件夹")

        self.last_records = []

        # 刷新文件列表（文件已移回原位，路径重新有效）
        self._rescan(silent=True)

    def _rescan(self, silent: bool = False) -> None:
        """重新扫描目录，刷新 self.files 与 self.plan。

        在 /run 或 /undo 后调用，防止下一轮操作使用失效路径。
        silent=True 时不打印提示信息。
        """
        if not silent:
            console.print("[dim]🔄 正在刷新文件列表...[/dim]")

        self.files = scan_directories(self.cfg.scan_dirs, self.cfg)
        if self.cfg.max_content_chars > 0:
            self.files = enrich_file_list(self.files, self.cfg)

        # 用文件当前所在的第一级目录名重建 plan，反映磁盘真实现状
        new_plan: FilePlan = {}
        for fi in self.files:
            parts = Path(fi.rel_path).parts
            current_dir = parts[0] if len(parts) > 1 else "."
            new_plan[fi.rel_path] = current_dir
        self.plan = new_plan

        if not silent:
            console.print(f"[dim]✅ 已发现 {len(self.files)} 个文件[/dim]")

    @staticmethod
    def _print_help() -> None:
        console.print(
            "\n[dim]💬 交互命令:[/dim]\n"
            "  [bold]/show[/bold]       查看当前方案\n"
            "  [bold]/dryrun[/bold]     预演移动（不修改文件）\n"
            "  [bold]/run[/bold]        执行移动\n"
            "  [bold]/undo[/bold]       撤销上次移动\n"
            "  [bold]/save[/bold] [f]   导出方案为 JSON\n"
            "  [bold]/load[/bold] [f]   从 JSON 加载方案\n"
            "  [bold]/help[/bold]       显示此帮助\n"
            "  [bold]/exit[/bold]       退出\n"
            "  [dim]其他文本     作为自然语言指令更新方案[/dim]"
        )
