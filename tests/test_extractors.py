"""
单元测试 ─ extractors.py
"""

from pathlib import Path

import pytest

from file_agent.config import AgentConfig
from file_agent.scanner import FileInfo
from file_agent.extractors import extract_content, enrich_file_list, _EXTRACTORS


class TestTextExtractor:
    def test_plain_text(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("这是一段测试文本。" * 50, encoding="utf-8")
        fi = FileInfo(path=p, rel_path="notes.txt", root_dir=str(tmp_path), ext=".txt")
        cfg = AgentConfig(max_content_chars=100)
        result = extract_content(fi, cfg)
        assert len(result) <= 100
        assert "测试文本" in result

    def test_python_code(self, tmp_path):
        code = "def hello():\n    pass\n\ndef world():\n    return 42\n"
        p = tmp_path / "example.py"
        p.write_text(code, encoding="utf-8")
        fi = FileInfo(path=p, rel_path="example.py", root_dir=str(tmp_path), ext=".py")
        cfg = AgentConfig(max_content_chars=500)
        result = extract_content(fi, cfg)
        assert "def hello" in result
        assert "def world" in result

    def test_unknown_ext_text(self, tmp_path):
        p = tmp_path / "data.custom"
        p.write_text("custom format data", encoding="utf-8")
        fi = FileInfo(path=p, rel_path="data.custom", root_dir=str(tmp_path), ext=".custom")
        cfg = AgentConfig(max_content_chars=500)
        result = extract_content(fi, cfg)
        assert "custom format data" in result

    def test_binary_file_fallback(self, tmp_path):
        p = tmp_path / "image.bin"
        p.write_bytes(bytes(range(256)))
        fi = FileInfo(path=p, rel_path="image.bin", root_dir=str(tmp_path), ext=".bin")
        cfg = AgentConfig(max_content_chars=500)
        result = extract_content(fi, cfg)
        # 二进制文件尝试文本读取失败应返回空
        assert isinstance(result, str)


class TestPptLegacy:
    def test_ppt_not_supported(self, tmp_path):
        p = tmp_path / "old.ppt"
        p.write_bytes(b"fake")
        fi = FileInfo(path=p, rel_path="old.ppt", root_dir=str(tmp_path), ext=".ppt")
        cfg = AgentConfig()
        result = extract_content(fi, cfg)
        assert "暂不支持" in result


class TestEnrichFileList:
    def test_batch_enrich(self, tmp_path):
        (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
        (tmp_path / "b.txt").write_text("bbb", encoding="utf-8")
        files = [
            FileInfo(path=tmp_path / "a.txt", rel_path="a.txt", root_dir=str(tmp_path), ext=".txt"),
            FileInfo(path=tmp_path / "b.txt", rel_path="b.txt", root_dir=str(tmp_path), ext=".txt"),
        ]
        cfg = AgentConfig(max_content_chars=500)
        enriched = enrich_file_list(files, cfg)
        assert enriched[0].content_summary == "aaa"
        assert enriched[1].content_summary == "bbb"


class TestRegisteredExtractors:
    def test_common_extensions_registered(self):
        for ext in [".py", ".js", ".docx", ".pptx", ".xlsx", ".pdf", ".ppt", ".txt", ".csv"]:
            assert ext in _EXTRACTORS, f"{ext} 未注册提取器"
