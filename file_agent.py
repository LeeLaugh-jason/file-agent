import os
import json
import shutil  # 📦 新增：用于真实移动文件的标准库
from datetime import datetime
from openai import OpenAI

# ==========================================
# 1. 智谱 GLM-5 API 配置
# ==========================================
API_KEY_FILE = "./api_key.txt"


def load_api_key(file_path):
    """从 txt 文件读取 API Key"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"找不到密钥文件: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        api_key = f.read().strip()

    if not api_key:
        raise ValueError(f"密钥文件为空: {file_path}")

    return api_key


client = OpenAI(
    api_key=load_api_key(API_KEY_FILE),
    base_url="https://open.bigmodel.cn/api/paas/v4/" 
)

TARGET_FOLDER = "./test_folder"


def show_plan(plan):
    """按可读格式打印当前整理方案"""
    print("\n✨ 当前整理方案：")
    for rel_filepath, target_dir_name in plan.items():
        print(f"📄 [{rel_filepath}] -> 📁 [{target_dir_name}]")


def normalize_plan(files, proposed_plan, fallback_plan=None):
    """确保计划覆盖全部文件；缺失项沿用旧计划或标记为未分类"""
    fallback_plan = fallback_plan or {}
    normalized = {}

    for rel_path in files:
        target_dir = proposed_plan.get(rel_path)
        if isinstance(target_dir, str) and target_dir.strip():
            normalized[rel_path] = target_dir.strip()
        elif rel_path in fallback_plan:
            normalized[rel_path] = fallback_plan[rel_path]
        else:
            normalized[rel_path] = "未分类"

    return normalized

def get_files_recursive(folder_path):
    """📂 核心升级：递归读取文件夹及所有子目录下的文件"""
    if not os.path.exists(folder_path):
        print(f"⚠️ 找不到文件夹: {folder_path}，请先创建它！")
        return []
    
    file_list = []
    # os.walk 会像剥洋葱一样，一层层遍历所有子文件夹
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            # 获取文件的完整路径
            full_path = os.path.join(root, file)
            # 计算出相对于目标文件夹的路径（比如：子文件夹/作业.docx）
            # 这样发给 LLM 会更清晰，防止不同子文件夹里有同名文件
            rel_path = os.path.relpath(full_path, folder_path)
            file_list.append(rel_path)
            
    return file_list


def get_file_metadata(folder_path, file_list):
    """为每个文件补充有用元信息，便于模型更准确分类"""
    metadata_list = []

    for rel_path in file_list:
        full_path = os.path.join(folder_path, rel_path)
        try:
            stat = os.stat(full_path)
            modified_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_bytes = stat.st_size
        except Exception:
            modified_at = "未知"
            size_bytes = -1

        _, ext = os.path.splitext(rel_path)
        metadata_list.append(
            {
                "path": rel_path,
                "ext": ext.lower() if ext else "无扩展名",
                "size_bytes": size_bytes,
                "modified_at": modified_at,
            }
        )

    return metadata_list


def ask_llm_for_plan(file_list, file_metadata, current_plan, user_instruction):
    """支持多轮对话：按用户追加要求不断优化整理计划"""
    prompt = f"""
你是一个专业的电脑文件夹整理助手。

我会给你：
1) 全量文件相对路径列表
2) 每个文件的元信息（扩展名、大小、修改时间）
2) 当前整理计划（相对路径 -> 目标根文件夹）
3) 用户本轮追加要求

请你根据用户要求调整计划，并严格返回 JSON 对象，格式如下：
{{
  "assistant_reply": "给用户的简短中文说明（1~3句）",
  "plan": {{"文件相对路径": "目标根文件夹", "...": "..."}}
}}

硬性要求：
- plan 必须尽量覆盖所有输入文件路径；不要虚构不存在的文件
- 每个 value 必须是目标根文件夹名称（不要写完整路径）
- 只输出 JSON，不要输出 Markdown

文件列表：
{file_list}

文件元信息：
{json.dumps(file_metadata, ensure_ascii=False)}

当前计划：
{json.dumps(current_plan, ensure_ascii=False)}

用户本轮要求：
{user_instruction}
"""

    print("🧠 GLM-5 正在根据你的新要求优化方案...")
    response = client.chat.completions.create(
        model="glm-5",
        messages=[
            {"role": "system", "content": "你是一个只输出 JSON 格式的机器助手。"},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}
    )

    data = json.loads(response.choices[0].message.content)
    assistant_reply = data.get("assistant_reply", "我已根据你的要求更新整理计划。")
    proposed_plan = data.get("plan", {})

    if not isinstance(proposed_plan, dict):
        proposed_plan = {}

    final_plan = normalize_plan(file_list, proposed_plan, fallback_plan=current_plan)
    return assistant_reply, final_plan


def execute_plan(plan):
    """按最终方案执行实际移动"""
    print("\n🚀 开始执行物理移动...")
    for rel_filepath, target_dir_name in plan.items():
        source_path = os.path.join(TARGET_FOLDER, rel_filepath)
        dest_dir = os.path.join(TARGET_FOLDER, target_dir_name)

        if not os.path.exists(source_path):
            print(f"⚠️ 源文件不存在，已跳过: {rel_filepath}")
            continue

        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        filename = os.path.basename(rel_filepath)
        dest_path = os.path.join(dest_dir, filename)

        try:
            shutil.move(source_path, dest_path)
            print(f"✅ 成功移动: {rel_filepath} -> {target_dir_name}/{filename}")
        except Exception as e:
            print(f"❌ 移动失败 [{rel_filepath}]: {e}")

    removed_count = remove_empty_dirs(TARGET_FOLDER)
    if removed_count > 0:
        print(f"\n🧹 已自动清理 {removed_count} 个空文件夹。")
    else:
        print("\n🧹 未发现可清理的空文件夹。")

    print("\n🎉 整理完成！快去文件夹里看看吧。")


def remove_empty_dirs(folder_path):
    """递归删除指定目录下的空文件夹（不删除根目录本身）"""
    if not os.path.exists(folder_path):
        return 0

    removed_count = 0
    for root, dirs, _ in os.walk(folder_path, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    removed_count += 1
                    print(f"🗑️ 已删除空文件夹: {os.path.relpath(dir_path, folder_path)}")
            except Exception as e:
                print(f"⚠️ 清理空文件夹失败 [{dir_path}]: {e}")

    return removed_count

def main():
    print("=== 🤖 智能文件夹管家 v1.0.2（多轮对话版）启动 ===")
    
    # 1. 获取所有层级的文件
    files = get_files_recursive(TARGET_FOLDER)
    if not files:
        print("文件夹是空的，没啥可整理的。")
        return
    file_metadata = get_file_metadata(TARGET_FOLDER, files)
    print(f"📂 在主目录及子目录中共发现 {len(files)} 个文件。")
    
    try:
        # 2. 初始整理计划
        plan = {file_path: "未分类" for file_path in files}
        first_instruction = input("\n请输入你希望的整理方式（例如：按课程名分类）：").strip()
        if not first_instruction:
            first_instruction = "请先给出一个合理的初始分类方案。"
        assistant_reply, plan = ask_llm_for_plan(files, file_metadata, plan, first_instruction)
        print(f"\n🤖 {assistant_reply}")
        show_plan(plan)

        # 3. 多轮对话优化
        print("\n💬 你可以继续输入新要求来优化方案。")
        print("   - 输入 /show 查看当前方案")
        print("   - 输入 /run  执行移动")
        print("   - 输入 /exit 取消退出")

        while True:
            user_text = input("\n你: ").strip()

            if not user_text:
                continue

            if user_text.lower() == "/show":
                show_plan(plan)
                continue

            if user_text.lower() == "/exit":
                print("\n🛑 已取消移动，文件停留在原位，一切安全。")
                break

            if user_text.lower() == "/run":
                execute_plan(plan)
                break

            assistant_reply, plan = ask_llm_for_plan(files, file_metadata, plan, user_text)
            print(f"\n🤖 {assistant_reply}")
            show_plan(plan)
            
    except Exception as e:
        print(f"❌ 运行出错: {e}")

if __name__ == "__main__":
    main()