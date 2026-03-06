"""
扫描模块 ─ 递归扫描多个目录，收集文件元信息。

核心数据结构 FileInfo 是后续所有模块的通用文件描述对象。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

from config import AgentConfig


@dataclass
class FileInfo:
    """单个文件的完整描述信息。"""

    path: Path                   # 绝对路径
    rel_path: str                # 相对于对应根目录的路径（含根目录前缀）
    root_dir: str                # 所属根目录（原始扫描目录）
    ext: str = ""                # 小写扩展名，如 ".docx"
    size_bytes: int = 0
    modified_at: datetime = field(default_factory=datetime.now)
    content_summary: str = ""    # 由 extractors 填充


def _should_ignore_dir(dir_name: str, ignore_dirs: List[str]) -> bool:
    """判断某个目录名是否在忽略列表中（仅按目录名匹配，不含路径）。"""
    return dir_name in ignore_dirs


def _should_ignore_file(filename: str, ignore_extensions: List[str]) -> bool:
    """判断某个文件是否应被忽略（按扩展名）。"""
    if not ignore_extensions:
        return False
    _, ext = os.path.splitext(filename)
    return ext.lower() in ignore_extensions


def scan_directories(roots: List[str], cfg: AgentConfig) -> List[FileInfo]:
    """递归扫描多个根目录，返回 FileInfo 列表。

    Parameters
    ----------
    roots : list[str]
        要扫描的根目录路径列表。
    cfg : AgentConfig
        全局配置（用于读取忽略规则）。

    Returns
    -------
    list[FileInfo]
        排序后的文件信息列表（按 rel_path 排序，便于稳定输出）。
    """
    results: List[FileInfo] = []

    for root_dir in roots:
        root_path = Path(root_dir).resolve()
        if not root_path.is_dir():
            print(f"⚠️ 扫描目录不存在，跳过: {root_dir}")
            continue

        root_label = root_path.name  # 用目录名作为前缀标识

        for dirpath, dirnames, filenames in os.walk(str(root_path)):
            # 原地修改 dirnames 以跳过忽略目录
            dirnames[:] = [
                d for d in dirnames
                if not _should_ignore_dir(d, cfg.ignore_dirs)
            ]

            for filename in filenames:
                if _should_ignore_file(filename, cfg.ignore_extensions):
                    continue

                full_path = Path(dirpath) / filename
                try:
                    stat = full_path.stat()
                    modified_at = datetime.fromtimestamp(stat.st_mtime)
                    size_bytes = stat.st_size
                except OSError:
                    modified_at = datetime.now()
                    size_bytes = 0

                _, ext = os.path.splitext(filename)

                # rel_path 相对于该根目录
                try:
                    inner_rel = full_path.relative_to(root_path)
                except ValueError:
                    inner_rel = Path(filename)

                # 当有多个根目录时，加上根目录名前缀避免冲突
                if len(roots) > 1:
                    rel_path_str = str(Path(root_label) / inner_rel)
                else:
                    rel_path_str = str(inner_rel)

                results.append(FileInfo(
                    path=full_path,
                    rel_path=rel_path_str,
                    root_dir=str(root_path),
                    ext=ext.lower(),
                    size_bytes=size_bytes,
                    modified_at=modified_at,
                ))

    results.sort(key=lambda fi: fi.rel_path)
    return results


# --------------- 辅助函数 ---------------

def file_list_paths(files: List[FileInfo]) -> List[str]:
    """提取 rel_path 列表（兼容旧代码中 file_list 传参）。"""
    return [fi.rel_path for fi in files]


def file_list_metadata(files: List[FileInfo]) -> List[dict]:
    """生成与旧版 get_file_metadata 兼容的字典列表。"""
    return [
        {
            "path": fi.rel_path,
            "ext": fi.ext if fi.ext else "无扩展名",
            "size_bytes": fi.size_bytes,
            "modified_at": fi.modified_at.strftime("%Y-%m-%d %H:%M:%S"),
            "content_summary": fi.content_summary[:200] if fi.content_summary else "",
        }
        for fi in files
    ]
