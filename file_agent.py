import os
import json
import shutil  # 📦 新增：用于真实移动文件的标准库
from openai import OpenAI

# ==========================================
# 1. 智谱 GLM-5 API 配置
# ==========================================
client = OpenAI(
    api_key="f0f0ac70bb5d40089d62379dafce2c44.faHeX44lvuNoBb2b", # 请替换为你的真实 Key
    base_url="https://open.bigmodel.cn/api/paas/v4/" 
)

TARGET_FOLDER = "./test_folder"

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

def ask_llm_for_plan(file_list):
    """将包含相对路径的文件列表发给 GLM-5"""
    prompt = f"""
    你是一个专业的电脑文件夹整理助手。请将以下文件列表进行分类。
    注意：输入的文件名可能包含子目录路径（如 "子文件夹/测试.c"）。
    
    请严格以 JSON 格式返回。
    键(Key)是原始的相对路径，值(Value)是你为它规划的【目标根文件夹名称】。
    
    例如输入: ["第1章.docx", "src/main.c", "图片/图纸1.png"]
    返回: {{"第1章.docx": "文档与报告", "src/main.c": "C语言代码", "图片/图纸1.png": "机械图纸"}}
    
    待分类的文件列表如下：
    {file_list}
    """

    print("🧠 GLM-5 正在分析全局文件结构，请稍候...")
    response = client.chat.completions.create(
        model="glm-5",
        messages=[
            {"role": "system", "content": "你是一个只输出 JSON 格式的机器助手。"},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"} 
    )
    
    return json.loads(response.choices[0].message.content)

def main():
    print("=== 🤖 智能文件夹管家 v1.0 启动 ===")
    
    # 1. 获取所有层级的文件
    files = get_files_recursive(TARGET_FOLDER)
    if not files:
        print("文件夹是空的，没啥可整理的。")
        return
    print(f"📂 在主目录及子目录中共发现 {len(files)} 个文件。")
    
    try:
        # 2. 获取整理计划
        plan = ask_llm_for_plan(files)
        print("\n✨ 整理方案出炉！")
        
        for rel_filepath, target_dir_name in plan.items():
            print(f"📄 [{rel_filepath}] -> 📁 [{target_dir_name}]")
            
        # 3. ⚠️ 人类确认机制 (Human-in-the-loop)
        confirm = input("\n❓ 是否执行上述移动计划？(输入 Y 确认，其他任意键取消): ")
        
        if confirm.strip().upper() == 'Y':
            print("\n🚀 开始执行物理移动...")
            for rel_filepath, target_dir_name in plan.items():
                # 原始完整路径
                source_path = os.path.join(TARGET_FOLDER, rel_filepath)
                # 目标文件夹的完整路径
                dest_dir = os.path.join(TARGET_FOLDER, target_dir_name)
                
                # 如果目标文件夹不存在，Python 会自动帮你新建它！
                if not os.path.exists(dest_dir):
                    os.makedirs(dest_dir)
                
                # 提取纯文件名 (比如把 "src/main.c" 变成 "main.c")
                filename = os.path.basename(rel_filepath)
                # 最终要存放的位置
                dest_path = os.path.join(dest_dir, filename)
                
                try:
                    shutil.move(source_path, dest_path)
                    print(f"✅ 成功移动: {filename}")
                except Exception as e:
                    print(f"❌ 移动失败 [{rel_filepath}]: {e}")
            
            print("\n🎉 整理完成！快去文件夹里看看吧。")
        else:
            print("\n🛑 已取消移动，文件停留在原位，一切安全。")
            
    except Exception as e:
        print(f"❌ 运行出错: {e}")

if __name__ == "__main__":
    main()