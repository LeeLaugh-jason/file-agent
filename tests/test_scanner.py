"""
单元测试 ─ scanner.py
"""

import os
from datetime import datetime
from pathlib import Path

import pytest

from file_agent.config import AgentConfig
from file_agent.scanner import FileInfo, scan_directories, file_list_paths, file_list_metadata


class TestScanDirectories:
    def _make_tree(self, tmp_path):
        """创建测试目录结构：
        tmp/
            a/
                hello.py
                data.csv
                .git/
                    config
            b/
                report.docx
                sub/
                    deep.txt
        """
        a = tmp_path / "a"
        a.mkdir()
        (a / "hello.py").write_text("print('hi')", encoding="utf-8")
        (a / "data.csv").write_text("a,b\n1,2", encoding="utf-8")
        git = a / ".git"
        git.mkdir()
        (git / "config").write_text("[core]", encoding="utf-8")

        b = tmp_path / "b"
        b.mkdir()
        (b / "report.docx").write_bytes(b"fake-docx")
        sub = b / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("deep content", encoding="utf-8")

        return a, b

    def test_single_root(self, tmp_path):
        a, b = self._make_tree(tmp_path)
        cfg = AgentConfig(ignore_dirs=[".git"])
        files = scan_directories([str(a)], cfg)
        paths = [fi.rel_path for fi in files]
        # .git/config 应被忽略
        assert any("hello.py" in p for p in paths)
        assert any("data.csv" in p for p in paths)
        assert not any(".git" in p for p in paths)

    def test_multi_root(self, tmp_path):
        a, b = self._make_tree(tmp_path)
        cfg = AgentConfig(ignore_dirs=[".git"])
        files = scan_directories([str(a), str(b)], cfg)

        paths = [fi.rel_path for fi in files]
        # 多根目录模式：应加根目录前缀
        assert any("a" in p and "hello.py" in p for p in paths)
        assert any("b" in p and "report.docx" in p for p in paths)
        assert any("deep.txt" in p for p in paths)

    def test_ignore_extensions(self, tmp_path):
        a, _ = self._make_tree(tmp_path)
        cfg = AgentConfig(ignore_dirs=[".git"], ignore_extensions=[".csv"])
        files = scan_directories([str(a)], cfg)
        paths = [fi.rel_path for fi in files]
        assert not any("data.csv" in p for p in paths)
        assert any("hello.py" in p for p in paths)

    def test_nonexistent_root(self, tmp_path):
        cfg = AgentConfig()
        files = scan_directories([str(tmp_path / "nosuchdir")], cfg)
        assert files == []

    def test_file_info_fields(self, tmp_path):
        a, _ = self._make_tree(tmp_path)
        cfg = AgentConfig(ignore_dirs=[".git"])
        files = scan_directories([str(a)], cfg)
        py_file = [f for f in files if f.ext == ".py"][0]
        assert py_file.size_bytes > 0
        assert isinstance(py_file.modified_at, datetime)
        assert py_file.root_dir == str(Path(a).resolve())


class TestHelpers:
    def test_file_list_paths(self):
        fi = FileInfo(path=Path("/a/b.py"), rel_path="b.py", root_dir="/a", ext=".py")
        assert file_list_paths([fi]) == ["b.py"]

    def test_file_list_metadata(self):
        fi = FileInfo(
            path=Path("/a/b.py"),
            rel_path="b.py",
            root_dir="/a",
            ext=".py",
            size_bytes=100,
            modified_at=datetime(2025, 1, 1, 12, 0),
            content_summary="hello world",
        )
        meta = file_list_metadata([fi])
        assert len(meta) == 1
        assert meta[0]["ext"] == ".py"
        assert meta[0]["size_bytes"] == 100
        assert "hello" in meta[0]["content_summary"]


class TestIgnoreDirPaths:
    def _make_dataset_tree(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir(parents=True)

        (root / "datasets" / "raw").mkdir(parents=True)
        (root / "datasets" / "raw" / "dup1.csv").write_text("x", encoding="utf-8")
        (root / "datasets" / "raw" / "dup2.csv").write_text("x", encoding="utf-8")

        (root / "datasets" / "processed").mkdir(parents=True)
        (root / "datasets" / "processed" / "keep.csv").write_text("y", encoding="utf-8")

        (root / "notes").mkdir(parents=True)
        (root / "notes" / "todo.txt").write_text("todo", encoding="utf-8")

        return root

    def test_ignore_nested_dir_path(self, tmp_path):
        root = self._make_dataset_tree(tmp_path)
        cfg = AgentConfig(ignore_dir_paths=["datasets/raw"])

        files = scan_directories([str(root)], cfg)
        paths = [fi.rel_path for fi in files]

        assert not any("datasets/raw" in p.replace("\\", "/") for p in paths)
        assert any("datasets/processed/keep.csv" in p.replace("\\", "/") for p in paths)
        assert any("notes/todo.txt" in p.replace("\\", "/") for p in paths)

    def test_multi_root_applies_ignore_per_root(self, tmp_path):
        root1 = self._make_dataset_tree(tmp_path / "r1")
        root2 = self._make_dataset_tree(tmp_path / "r2")
        cfg = AgentConfig(ignore_dir_paths=["datasets/raw"])

        files = scan_directories([str(root1), str(root2)], cfg)
        paths = [fi.rel_path.replace("\\", "/") for fi in files]

        assert not any("datasets/raw" in p for p in paths)
        assert any(p.endswith("datasets/processed/keep.csv") for p in paths)

    def test_ignore_dirs_and_ignore_dir_paths_can_coexist(self, tmp_path):
        root = self._make_dataset_tree(tmp_path)
        (root / "node_modules").mkdir()
        (root / "node_modules" / "pkg.json").write_text("{}", encoding="utf-8")

        cfg = AgentConfig(ignore_dirs=["node_modules"], ignore_dir_paths=["datasets/raw"])
        files = scan_directories([str(root)], cfg)
        paths = [fi.rel_path.replace("\\", "/") for fi in files]

        assert not any("datasets/raw" in p for p in paths)
        assert not any("node_modules" in p for p in paths)
        assert any("datasets/processed/keep.csv" in p for p in paths)
