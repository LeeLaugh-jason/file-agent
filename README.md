# 🤖 智能文件夹管家 (File-Agent) v2.0

基于 LLM（GLM-5）的命令行文件分类整理 Agent。通过自然语言指令，自动为文件生成分类方案并执行移动。

> 适合用于课程资料、实验文件、杂乱文档的半自动整理场景。

---

## ✨ 功能特性

- **多目录递归扫描**：支持同时扫描多个目录，自动跳过 `.git` 等无关目录
- **文件内容提取**：支持 `.py`、`.docx`、`.pptx`、`.xlsx`、`.pdf` 等多种格式的内容摘要提取
- **MCP 工具集**：LLM 通过 Function Calling 获取文件列表、元信息、内容摘要
- **多轮对话优化**：持续修改分类方案直到满意
- **Dry-Run 预演**：执行前可预览移动结果
- **冲突重命名**：目标目录存在同名文件时自动追加后缀
- **一键回滚**：`/undo` 撤销上次移动
- **方案导入导出**：`/save` 和 `/load` 支持 JSON 格式
- **Rich 美化界面**：表格展示分类方案和执行结果
- **YAML 配置文件**：统一管理 API Key、扫描目录、忽略规则等

---

## 🏗️ 项目架构

```text
file-agent/
├── main.py          ← 程序入口（argparse CLI）
├── config.py        ← 配置管理（AgentConfig + YAML 读写）
├── scanner.py       ← 多目录递归扫描（FileInfo 数据类）
├── extractors.py    ← 文件内容提取（按扩展名分派）
├── classifier.py    ← LLM 分类引擎（MCP 工具 + Function Calling）
├── executor.py      ← 文件移动 / 冲突重命名 / 回滚
├── cli.py           ← Rich 美化交互界面
├── config.yaml      ← 配置文件（首次运行自动生成）
├── tests/           ← 单元测试
│   ├── test_config.py
│   ├── test_scanner.py
│   ├── test_extractors.py
│   └── test_executor.py
└── file_agent.py    ← 旧版单文件脚本（保留参考）
```

### 模块依赖关系

```text
main.py → cli.py → classifier.py → scanner.py → config.py
                  → executor.py  → scanner.py
                  → extractors.py → scanner.py
```

---

## 📦 环境要求

- Python 3.8+

### 安装依赖

```bash
pip install openai python-docx python-pptx pandas openpyxl rich pyyaml
```

可选依赖（PDF 解析）：

```bash
pip install pdfplumber
```

---

## ⚙️ 配置

首次运行会自动生成 `config.yaml` 模板。也可手动创建：

```yaml
# File-Agent 配置文件
api_key: "<YOUR_API_KEY_HERE>"
api_base: "https://open.bigmodel.cn/api/paas/v4/"
model: "glm-5"

scan_dirs:
  - "./test_folder"

ignore_dirs:
  - ".git"
  - "__pycache__"
  - "node_modules"

ignore_extensions: []

max_content_chars: 500
dry_run: false
```

**API Key 读取优先级**（从高到低）：
1. 环境变量 `FILE_AGENT_API_KEY`
2. `config.yaml` 中的 `api_key` 字段
3. 项目根目录下的 `api_key.txt` 文件（旧版兼容）

---

## 🚀 使用方式

### 基本用法

```bash
python main.py
```

### 命令行参数

```bash
python main.py --dirs folder1 folder2    # 指定多个扫描目录
python main.py --dry-run                 # 强制预演模式
python main.py --config my.yaml          # 指定配置文件
python main.py --no-extract              # 跳过内容提取（加速）
```

### 交互命令

| 命令 | 说明 |
|------|------|
| `/show` | 查看当前分类方案 |
| `/dryrun` | 预演移动（不修改文件） |
| `/run` | 执行移动 |
| `/undo` | 撤销上次移动 |
| `/save [file]` | 导出方案为 JSON（默认 plan.json） |
| `/load [file]` | 从 JSON 加载方案 |
| `/help` | 显示帮助 |
| `/exit` | 退出 |
| 其他文本 | 作为自然语言指令更新方案 |

---

## 🧪 运行测试

```bash
python -m pytest tests/ -v
```

---

## 安全与开源注意事项

- **不要在公开仓库提交真实密钥**。
- 建议将 `api_key.txt` 和 `config.yaml` 加入 `.gitignore`。

---

## 许可证

本项目采用 MIT License，详见 LICENSE 文件。
