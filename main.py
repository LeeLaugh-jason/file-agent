#!/usr/bin/env python3
"""
智能文件夹管家 v2.0 ─ 程序入口

用法:
    python main.py                              # 使用 config.yaml 默认配置
    python main.py --dirs folder1 folder2       # 指定扫描目录
    python main.py --dry-run                    # 强制 dry-run 模式
    python main.py --config my_config.yaml      # 指定配置文件
    python main.py --no-extract                 # 跳过内容提取（加速）
"""

from __future__ import annotations

import argparse
import sys

from config import load_config
from cli import App


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="🤖 智能文件夹管家 ─ 基于 LLM 的文件分类整理 Agent",
    )
    parser.add_argument(
        "--dirs",
        nargs="+",
        default=None,
        help="要扫描的目录列表（覆盖配置文件中的 scan_dirs）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="强制启用 dry-run 模式",
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 加载配置
    cfg = load_config(args.config)

    # CLI 参数覆盖
    if args.dirs:
        cfg.scan_dirs = args.dirs
    if args.dry_run:
        cfg.dry_run = True

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
    app.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 已退出。")
        sys.exit(0)
