"""
tests/test_bug_fixes.py — 针对三个已修复 bug 的回归测试

覆盖问题：
  1. Context 超长未捕获 — BadRequestError 中文"超长"/"413"/"context length" 检测
  2. Tab 键覆盖提示符 — _build_prompt_session 中 Tab 绑定须使用 run_in_terminal
  3. 流式输出 — ChatMode.ask() 须用 stream=True；cli._stream_print 须逐字符写入 stdout
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from file_agent.config import AgentConfig
from file_agent.scanner import FileInfo


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def _make_file(rel_path: str, ext: str = ".txt", size: int = 1024) -> FileInfo:
    return FileInfo(
        path=Path("/tmp") / rel_path,
        rel_path=rel_path,
        root_dir="/tmp",
        ext=ext,
        size_bytes=size,
        modified_at=datetime(2025, 1, 1),
        content_summary="",
    )


def _make_cfg() -> AgentConfig:
    cfg = AgentConfig()
    cfg.api_key = "test_key"
    cfg.api_base = "https://example.com/v1"
    cfg.model = "glm-4"
    return cfg


def _make_overflow_error(message: str):
    """构造 openai.BadRequestError，模拟 API 返回超长错误。"""
    from openai import BadRequestError
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.headers = {}
    mock_resp.request = MagicMock()
    return BadRequestError(
        message=message,
        response=mock_resp,
        body={"error": {"message": message}},
    )


def _make_no_tool_response(content: str = "已完成") -> MagicMock:
    """构造没有 tool_calls 的 LLM 响应（让工具调用循环直接退出）。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_stream(tokens: list) -> iter:
    """构造 stream=True 时的 chunk 迭代器。"""
    chunks = []
    for t in tokens:
        delta = MagicMock()
        delta.content = t
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        chunks.append(chunk)
    return iter(chunks)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Context 超长自动裁剪重试
# ══════════════════════════════════════════════════════════════════════════════

class TestContextOverflowRetry:
    """ask_llm_for_plan 遇到超长错误时应自动裁剪 history 并重试。"""

    @pytest.fixture
    def sample_files(self):
        return [_make_file("a.txt"), _make_file("b.py")]

    @pytest.fixture
    def cfg(self):
        return _make_cfg()

    def _run_expecting_retry(self, sample_files, cfg, error_message: str):
        """发起一次会触发超长错误的调用，返回 mock create 对象。"""
        from file_agent.classifier import ask_llm_for_plan
        err = _make_overflow_error(error_message)
        ok_resp = _make_no_tool_response()
        with patch("file_agent.classifier.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [err, ok_resp]
            MockOpenAI.return_value = mock_client
            ask_llm_for_plan(
                files=sample_files,
                current_plan={f.rel_path: "未分类" for f in sample_files},
                user_instruction="整理一下",
                cfg=cfg,
            )
            return mock_client.chat.completions.create

    def test_retries_on_chinese_chaochang(self, sample_files, cfg):
        """【回归】中文 'Prompt 超长' 必须触发重试——修复前此处直接崩溃。"""
        mock_create = self._run_expecting_retry(
            sample_files, cfg,
            "Error code: 400 - {'error': {'message': 'Prompt 超长'}}"
        )
        assert mock_create.call_count == 2, "遇到中文'超长'时应重试一次，共调用 2 次"

    def test_retries_on_413_code(self, sample_files, cfg):
        """错误信息含 '413' 时触发重试。"""
        mock_create = self._run_expecting_retry(
            sample_files, cfg,
            "Error code: 400 - code: 413 input tokens exceeds limit"
        )
        assert mock_create.call_count == 2

    def test_retries_on_context_length_english(self, sample_files, cfg):
        """错误信息含 'context length' 时触发重试。"""
        mock_create = self._run_expecting_retry(
            sample_files, cfg,
            "Request tokens exceeds the model's maximum context length"
        )
        assert mock_create.call_count == 2

    def test_retries_on_exceeds_keyword(self, sample_files, cfg):
        """错误信息含 'exceeds' 时触发重试。"""
        mock_create = self._run_expecting_retry(
            sample_files, cfg,
            "input 999999 tokens exceeds context window"
        )
        assert mock_create.call_count == 2

    def test_retry_messages_trimmed_to_system_and_last_user(self, sample_files, cfg):
        """重试发送的 messages 只保留 system + 最后一条 user，不含 assistant/tool 历史。"""
        from file_agent.classifier import ask_llm_for_plan
        err = _make_overflow_error("Prompt 超长")
        ok_resp = _make_no_tool_response()
        with patch("file_agent.classifier.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [err, ok_resp]
            MockOpenAI.return_value = mock_client
            ask_llm_for_plan(
                files=sample_files, current_plan={},
                user_instruction="整理一下", cfg=cfg, history=None,
            )
            second_call = mock_client.chat.completions.create.call_args_list[1]
            msgs = second_call[1].get("messages") or second_call[0][0]

        roles = [m["role"] for m in msgs]
        assert all(r in ("system", "user") for r in roles), (
            f"重试消息中含有非 system/user 角色: {roles}"
        )
        assert len(msgs) <= 2, f"重试消息过多（{len(msgs)} 条），应只保留 system + 当前 user"

    def test_non_overflow_error_propagates(self, sample_files, cfg):
        """非超长的 BadRequestError 应直接抛出，不触发重试。"""
        from openai import BadRequestError
        from file_agent.classifier import ask_llm_for_plan
        err = _make_overflow_error("invalid_api_key")
        with patch("file_agent.classifier.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = err
            MockOpenAI.return_value = mock_client
            with pytest.raises(BadRequestError):
                ask_llm_for_plan(
                    files=sample_files, current_plan={},
                    user_instruction="整理", cfg=cfg,
                )
            assert mock_client.chat.completions.create.call_count == 1

    def test_no_error_calls_api_once(self, sample_files, cfg):
        """无错误时 API 只调用一次——正常路径不受影响。"""
        from file_agent.classifier import ask_llm_for_plan
        with patch("file_agent.classifier.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_no_tool_response()
            MockOpenAI.return_value = mock_client
            ask_llm_for_plan(
                files=sample_files, current_plan={},
                user_instruction="整理", cfg=cfg,
            )
        assert mock_client.chat.completions.create.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. Tab 键绑定使用 run_in_terminal（修复"你"被覆盖）
# ══════════════════════════════════════════════════════════════════════════════

def _get_tab_handler(app):
    """从 _build_key_bindings() 提取 Tab handler。

    prompt_toolkit 内部将 'tab' 解析为 Keys.ControlI 或 'c-i'，
    其 str 表示干版本不同而异。不进行 key 字符串匹配，
    直接取注册的最后一个 handler（即 Tab 绑定）。
    """
    kb = app._build_key_bindings()
    if not kb.bindings:
        return None
    return kb.bindings[-1].handler


class TestTabKeyBinding:
    """Tab 键触发时须通过 run_in_terminal 包装 _switch_mode，否则"你"被覆盖。"""

    @pytest.fixture
    def app(self):
        from file_agent.cli import App
        cfg = _make_cfg()
        cfg.scan_dirs = ["."]
        a = App(cfg)
        a._chat = MagicMock()
        a._impl = MagicMock()
        return a

    def test_tab_binding_exists(self, app):
        """_build_key_bindings() 必须至少有一个绑定（即 Tab 绑定）。"""
        kb = app._build_key_bindings()
        assert len(kb.bindings) > 0, "KeyBindings 中没有任何绑定，请检查 _build_key_bindings"

    def test_tab_calls_run_in_terminal(self, app):
        """【回归】Tab 触发时必须调用 event.app.run_in_terminal。

        直接调用 _switch_mode 会在 prompt_toolkit 持有终端时写入 rich 输出，
        导致提示符中的"你"字被覆盖清空。
        """
        handler = _get_tab_handler(app)
        if handler is None:
            pytest.skip("Tab 绑定未找到")
        mock_event = MagicMock()
        handler(mock_event)
        mock_event.app.run_in_terminal.assert_called_once()

    def test_tab_resets_buffer(self, app):
        """Tab 触发后输入缓冲区须被清空，避免 Tab 字符残留在输入框。"""
        handler = _get_tab_handler(app)
        if handler is None:
            pytest.skip("Tab 绑定未找到")
        mock_event = MagicMock()
        handler(mock_event)
        mock_event.app.current_buffer.reset.assert_called_once()

    def test_tab_does_not_directly_call_switch_mode(self, app):
        """【回归】handler 执行期间不应直接调用 _switch_mode（须包裹在 run_in_terminal 中）。"""
        with patch.object(app, "_switch_mode") as mock_switch:
            handler = _get_tab_handler(app)
            if handler is None:
                pytest.skip("Tab 绑定未找到")
            mock_event = MagicMock()
            mock_event.app.run_in_terminal = MagicMock()  # 阻断回调立即执行
            handler(mock_event)
            # _switch_mode 不应在 handler 同步执行期间被直接调用
            mock_switch.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 3a. _stream_print 打字机输出
# ══════════════════════════════════════════════════════════════════════════════

class TestStreamPrint:
    """_stream_print 须将所有字符写入 stdout 并携带前缀。"""

    @pytest.fixture(autouse=True)
    def load(self):
        from file_agent.cli import _stream_print
        self.fn = _stream_print

    def test_output_contains_full_text(self, capsys):
        self.fn("Hello World", prefix="")
        assert "Hello World" in capsys.readouterr().out

    def test_output_has_prefix(self, capsys):
        self.fn("你好", prefix="[BOT] ")
        out = capsys.readouterr().out
        assert out.startswith("[BOT] ")
        assert "你好" in out

    def test_no_chars_missing(self, capsys):
        """任何字符都不允许被截断。"""
        text = "ABCDE12345"
        self.fn(text, prefix="")
        out = capsys.readouterr().out
        for ch in text:
            assert ch in out

    def test_default_prefix_contains_robot_emoji(self, capsys):
        self.fn("测试")
        assert "🤖" in capsys.readouterr().out

    def test_empty_string_only_prefix_and_newline(self, capsys):
        self.fn("", prefix=">> ")
        assert capsys.readouterr().out.startswith(">> ")


# ══════════════════════════════════════════════════════════════════════════════
# 3b. ChatMode.ask() 流式调用
# ══════════════════════════════════════════════════════════════════════════════

class TestChatModeStreaming:
    """ChatMode.ask() 须用 stream=True，chunk 须实时写入 stdout。"""

    @pytest.fixture
    def chat(self):
        from file_agent.modes.chat_mode import ChatMode
        return ChatMode([_make_file("readme.md")], _make_cfg())

    def test_ask_passes_stream_true(self, chat, capsys):
        """【回归】ask() 必须携带 stream=True，否则回复全量返回无流式效果。"""
        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_stream(["Hi"])
            MockOpenAI.return_value = mock_client
            chat.ask("你好")
            kwargs = mock_client.chat.completions.create.call_args[1]
        assert kwargs.get("stream") is True, "ask() 缺少 stream=True 参数"

    def test_ask_concatenates_all_chunks(self, chat, capsys):
        """所有 chunk 须被拼接为完整回复字符串。"""
        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_stream(
                ["你", "好", "，", "世", "界"]
            )
            MockOpenAI.return_value = mock_client
            reply, _ = chat.ask("hi")
        assert reply == "你好，世界"

    def test_ask_streams_to_stdout(self, chat, capsys):
        """chunk 须实时写入 stdout，不能等全部收集完再打印。"""
        tokens = ["流", "式", "输", "出"]
        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_stream(tokens)
            MockOpenAI.return_value = mock_client
            chat.ask("测试")
        out = capsys.readouterr().out
        for ch in tokens:
            assert ch in out, f"字符 '{ch}' 未出现在 stdout 中"

    def test_ask_saves_full_reply_to_history(self, chat):
        """流式完成后完整回复须存入 _history 供多轮对话使用。"""
        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_stream(
                ["完", "整", "答", "复"]
            )
            MockOpenAI.return_value = mock_client
            chat.ask("问题")
        # history: [system, user, assistant]
        assert len(chat._history) == 3
        last = chat._history[-1]
        assert last["role"] == "assistant"
        assert last["content"] == "完整答复"

    def test_suggest_implement_detection_works_with_streaming(self, chat, capsys):
        """流式模式下切换模式检测功能不应受影响。"""
        with patch("file_agent.modes.chat_mode.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_stream(
                ["建议切换到", " Implement 模式", " (:mode implement)"]
            )
            MockOpenAI.return_value = mock_client
            _, suggest = chat.ask("帮我移动文件")
        assert suggest is True
