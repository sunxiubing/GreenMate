"""B05 Step 4 — 多工具任务验证（五步日志格式）

Step 1  tool_call  : 模型生成的调用名称与参数
Step 2  execute    : 底层实际执行动作
Step 3  result     : 工具返回原始数据
Step 4  next tool  : 多工具场景中下一个要调用的工具
Step 5  final      : 最终大模型回答，明确引用了工具返回数据
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

import json

# --------------------------------------------------------------------------
# 五步日志格式
# --------------------------------------------------------------------------
class StepLogger:
    def __init__(self, case_id: str, question: str):
        self.case_id = case_id
        self.question = question
        self.steps = []
        self.tool_calls = []   # [(tool_name, args, result)]

    def tool_call(self, tool_name: str, args: dict):
        args_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items()) if args else ""
        line = f"  【tool_call】 {tool_name}({args_str})"
        self.steps.append(("tool_call", line))
        print(line)

    def execute(self, tool_name: str, action: str):
        line = f"  【execute】   {action}"
        self.steps.append(("execute", line))
        print(line)

    def result(self, _tool_name: str, result: dict):
        # 裁剪 result 便于显示
        brief = {k: v for k, v in result.items() if k not in ("citation", "data")}
        if "data" in result and result["data"]:
            d = result["data"]
            if isinstance(d, dict):
                brief["data_summary"] = {k: v for k, v in d.items() if k not in ("citation",)}
                if "count" in d:
                    brief["data_summary"]["_count"] = d["count"]
        if "citation" in result:
            brief["citation_count"] = len(result["citation"])
        line = f"  【result】    {json.dumps(brief, ensure_ascii=False, indent=2)[:200]}"
        self.steps.append(("result", line))
        print(line)
        self.tool_calls.append((_tool_name, result))

    def next_tool(self, pending: list):
        if pending:
            names = " → ".join(p["function"]["name"] for p in pending)
            line = f"  【next tool】 {names}"
            self.steps.append(("next_tool", line))
            print(line)

    def final(self, answer: str):
        line = f"  【final】\n{answer}"
        self.steps.append(("final", line))
        print(f"\n  【final】")
        for ln in answer.split("\n"):
            print(f"    {ln}")


# --------------------------------------------------------------------------
# 补丁 orchestrator：注入五步日志
# --------------------------------------------------------------------------
import B05.orchestrator as _oc

_run_executor_original = _oc._run_executor

# 每个 case 开始时注入 logger
_active_logger = None


def _patched_run_executor(tool_name: str, args: dict):
    result = _run_executor_original(tool_name, args)

    if _active_logger is not None:
        _active_logger.execute(tool_name, _describe_action(tool_name, args))
        _active_logger.result(tool_name, result)

    return result


_oc._run_executor = _patched_run_executor


def _describe_action(tool_name: str, args: dict) -> str:
    """生成 human-readable 执行动作描述"""
    if tool_name == "get_current_environment":
        return "SQLite: SELECT * FROM environment_samples ORDER BY id DESC LIMIT 1"
    elif tool_name == "get_environment_history":
        hours = args.get("hours", 24)
        return f"SQLite: SELECT * FROM environment_samples WHERE timestamp >= now-{hours}h"
    elif tool_name == "get_weather_forecast":
        return f"HTTP GET https://api.open-meteo.com/v1/forecast?location={args.get('location')}"
    elif tool_name == "search_tomato_knowledge":
        q = args.get("query", "")
        k = args.get("top_k", 5)
        return f"TF-IDF retrieval: query='{q}', top_k={k}"


# Patch ask to capture tool calls
def _patched_ask(question: str, max_turns: int = 10) -> str:
    import os
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ["LLM_BASE_URL"],
        timeout=60.0,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a greenhouse assistant with access to 4 tools. "
                "When a user asks a question, decide whether to call any tools. "
                "If you call tools, wait for the results and then give a natural language answer. "
                "If the knowledge base has no relevant content (all scores=0), say so honestly. "
                "If weather API returns unavailable, tell the user to retry later. "
                "Answer in Chinese. Keep answers concise (under 200 characters)."
            ),
        },
        {"role": "user", "content": question},
    ]

    for _ in range(max_turns):
        resp = client.chat.completions.create(
            model=os.environ["LLM_MODEL"],
            messages=messages,
            tools=_oc.TOOLS,
            tool_choice="auto",
        )

        msg = resp.choices[0].message
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        if not msg.tool_calls:
            answer = msg.content or "(模型未返回内容)"
            if _active_logger is not None:
                _active_logger.final(answer)
            return answer

        # 记录 tool_call（和 next_tool）
        if _active_logger is not None:
            for i, call in enumerate(msg.tool_calls):
                args = json.loads(call.function.arguments) if isinstance(call.function.arguments, str) else call.function.arguments
                _active_logger.tool_call(call.function.name, args)
                if i < len(msg.tool_calls) - 1:
                    next_call = msg.tool_calls[i + 1]
                    next_dict = {"function": {"name": next_call.function.name}}
                    _active_logger.next_tool([next_dict])

        # 依次执行工具
        for call in msg.tool_calls:
            tool_name = call.function.name
            args = json.loads(call.function.arguments) if isinstance(call.function.arguments, str) else call.function.arguments

            executor = _oc._TOOL_EXECUTORS.get(tool_name)
            if executor is None:
                result = {"status": "error", "error": f"Unknown tool: {tool_name}"}
            else:
                result = _patched_run_executor(tool_name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False, indent=2),
            })

    return "(已达到最大调用轮次)"


_oc.ask = _patched_ask


# --------------------------------------------------------------------------
# 测试用例
# --------------------------------------------------------------------------
CASES = [
    {
        "id": "S1", "category": "单工具",
        "question": "现在大棚里的温度、湿度、光照是多少？",
        "expected_tools": ["get_current_environment"],
    },
    {
        "id": "S2", "category": "单工具",
        "question": "过去12小时大棚温度变化趋势如何？",
        "expected_tools": ["get_environment_history"],
    },
    {
        "id": "S3", "category": "单工具",
        "question": "北京大兴今天的天气怎么样？",
        "expected_tools": ["get_weather_forecast"],
    },
    {
        "id": "S4", "category": "单工具",
        "question": "GreenMate 系统中 SIMULATED 和 PUBLIC 数据源有什么区别？",
        "expected_tools": ["search_tomato_knowledge"],
    },
    {
        "id": "M1", "category": "多工具-环境+天气",
        "question": "当前大棚内部环境和北京大兴外部天气分别是怎样的？",
        "expected_tools": ["get_current_environment", "get_weather_forecast"],
    },
    {
        "id": "M2", "category": "多工具-历史+知识库",
        "question": "过去24小时大棚环境有什么变化？SIMULATED和PUBLIC数据源有什么区别？",
        "expected_tools": ["get_environment_history", "search_tomato_knowledge"],
    },
    {
        "id": "M3", "category": "多工具-全量数据",
        "question": "请同时提供：当前大棚状态、过去24小时趋势、北京大兴天气、知识库中关于干旱场景的参数说明。",
        "expected_tools": ["get_current_environment", "get_environment_history", "get_weather_forecast", "search_tomato_knowledge"],
    },
]


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------
print("=" * 70)
print("B05 Step 4 — 多工具任务验证（五步日志格式）")
print("=" * 70)

all_passed = True

for case in CASES:
    _active_logger = StepLogger(case["id"], case["question"])

    print(f"\n{'='*70}")
    print(f"[{case['id']}] {case['category']}")
    print(f"问题: {case['question']}")
    print(f"预期工具: {case['expected_tools']}")
    print("-" * 70)

    try:
        answer = _patched_ask(case["question"])

        # 验证
        actual_tools = [tc[0] for tc in _active_logger.tool_calls]
        expected_set = set(case["expected_tools"])
        actual_set = set(actual_tools)

        print(f"\n  【验证】")
        print(f"  预期工具: {sorted(expected_set)}")
        print(f"  实际工具: {sorted(actual_set)}")

        match = "✅ PASS" if actual_set == expected_set else "⚠️  PARTIAL"
        print(f"  结果: {match}")

        if actual_set != expected_set:
            missing = expected_set - actual_set
            extra = actual_set - expected_set
            if missing: print(f"  缺失: {missing}")
            if extra: print(f"  多余: {extra}")
            all_passed = False

    except Exception as e:
        print(f"\n  【FAIL】程序崩溃: {type(e).__name__}: {e}")
        all_passed = False

    _active_logger = None

print("\n" + "=" * 70)
print("全部测试执行完毕" + (" ✅" if all_passed else " ❌"))
print("=" * 70)
