#!/usr/bin/env python3
"""
智能文件夹管家 v3.0 ─ 双模式程序入口

模式说明:
    Chat 模式（默认）  — 自然语言对话，探索文件信息，不修改磁盘
    Implement 模式 — LLM 生成分类方案，dry-run 预览，确认执行，多层撤销

切换方式:
    Tab 键              — 在两种模式间快速切换
    :mode chat          — 切换到 Chat 模式
    :mode implement     — 切换到 Implement 模式
    :help               — 当前模式可用命令
    :exit               — 退出程序

用法:
    python main.py                              # 使用 config.yaml 默认配置
    python main.py --dirs folder1 folder2       # 指定扫描目录
    python main.py --config my_config.yaml      # 指定配置文件
    python main.py --no-extract                 # 跳过内容提取（加速）
    python main.py --mode implement             # 直接进入 Implement 模式
"""

from __future__ import annotations

import argparse
import sys

from file_agent.config import load_config
from file_agent.cli import App


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="🤖 智能文件夹管家 v3.0 ─ 双模式文件管理 Agent",
        epilog=(
            "模式说明:\n"
            "  chat       只读模式，自然语言探索文件\n"
            "  implement  执行模式，文件整理 + 多层撤销"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dirs",
        nargs="+",
        default=None,
        help="要扫描的目录列表（覆盖配置文件中的 scan_dirs）",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        default=False,
        help="跳过文件内容提取（加速启动）",
    )
    parser.add_argument(
        "--mode",
        choices=["chat", "implement"],
        default="chat",
        help="启动时进入的模式（默认: chat）",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 加载配置
    cfg = load_config(args.config)

    # CLI 参数覆盖
    if args.dirs:
        cfg.scan_dirs = args.dirs

    # 校验 API Key
    if not cfg.api_key:
        print("❌ 未设置 API Key。")
        print("   请在 config.yaml 中填写 api_key，")
        print("   或设置环境变量 FILE_AGENT_API_KEY，")
        print("   或在项目根目录放置 api_key.txt 文件。")
        sys.exit(1)

    # 如果指定了 --no-extract，设置 max_content_chars = 0 禁用提取
    if args.no_extract:
        cfg.max_content_chars = 0

    # 启动应用
    app = App(cfg)

    # 如果指定 --mode implement，在展示欢迎界面后自动切换
    if args.mode == "implement":
        # 延迟切换到 run() 内部处理：通过设置点标志位
        app._start_mode = "implement"
    else:
        app._start_mode = "chat"

    app.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 已退出。")
        sys.exit(0)
