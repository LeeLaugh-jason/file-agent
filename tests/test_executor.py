"""
单元测试 ─ executor.py
"""

import importlib
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from file_agent.config import AgentConfig
from file_agent.scanner import FileInfo
from file_agent.executor import (
    MoveRecord,
    execute_plan,
    rollback,
    remove_empty_dirs,
    _resolve_conflict,
)


def _make_files(tmp_path):
    """创建测试文件：
    tmp/
        root/
            a.txt
            sub/
                b.txt
    返回 (root_dir, files)
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("aaa", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("bbb", encoding="utf-8")

    files = [
        FileInfo(
            path=root / "a.txt",
            rel_path="a.txt",
            root_dir=str(root),
            ext=".txt",
            size_bytes=3,
        ),
        FileInfo(
            path=sub / "b.txt",
            rel_path="sub\\b.txt",
            root_dir=str(root),
            ext=".txt",
            size_bytes=3,
        ),
    ]
    return root, files


class TestResolveConflict:
    def test_no_conflict(self, tmp_path):
        p = tmp_path / "new.txt"
        assert _resolve_conflict(p) == p

    def test_conflict_rename(self, tmp_path):
        p = tmp_path / "dup.txt"
        p.write_text("x", encoding="utf-8")
        resolved = _resolve_conflict(p)
        assert resolved.name == "dup_1.txt"

    def test_multiple_conflicts(self, tmp_path):
        p = tmp_path / "dup.txt"
        p.write_text("x", encoding="utf-8")
        (tmp_path / "dup_1.txt").write_text("x", encoding="utf-8")
        (tmp_path / "dup_2.txt").write_text("x", encoding="utf-8")
        resolved = _resolve_conflict(p)
        assert resolved.name == "dup_3.txt"


class TestExecutePlan:
    def test_dry_run(self, tmp_path):
        root, files = _make_files(tmp_path)
        cfg = AgentConfig(scan_dirs=[str(root)])
        plan = {"a.txt": "分类A", "sub\\b.txt": "分类B"}
        records = execute_plan(plan, files, cfg, dry_run=True)
        assert all(r.success is None for r in records)
        # 文件应该没有被移动
        assert (root / "a.txt").exists()
        assert (root / "sub" / "b.txt").exists()

    def test_real_move(self, tmp_path):
        root, files = _make_files(tmp_path)
        cfg = AgentConfig(scan_dirs=[str(root)])
        plan = {"a.txt": "分类A", "sub\\b.txt": "分类B"}
        records = execute_plan(plan, files, cfg, dry_run=False)
        success = [r for r in records if r.success is True]
        assert len(success) == 2
        assert (root / "分类A" / "a.txt").exists()
        assert (root / "分类B" / "b.txt").exists()

    def test_conflict_rename_on_move(self, tmp_path):
        root, files = _make_files(tmp_path)
        # 预先创建冲突文件
        target = root / "分类A"
        target.mkdir()
        (target / "a.txt").write_text("conflict", encoding="utf-8")

        cfg = AgentConfig(scan_dirs=[str(root)])
        plan = {"a.txt": "分类A"}
        records = execute_plan(plan, [files[0]], cfg, dry_run=False)
        assert records[0].success is True
        assert records[0].dst.name == "a_1.txt"

    def test_source_not_found(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        fi = FileInfo(
            path=root / "ghost.txt",
            rel_path="ghost.txt",
            root_dir=str(root),
            ext=".txt",
        )
        cfg = AgentConfig(scan_dirs=[str(root)])
        records = execute_plan({"ghost.txt": "X"}, [fi], cfg, dry_run=False)
        assert records[0].success is False



    @pytest.mark.parametrize("target_dir", ["../x", "a/b", "/tmp/evil", "..", ""])
    def test_rejects_unsafe_target_dir_dry_run(self, tmp_path, target_dir):
        root, files = _make_files(tmp_path)
        cfg = AgentConfig(scan_dirs=[str(root)])
        records = execute_plan({"a.txt": target_dir}, [files[0]], cfg, dry_run=True)
        assert len(records) == 1
        assert records[0].success is False
        assert "非法目标目录名" in records[0].error
        assert (root / "a.txt").exists()

    @pytest.mark.parametrize("target_dir", ["../x", "a/b", "/tmp/evil", "..", ""])
    def test_rejects_unsafe_target_dir_real_run(self, tmp_path, target_dir):
        root, files = _make_files(tmp_path)
        cfg = AgentConfig(scan_dirs=[str(root)])
        records = execute_plan({"a.txt": target_dir}, [files[0]], cfg, dry_run=False)
        assert len(records) == 1
        assert records[0].success is False
        assert "非法目标目录名" in records[0].error
        assert (root / "a.txt").exists()


class TestExecutorImportIndependence:
    def test_executor_importable_without_openai(self):
        with patch.dict(sys.modules, {"openai": None}):
            mod = importlib.reload(importlib.import_module("file_agent.executor"))
        assert hasattr(mod, "execute_plan")


class TestRollback:
    def test_rollback_success(self, tmp_path):
        root, files = _make_files(tmp_path)
        cfg = AgentConfig(scan_dirs=[str(root)])
        plan = {"a.txt": "分类A"}
        records = execute_plan(plan, [files[0]], cfg, dry_run=False)
        assert not (root / "a.txt").exists()
        assert (root / "分类A" / "a.txt").exists()

        rb = rollback(records)
        assert rb[0].success is True
        assert (root / "a.txt").exists()

    def test_rollback_empty(self):
        rb = rollback([])
        assert rb == []


class TestRemoveEmptyDirs:
    def test_remove(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (root / "empty1").mkdir()
        (root / "empty2").mkdir()
        (root / "notempty").mkdir()
        (root / "notempty" / "file.txt").write_text("x", encoding="utf-8")
        removed = remove_empty_dirs([str(root)])
        assert removed == 2
        assert not (root / "empty1").exists()
        assert (root / "notempty").exists()
