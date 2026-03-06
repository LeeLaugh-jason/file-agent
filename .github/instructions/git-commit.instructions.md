---
# Git 版本管理与提交说明

## 提交信息规范
- 请始终按照 Conventional Commits 格式生成提交内容：
  - feat: 新功能
  - fix: 错误修复
  - docs: 文档
  - style: 格式/风格调整
  - refactor: 重构
  - test: 测试相关改动
  - chore: 维护/工具改动

## 工作流要求
1. **先显示变更摘要（变更文件 + 修改说明）**
2. 请结合实际 diff 生成简明易懂的提交信息
3. 若涉及多个功能变更，请拆分多个提交

## 示例规范
feat: add file classification rules for move command
fix: correct file path resolution in FileAgent
docs: update README and usage examples

## 与代码修改联动
- 在提取提交信息前，请显示关联的 Python 修改描述；
- 若有测试失败或变更风险，请将风险提示写进提交信息结尾；