"""
分类与 LLM 交互模块 ─ MCP 工具注册 + Function Calling 驱动的分类引擎。

对外暴露:
    - MCPToolRegistry          工具注册中心
    - build_mcp_registry()     构建规划工具集
    - ask_llm_for_plan()       调用 LLM 生成/更新方案
    - normalize_plan()         确保方案覆盖全部文件
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from openai import OpenAI, BadRequestError

from .config import AgentConfig
from .scanner import FileInfo, file_list_paths, file_list_metadata

# 类型别名
FilePlan = Dict[str, str]  # {相对路径: 目标文件夹名}


# ==========================================
# MCP 工具注册中心
# ==========================================

class MCPToolRegistry:
    """轻量 MCP 风格工具注册中心（本地实现）。"""

    def __init__(self):
        self._tools: dict = {}

    def register(self, name: str, description: str, parameters: dict, handler):
        self._tools[name] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
            "handler": handler,
        }

    def openai_tools(self) -> list:
        return [item["schema"] for item in self._tools.values()]

    def execute(self, name: str, args: dict) -> dict:
        if name not in self._tools:
            return {"ok": False, "error": f"未注册工具: {name}"}
        try:
            return self._tools[name]["handler"](args)
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ==========================================
# 构建 MCP 工具集
# ==========================================

_EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}

_SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "assistant_reply": {
            "type": "string",
            "description": "给用户的简短中文说明（1~3句）",
        },
        "plan": {
            "type": "object",
            "description": "文件相对路径到目标根文件夹名的映射",
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["assistant_reply", "plan"],
}

_GET_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要获取内容摘要的文件相对路径",
        },
    },
    "required": ["file_path"],
}


def build_mcp_registry(
    files: List[FileInfo],
    current_plan: FilePlan,
) -> MCPToolRegistry:
    """构建供 LLM 调用的 MCP 工具集。

    闭包捕获 files 和 current_plan 以在工具回调中使用。
    """
    registry = MCPToolRegistry()

    path_list = file_list_paths(files)
    # metadata 中 content_summary 截断为 80 字，减少单次 token 消耗
    # （需要更详细内容时 LLM 可按需调用 mcp_get_file_content）
    metadata_list = [
        {
            "path": fi.rel_path,
            "ext": fi.ext if fi.ext else "无扩展名",
            "size_bytes": fi.size_bytes,
            "modified_at": fi.modified_at.strftime("%Y-%m-%d %H:%M:%S"),
            "content_summary": fi.content_summary[:80] if fi.content_summary else "",
        }
        for fi in files
    ]

    # 建立快速查找映射
    content_map = {fi.rel_path: fi.content_summary for fi in files}

    # ---- 工具回调 ----

    def mcp_get_files(_args):
        return {"ok": True, "files": path_list}

    def mcp_get_file_metadata(_args):
        return {"ok": True, "file_metadata": metadata_list}

    def mcp_get_current_plan(_args):
        return {"ok": True, "current_plan": current_plan}

    def mcp_get_file_content(args):
        fp = args.get("file_path", "")
        summary = content_map.get(fp, "")
        if not summary:
            return {"ok": True, "content": "(无法获取或无内容)"}
        return {"ok": True, "content": summary}

    def mcp_submit_plan(args):
        assistant_reply = args.get("assistant_reply", "我已根据你的要求更新整理计划。")
        plan = args.get("plan", {})
        if not isinstance(plan, dict):
            return {"ok": False, "error": "plan 必须是对象（dict）"}
        return {
            "ok": True,
            "assistant_reply": assistant_reply,
            "plan": plan,
            "is_final": True,
        }

    # ---- 注册 ----

    registry.register(
        name="mcp_get_files",
        description="获取全部文件相对路径列表",
        parameters=_EMPTY_SCHEMA,
        handler=mcp_get_files,
    )
    registry.register(
        name="mcp_get_file_metadata",
        description="获取每个文件的元信息（扩展名、大小、修改时间、内容摘要前200字）",
        parameters=_EMPTY_SCHEMA,
        handler=mcp_get_file_metadata,
    )
    registry.register(
        name="mcp_get_current_plan",
        description="获取当前整理计划",
        parameters=_EMPTY_SCHEMA,
        handler=mcp_get_current_plan,
    )
    registry.register(
        name="mcp_get_file_content",
        description="获取指定文件的内容摘要（完整版，最多500字）",
        parameters=_GET_CONTENT_SCHEMA,
        handler=mcp_get_file_content,
    )
    registry.register(
        name="mcp_submit_plan",
        description="提交最终整理计划；当你完成规划后调用此工具",
        parameters=_SUBMIT_SCHEMA,
        handler=mcp_submit_plan,
    )

    return registry


# ==========================================
# LLM 交互
# ==========================================

_SYSTEM_PROMPT = (
    "你是一个专业文件整理助手。\n"
    "你必须通过工具获取信息，并在完成规划后调用 mcp_submit_plan 提交最终方案。\n"
    "硬性要求：\n"
    "1. plan 尽量覆盖所有输入文件；不得虚构文件路径。\n"
    "2. plan 的 value 必须是目标根文件夹名称（不是完整路径）。\n"
    "3. 你可以先调用 mcp_get_file_metadata 获取元数据，\n"
    "   如果需要更详细内容，再调用 mcp_get_file_content 查看特定文件的内容摘要。\n"
    "4. 基于文件名、扩展名、大小、修改时间及内容摘要进行合理分类。\n"
)


def normalize_plan(
    files: List[FileInfo],
    proposed_plan: FilePlan,
    fallback_plan: Optional[FilePlan] = None,
) -> FilePlan:
    """确保计划覆盖全部文件；缺失项沿用旧计划或标记为未分类。"""
    fallback_plan = fallback_plan or {}
    normalized: FilePlan = {}

    for fi in files:
        rel = fi.rel_path
        target = proposed_plan.get(rel)
        if isinstance(target, str) and target.strip():
            normalized[rel] = target.strip()
        elif rel in fallback_plan:
            normalized[rel] = fallback_plan[rel]
        else:
            normalized[rel] = "未分类"

    return normalized


def _build_client(cfg: AgentConfig) -> OpenAI:
    """根据配置创建 OpenAI 兼容客户端。"""
    return OpenAI(api_key=cfg.api_key, base_url=cfg.api_base)


def ask_llm_for_plan(
    files: List[FileInfo],
    current_plan: FilePlan,
    user_instruction: str,
    cfg: AgentConfig,
    history: Optional[List[dict]] = None,
    max_rounds: int = 8,
) -> Tuple[FilePlan, str, List[dict]]:
    """使用 MCP + Function Calling 方式生成或更新整理计划。

    Parameters
    ----------
    files : list[FileInfo]
    current_plan : FilePlan
    user_instruction : str
        本轮用户指令。
    cfg : AgentConfig
    history : list[dict] | None
        上一轮对话消息历史（多轮保持上下文）。
    max_rounds : int
        最大工具调用循环轮数。

    Returns
    -------
    (new_plan, assistant_reply, messages)
        new_plan: 规范化后的分类方案
        assistant_reply: LLM 的文字回复
        messages: 完整消息历史（用于下一轮传入）
    """
    print("🧠 LLM 正在通过 MCP 工具链生成整理方案...")

    registry = build_mcp_registry(files, current_plan)
    client = _build_client(cfg)

    # 构建消息
    if history:
        messages = list(history)
        messages.append({"role": "user", "content": user_instruction})
    else:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"请根据用户要求输出整理计划。\n用户本轮要求：{user_instruction}",
            },
        ]

    final_payload = None

    def _call_api(msgs: list) -> object:
        """调用 LLM API，遇到 context 超长时自动裁剪 history 重试一次。"""
        try:
            return client.chat.completions.create(
                model=cfg.model,
                messages=msgs,
                tools=registry.openai_tools(),
                tool_choice="auto",
            )
        except BadRequestError as e:
            err_body = str(e)
            if "413" in err_body or "exceeds" in err_body.lower() or "context length" in err_body.lower():
                print(
                    "⚠️  上下文过长，自动裁剪对话历史后重试（仅保留 system + 当前用户指令）..."
                )
                system_msgs = [m for m in msgs if m.get("role") == "system"]
                user_msgs   = [m for m in msgs if m.get("role") == "user"]
                trimmed = system_msgs + ([user_msgs[-1]] if user_msgs else [])
                return client.chat.completions.create(
                    model=cfg.model,
                    messages=trimmed,
                    tools=registry.openai_tools(),
                    tool_choice="auto",
                )
            raise

    for _ in range(max_rounds):
        response = _call_api(messages)

        assistant_msg = response.choices[0].message
        tool_calls = assistant_msg.tool_calls or []

        # 构建 assistant 消息记录
        assistant_record: dict = {
            "role": "assistant",
            "content": assistant_msg.content or "",
        }
        if tool_calls:
            assistant_record["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]
        messages.append(assistant_record)

        if not tool_calls:
            break

        # 处理工具调用
        for call in tool_calls:
            tool_name = call.function.name
            try:
                tool_args = json.loads(call.function.arguments or "{}")
            except Exception:
                tool_args = {}

            result = registry.execute(tool_name, tool_args)

            if tool_name == "mcp_submit_plan" and result.get("ok"):
                final_payload = result

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

        if final_payload:
            break

    # 解析结果
    if final_payload:
        assistant_reply = final_payload.get("assistant_reply", "我已根据你的要求更新整理计划。")
        proposed_plan = final_payload.get("plan", {})
    else:
        assistant_reply = "模型未通过工具返回方案，保留当前计划。"
        proposed_plan = current_plan

    if not isinstance(proposed_plan, dict):
        proposed_plan = {}

    new_plan = normalize_plan(files, proposed_plan, fallback_plan=current_plan)
    return new_plan, assistant_reply, messages
