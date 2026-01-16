from langsmith import Client
import os
import json
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

def inspect_langsmith_traces():
    # 初始化 LangSmith 客户端
    # 它会自动读取环境变量 LANGCHAIN_API_KEY 和 LANGCHAIN_ENDPOINT
    client = Client()
    
    project_name = os.getenv("LANGCHAIN_PROJECT", "askc-backend-dev")
    print(f"Fetching traces for project: {project_name}...")

    try:
        # 获取最近的 5 个 Run
        runs = list(client.list_runs(
            project_name=project_name,
            limit=5,
            execution_order=1,  # 最新优先
            # filter='eq(run_type, "chain")' # 移除 filter 以查看所有类型的 Run
        ))

        if not runs:
            print("No runs found.")
            return

        for i, run in enumerate(runs):
            print(f"\n{'='*20} Run #{i+1} {'='*20}")
            print(f"Run ID: {run.id}")
            print(f"Name: {run.name}")
            print(f"Status: {run.status}")
            print(f"Start Time: {run.start_time}")
            
            print("\n--- Inputs ---")
            print(json.dumps(run.inputs, indent=2, ensure_ascii=False))
            
            print("\n--- Outputs ---")
            if run.outputs:
                print(json.dumps(run.outputs, indent=2, ensure_ascii=False))
            else:
                print("(No outputs or still running)")
            
            print(f"{'='*50}")

    except Exception as e:
        print(f"Error fetching traces: {e}")
        print("Please check your LANGCHAIN_API_KEY and LANGCHAIN_ENDPOINT in .env")

if __name__ == "__main__":
    inspect_langsmith_traces()
