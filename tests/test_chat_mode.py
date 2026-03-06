"""
tests/test_chat_mode.py — ChatMode 单元测试（本地计算部分，不调用 LLM）
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from file_agent.config import AgentConfig
from file_agent.scanner import FileInfo
from file_agent.modes.chat_mode import ChatMode, build_context_summary, _fmt_size


def _make_file(rel_path: str, ext: str, size: int, summary: str = "") -> FileInfo:
    return FileInfo(
        path=Path("/tmp") / rel_path,
        rel_path=rel_path,
        root_dir="/tmp",
        ext=ext,
        size_bytes=size,
        modified_at=datetime(2025, 1, 1),
        content_summary=summary,
    )


@pytest.fixture
def sample_files():
    return [
        _make_file("docs/report.docx", ".docx", 1024 * 50, "季度报告摘要"),
        _make_file("docs/slides.pptx", ".pptx", 1024 * 200),
        _make_file("code/main.py", ".py", 1024 * 10, "主程序入口"),
        _make_file("code/utils.py", ".py", 1024 * 5),
        _make_file("data/table.xlsx", ".xlsx", 1024 * 30),
        _make_file("README.md", ".md", 512, "项目说明"),
    ]


@pytest.fixture
def cfg():
    c = AgentConfig()
    c.api_key = "test_key"
    c.max_content_chars = 500
    return c


class TestFmtSize:
    def test_bytes(self):
        assert _fmt_size(500) == "500 B"

    def test_kb(self):
        assert "KB" in _fmt_size(2048)

    def test_mb(self):
        assert "MB" in _fmt_size(1024 * 1024 * 2)


class TestBuildContextSummary:
    def test_empty_files(self):
        result = build_context_summary([])
        assert "无文件" in result

    def test_contains_total_count(self, sample_files):
        result = build_context_summary(sample_files)
        assert "6" in result  # 总文件数

    def test_contains_ext_distribution(self, sample_files):
        result = build_context_summary(sample_files)
        assert ".py" in result
        assert ".docx" in result

    def test_contains_content_summary(self, sample_files):
        result = build_context_summary(sample_files)
        assert "季度报告摘要" in result

    def test_valid_json(self, sample_files):
        import json
        result = build_context_summary(sample_files)
        data = json.loads(result)
        assert "总文件数" in data
        assert data["总文件数"] == 6


class TestChatMode:
    def test_init(self, sample_files, cfg):
        chat = ChatMode(sample_files, cfg)
        assert chat.files == sample_files
        assert chat._history == []

    def test_refresh_clears_context_cache(self, sample_files, cfg):
        chat = ChatMode(sample_files, cfg)
        _ = chat._get_context_summary()  # 触发缓存
        assert chat._context_summary is not None

        new_files = sample_files[:3]
        chat.refresh(new_files)
        assert chat._context_summary is None  # 缓存已清除
        assert chat.files == new_files

    def test_clear_history(self, sample_files, cfg):
        chat = ChatMode(sample_files, cfg)
        chat._history = [{"role": "user", "content": "test"}]
        chat.clear_history()
        assert chat._history == []

    def test_ask_calls_llm_and_returns_reply(self, sample_files, cfg):
        """Mock 流式 LLM 调用，验证 ask() 返回完整拼接结果。"""
        chat = ChatMode(sample_files, cfg)

        def _make_chunk(text):
            delta = MagicMock()
            delta.content = text
            choice = MagicMock()
            choice.delta = delta
            chunk = MagicMock()
            chunk.choices = [choice]
            return chunk

        tokens = ["目录中有", " 6 个", "文件。"]
        stream_iter = iter([_make_chunk(t) for t in tokens])

        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = stream_iter
            MockOpenAI.return_value = mock_client

            reply, _ = chat.ask("有多少个文件？")

        assert reply == "目录中有 6 个文件。"

    def test_ask_detects_implement_suggestion(self, sample_files, cfg):
        """当 LLM 回复中包含 Implement 模式建议时，suggest_implement=True。"""
        chat = ChatMode(sample_files, cfg)

        def _make_chunk(text):
            delta = MagicMock()
            delta.content = text
            choice = MagicMock()
            choice.delta = delta
            chunk = MagicMock()
            chunk.choices = [choice]
            return chunk

        tokens = ["建议切换到 Implement 模式 (:mode implement) 来执行整理。"]
        stream_iter = iter([_make_chunk(t) for t in tokens])

        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = stream_iter
            MockOpenAI.return_value = mock_client

            _, suggest = chat.ask("帮我整理这些文件。")

        assert suggest is True

    def test_history_accumulates(self, sample_files, cfg):
        """多轮对话时 history 应正确积累。"""
        chat = ChatMode(sample_files, cfg)

        def _make_chunk(text):
            delta = MagicMock()
            delta.content = text
            choice = MagicMock()
            choice.delta = delta
            chunk = MagicMock()
            chunk.choices = [choice]
            return chunk

        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [
                iter([_make_chunk("回复一")]),
                iter([_make_chunk("回复二")]),
            ]
            MockOpenAI.return_value = mock_client

            chat.ask("问题一")
            chat.ask("问题二")

        # system + user1 + assistant1 + user2 + assistant2 = 5 条
        assert len(chat._history) == 5
