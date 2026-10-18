"""B05 Step 3 — 测试样例：验证多轮工具调用编排器"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

from B05.orchestrator import ask

CASES = [
    # Case 1: 单工具调用 - 当前环境
    {
        "id": "Case 1",
        "question": "现在大棚里的温度、湿度、光照是多少？",
        "expected_tools": ["get_current_environment"],
    },
    # Case 2: 单工具调用 - 历史趋势
    {
        "id": "Case 2",
        "question": "过去24小时大棚的温度变化趋势怎么样？",
        "expected_tools": ["get_environment_history"],
    },
    # Case 3: 单工具调用 - 天气预报
    {
        "id": "Case 3",
        "question": "北京大兴今天的天气如何？",
        "expected_tools": ["get_weather_forecast"],
    },
    # Case 4: 单工具调用 - 知识库
    {
        "id": "Case 4",
        "question": "GreenMate 系统中 SIMULATED 和 PUBLIC 数据源有什么区别？",
        "expected_tools": ["search_tomato_knowledge"],
    },
    # Case 5: 多工具调用 - 环境+天气组合
    {
        "id": "Case 5",
        "question": "当前大棚内部环境和北京大兴外部天气分别是怎样的？",
        "expected_tools": ["get_current_environment", "get_weather_forecast"],
    },
    # Case 6: 知识库边界 - 不存在的内容
    {
        "id": "Case 6",
        "question": "GreenMate 大棚系统内置告警音频文件名称是什么？",
        "expected_tools": ["search_tomato_knowledge"],
    },
]


if __name__ == "__main__":
    for case in CASES:
        print("=" * 70)
        print(f"[{case['id']}] {case['question']}")
        print(f"预期工具: {', '.join(case['expected_tools'])}")
        print("-" * 70)
        try:
            answer = ask(case["question"])
            print(f"回答:\n{answer}")
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
        print()
