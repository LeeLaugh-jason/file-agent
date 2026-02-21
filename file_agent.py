import os
import json
from openai import OpenAI

# ==========================================
# 1. 配置你的大模型 API
# 这里以 DeepSeek 为例 (你需要去官网申请一个免费的 API Key 替换下面这段)
# 如果用通义千问，换成阿里的 base_url 和 Key 即可
# ==========================================
client = OpenAI(
    api_key="f0f0ac70bb5d40089d62379dafce2c44.faHeX44lvuNoBb2b", 
    base_url="https://open.bigmodel.cn/api/paas/v4/"
)

# 目标测试文件夹的路径 (建议先用相对路径)
TARGET_FOLDER = "./test_folder"

def get_files(folder_path):
    """读取指定文件夹下的所有文件（排除文件夹本身）"""
    if not os.path.exists(folder_path):
        print(f"⚠️ 找不到文件夹: {folder_path}，请先创建它！")
        return []
    
    file_list = []
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path):
            file_list.append(item)
    return file_list

def ask_llm_for_plan(file_list):
    """将文件列表发给大模型，让它输出整理方案"""
    
    # 提示词（Prompt）：告诉 AI 它该怎么做
    prompt = f"""
    你是一个专业的电脑文件夹整理助手。你的任务是根据文件名，判断它们应该归入哪个类别的文件夹。
    请将以下文件列表进行分类，并严格以 JSON 格式返回。
    键(Key)是原始文件名，值(Value)是目标文件夹的名字。
    
    例如输入: ["第1章.docx", "main.c"]
    返回: {{"第1章.docx": "文档", "main.c": "代码"}}
    
    待分类的文件列表如下：
    {file_list}
    """

    print("🧠 GLM-5 正在思考分类方案，请稍候...")
    response = client.chat.completions.create(
        model="glm-5",  # 🌟 这里将模型名称改为 glm-5
        messages=[
            {"role": "system", "content": "你是一个只输出 JSON 格式的机器助手。"},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"} # GLM-5 完美支持强制 JSON 输出
    )
    
    # 提取大模型的回复文本
    result_text = response.choices[0].message.content
    return json.loads(result_text)

def main():
    print("=== 🤖 文件夹整理助手 (MVP版) 启动 ===")
    
    # 1. 观察环境：获取文件列表
    files = get_files(TARGET_FOLDER)
    if not files:
        print("文件夹是空的，没啥可整理的。")
        return
    print(f"📂 发现 {len(files)} 个文件: {files}")
    
    # 2. 大脑决策：让大模型规划分类
    try:
        plan = ask_llm_for_plan(files)
        print("\n✨ 整理方案出炉！")
        
        # 3. 打印计划 (安全起见，这里先只打印，不真正移动文件)
        for filename, target_dir in plan.items():
            print(f"📄 [{filename}] -> 将被移动到目录 📁 [{target_dir}]")
            
        print("\n⚠️ 当前为【安全模式】，只打印方案，未真正移动文件。")
        
    except Exception as e:
        print(f"❌ 调用大模型出错啦: {e}")

if __name__ == "__main__":
    print("Enter main()")
    main()