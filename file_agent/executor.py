"""
文件移动与回滚模块 ─ 负责按分类方案执行物理移动。

功能:
    - dry-run 预演（不修改磁盘）
    - 冲突重命名（同名文件追加 _1, _2 …）
    - 异常捕获 + 回滚（逆序将已移动文件移回原位）
    - 清理空目录
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import AgentConfig
from .scanner import FileInfo
from .types import FilePlan
from .plan_validation import is_safe_target_dir_name


@dataclass
class MoveRecord:
    """记录一次移动操作的完整信息。"""

    src: Path           # 原始绝对路径
    dst: Path           # 目标绝对路径
    rel_path: str       # 文件相对路径（用于显示）
    target_dir: str     # 目标文件夹名
    success: Optional[bool] = None  # True=成功, False=失败, None=dry-run
    error: str = ""


# ==========================================
# 冲突重命名
# ==========================================

def _resolve_conflict(dest_path: Path) -> Path:
    """若目标路径已存在，追加 _1, _2 … 直到不冲突。"""
    if not dest_path.exists():
        return dest_path

    stem = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent

    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        candidate = parent / new_name
        if not candidate.exists():
            return candidate
        counter += 1


# ==========================================
# 核心执行
# ==========================================

def execute_plan(
    plan: FilePlan,
    files: List[FileInfo],
    cfg: AgentConfig,
    dry_run: bool = False,
) -> List[MoveRecord]:
    """按分类方案执行文件移动。

    Parameters
    ----------
    plan : FilePlan
        {相对路径: 目标文件夹名}
    files : list[FileInfo]
        扫描得到的文件列表（用于查询绝对路径和根目录）。
    cfg : AgentConfig
    dry_run : bool
        True 时仅预演，不修改磁盘。

    Returns
    -------
    list[MoveRecord]
    """
    records: List[MoveRecord] = []

    # 建立快速查找映射：rel_path -> FileInfo
    fi_map: Dict[str, FileInfo] = {fi.rel_path: fi for fi in files}

    for rel_path, target_dir_name in plan.items():
        fi = fi_map.get(rel_path)
        if fi is None:
            records.append(MoveRecord(
                src=Path(rel_path),
                dst=Path(rel_path),
                rel_path=rel_path,
                target_dir=target_dir_name,
                success=False,
                error="文件未在扫描结果中找到",
            ))
            continue

        if not is_safe_target_dir_name(target_dir_name):
            records.append(MoveRecord(
                src=fi.path,
                dst=fi.path,
                rel_path=rel_path,
                target_dir=target_dir_name,
                success=False,
                error="非法目标目录名",
            ))
            continue

        source_path = fi.path
        # 目标目录放在对应根目录下
        root = Path(fi.root_dir)
        dest_dir = root / target_dir_name
        filename = source_path.name
        dest_path = dest_dir / filename

        if not source_path.exists():
            records.append(MoveRecord(
                src=source_path,
                dst=dest_path,
                rel_path=rel_path,
                target_dir=target_dir_name,
                success=False,
                error="源文件不存在",
            ))
            continue

        # 如果源已经在目标目录下，跳过
        if source_path.parent == dest_dir:
            records.append(MoveRecord(
                src=source_path,
                dst=source_path,
                rel_path=rel_path,
                target_dir=target_dir_name,
                success=True,
                error="已在目标目录中，无需移动",
            ))
            continue

        # 冲突重命名
        dest_path = _resolve_conflict(dest_path)

        record = MoveRecord(
            src=source_path,
            dst=dest_path,
            rel_path=rel_path,
            target_dir=target_dir_name,
        )

        if dry_run:
            record.success = None  # 标识 dry-run
            records.append(record)
            continue

        # 真实执行
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(dest_path))
            record.success = True
        except Exception as e:
            record.success = False
            record.error = str(e)

        records.append(record)

    return records


# ==========================================
# 回滚
# ==========================================

def rollback(records: List[MoveRecord]) -> List[MoveRecord]:
    """逆序将成功移动的文件移回原位。

    Returns
    -------
    list[MoveRecord]
        回滚操作的记录列表。
    """
    rollback_records: List[MoveRecord] = []

    # 只回滚 success=True 的记录，逆序处理
    successful = [r for r in records if r.success is True and r.src != r.dst]
    for record in reversed(successful):
        rb = MoveRecord(
            src=record.dst,
            dst=record.src,
            rel_path=record.rel_path,
            target_dir="(回滚)",
        )
        try:
            record.src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(record.dst), str(record.src))
            rb.success = True
        except Exception as e:
            rb.success = False
            rb.error = str(e)
        rollback_records.append(rb)

    return rollback_records


# ==========================================
# 清理空目录
# ==========================================

def remove_empty_dirs(roots: List[str]) -> int:
    """递归删除指定目录列表下的空文件夹（不删除根目录本身）。

    Returns
    -------
    int
        删除的空目录数量。
    """
    removed = 0
    for root_dir in roots:
        root_path = Path(root_dir).resolve()
        if not root_path.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(root_path), topdown=False):
            dp = Path(dirpath)
            if dp == root_path:
                continue
            try:
                if not any(dp.iterdir()):
                    dp.rmdir()
                    removed += 1
            except OSError:
                pass
    return removed
