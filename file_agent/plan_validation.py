"""计划与目标目录名安全校验。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set, Tuple

from .types import FilePlan


INVALID_SEGMENT = ".."


def is_safe_target_dir_name(target_dir_name: str) -> bool:
    """仅允许单级目录名，拒绝路径穿越与多级路径。"""
    if not isinstance(target_dir_name, str):
        return False

    name = target_dir_name.strip()
    if not name:
        return False
    if name in {".", INVALID_SEGMENT}:
        return False

    p = Path(name)
    if p.is_absolute():
        return False
    if len(p.parts) != 1:
        return False
    if "/" in name or "\\" in name:
        return False

    return True


def validate_loaded_plan(plan: object, valid_keys: Set[str]) -> Tuple[Optional[FilePlan], List[str]]:
    """验证导入计划的结构与语义。"""
    if not isinstance(plan, dict):
        return None, ["JSON 顶层必须是对象（dict）"]

    normalized: FilePlan = {}
    errors: List[str] = []

    for key, value in plan.items():
        if key not in valid_keys:
            errors.append(f"非法 key: {key}")
            continue
        if not is_safe_target_dir_name(value):
            errors.append(f"非法目标目录名: {key} -> {value}")
            continue
        normalized[key] = value

    if errors:
        return None, errors

    return normalized, []
