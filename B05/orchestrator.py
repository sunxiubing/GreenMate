"""B05 Step 3 — 主调用流程：ReAct 风格的工具调用编排器

重构目标：
1. 用户仅传入自然语言任务，禁止硬编码工具调用顺序，LLM 全权判断
2. LLM 输出 tool call，后端解析并真实执行工具，获取真实返回结果
3. 更换输入任务，自动切换工具以及参数
4. 捕获工具调用失败，返回明确的缺失/错误信息

流程：
1. 用户输入自然语言问题
2. 将 tools.py 的工具定义传给大模型
3. 模型判断是否需要调用工具（tool_choice="auto"）
4. 若返回工具调用，验证参数 → 执行工具 → unavailable 重试一次
5. 将工具返回结果再次发给大模型，模型整理自然语言答案
6. 支持多轮连续工具调用（一次提问可调用多个工具）
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

import os
import json
import threading
from pathlib import Path

# Load B01/.env for LLM credentials
_env_path = Path(__file__).resolve().parent.parent / "B01/.env"
for line in _env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI
from B05.tools import TOOLS
from B05 import test_functions as _tf

client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ["LLM_BASE_URL"],
    timeout=60.0,
)

# --------------------------------------------------------------------------
# 工具执行器映射
# --------------------------------------------------------------------------
def _exec_get_current_environment(_inject: dict | None = None):
    if _inject:
        return {"status": "error", "error": _inject.get("error", "injected error"), "source": "B02", "data": None, "timestamp": "injected"}
    return _tf.get_current_environment()


def _exec_get_environment_history(hours: int, _inject: dict | None = None):
    if _inject:
        return {"status": "error", "error": _inject.get("error", "injected error"), "source": "B02", "data": None, "timestamp": "injected", "query_hours": hours, "count": 0}
    return _tf.get_environment_history(hours=int(hours))


def _exec_get_weather_forecast(location: str, _inject: dict | None = None):
    _en_to_zh = {
        "Beijing Daxing": "北京大兴",
        "Beijing Shunyi": "北京顺义",
        "Hebei Gu'an": "河北固安",
        "Shandong Shouguang": "山东寿光",
        "Shanghai Chongming": "上海崇明",
        "Zhejiang Jiashan": "浙江嘉善",
        "Guangdong Guangzhou": "广东广州",
        "Hainan Sanya": "海南三亚",
        "Jiangsu Nanjing": "江苏南京",
    }
    location_zh = _en_to_zh.get(location, location)
    return _tf.get_weather_forecast(location_zh, _inject=_inject)


def _exec_search_tomato_knowledge(query: str, top_k: int = 5, _inject: dict | None = None):
    if _inject:
        return {"status": "error", "error": _inject.get("error", "injected error"), "query": query, "Top-K": top_k, "citation": [], "retrieved_at": "injected"}
    return _tf.search_tomato_knowledge(query, top_k=int(top_k))


_TOOL_EXECUTORS = {
    "get_current_environment": _exec_get_current_environment,
    "get_environment_history": _exec_get_environment_history,
    "get_weather_forecast": _exec_get_weather_forecast,
    "search_tomato_knowledge": _exec_search_tomato_knowledge,
}

# 全局异常注入开关，用于测试 unavailable / error 场景
_INJECT: dict | None = None

# --------------------------------------------------------------------------
# 演示模式开关
# DEMO_MODE = False：真实 Open-Meteo 网络请求，超时自动返回 unavailable
# DEMO_MODE = True ：使用 mock 固定返回值，保证演示流畅稳定
# --------------------------------------------------------------------------
DEMO_MODE = False

# 演示模式的固定返回值（模拟 unavailable / error 场景）
_DEMO_RESPONSES = {
    "get_current_environment": {
        "status": "ok",
        "source": "B02/environment_samples",
        "data": {"temperature": 25.0, "humidity": 65.0, "lux": 12000, "source_type": "simulated", "sim_scenario": "normal"},
        "timestamp": "2026-10-18T12:00:00+00:00",
    },
    "get_environment_history": {
        "status": "ok",
        "source": "B02/environment_samples",
        "data": [
            {"timestamp": "2026-10-18T08:00:00+00:00", "temperature": 23.0, "humidity": 68.0, "lux": 8000, "source_type": "simulated", "sim_scenario": "normal"},
            {"timestamp": "2026-10-18T10:00:00+00:00", "temperature": 25.5, "humidity": 64.0, "lux": 14000, "source_type": "simulated", "sim_scenario": "normal"},
            {"timestamp": "2026-10-18T12:00:00+00:00", "temperature": 27.0, "humidity": 60.0, "lux": 18000, "source_type": "simulated", "sim_scenario": "normal"},
        ],
        "timestamp": "2026-10-18T12:00:00+00:00",
        "query_hours": 12,
        "count": 3,
    },
    "get_weather_forecast": {
        "status": "ok",
        "provider": "Open-Meteo",
        "retrieved_at": "2026-10-18T12:00:00+00:00",
        "data": {
            "location": "北京大兴",
            "forecast_window": "now",
            "timestamp": "2026-10-18T12:00:00",
            "temperature": 18.0,
            "humidity": 52.0,
            "precipitation_probability": 10.0,
            "cloud_cover": 20.0,
            "solar_radiation": 350.0,
            "weather_code": 2,
            "weather_description": "多云",
        },
        "error": None,
    },
    "search_tomato_knowledge": {
        "status": "ok",
        "query": "SIMULATED PUBLIC",
        "Top-K": 5,
        "citation": [
            {"chunk_id": "CH-001-01", "source_id": "SRC-001", "section": "SIMULATED vs PUBLIC 区别", "page": "./docs/b01_b03_design.md", "score": 0.85, "content": "SIMULATED 数据来源：B02 GreenhouseSimulator 本地模拟器。PUBLIC 数据来源：B03 Open-Meteo 天气 API。"},
            {"chunk_id": "CH-001-02", "source_id": "SRC-001", "section": "A/B 实验数据源选取规则", "page": "./docs/b01_b03_design.md", "score": 0.72, "content": "A/B 对照实验必须使用 SIMULATED 数据：可控、隔离、可复现。"},
            {"chunk_id": "CH-001-03", "source_id": "SRC-001", "section": "PUBLIC 不用于 A/B 实验的原因", "page": "./docs/b01_b03_design.md", "score": 0.65, "content": "PUBLIC 数据实时变化，HTTP 400/429 时返回 None，无法保证 A/B 两边有效状态。"},
        ],
        "retrieved_at": "2026-10-18T12:00:00+00:00",
    },
}


def set_inject(inject: dict | None):
    """设置全局异常注入，传入 {"tool": "get_weather_forecast", "status": "unavailable", "error": "..."}"""
    global _INJECT
    _INJECT = inject


# --------------------------------------------------------------------------
# Gap 1: Schema 参数验证
# --------------------------------------------------------------------------
def validate_tool_args(tool_name: str, args: dict) -> dict:
    """验证 LLM 生成的参数是否符合 tools.py 中的 schema。

    返回 {"valid": True} 或 {"valid": False, "error": "具体错误信息"}
    """
    tool_def = next((t for t in TOOLS if t["function"]["name"] == tool_name), None)
    if tool_def is None:
        return {"valid": False, "error": f"Unknown tool: {tool_name}"}

    params = tool_def["function"].get("parameters", {})
    properties = params.get("properties", {})
    required = params.get("required", [])

    # 检查必填参数
    for field in required:
        if field not in args or args[field] is None:
            return {"valid": False, "error": f"Missing required argument: {field}"}

    # 检查参数类型和枚举
    for key, value in args.items():
        if key not in properties:
            continue  # 未知参数跳过，不阻断
        prop = properties[key]
        expected_type = prop.get("type", "string")

        # 类型检查
        if expected_type == "number" and not isinstance(value, (int, float)):
            return {"valid": False, "error": f"Argument '{key}' must be number, got {type(value).__name__}"}
        if expected_type == "string" and not isinstance(value, str):
            return {"valid": False, "error": f"Argument '{key}' must be string, got {type(value).__name__}"}
        if expected_type == "boolean" and not isinstance(value, bool):
            return {"valid": False, "error": f"Argument '{key}' must be boolean, got {type(value).__name__}"}
        if expected_type == "array" and not isinstance(value, list):
            return {"valid": False, "error": f"Argument '{key}' must be array, got {type(value).__name__}"}

        # 枚举检查（仅当有 enum 定义时）
        if "enum" in prop and value not in prop["enum"]:
            return {"valid": False, "error": f"Argument '{key}' must be one of {prop['enum']}, got '{value}'"}

    return {"valid": True}


# --------------------------------------------------------------------------
# Gap 2: 执行器超时包装器
# --------------------------------------------------------------------------
def _exec_with_timeout(fn, args: dict, timeout: float = 10.0) -> dict:
    """在线程中执行 fn(**args)，超时则返回错误 dict。"""
    result: dict = {"status": "error", "error": f"tool execution timeout after {timeout}s"}

    def target():
        nonlocal result
        try:
            result = fn(**args)
        except Exception as e:
            result = {"status": "error", "error": f"{type(e).__name__}: {e}"}

    t = threading.Thread(target=target)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        # 线程仍在运行（超时），result 已是 timeout 错误
        return result
    return result


# --------------------------------------------------------------------------
# Gap 3: 重构 _run_executor（验证 + 超时 + unavailable 重试）
# --------------------------------------------------------------------------
def _run_executor(tool_name: str, args: dict, max_retries: int = 1):
    """执行工具调用：参数验证 → 超时保护 → unavailable 重试"""
    # 1. Schema 验证
    validation = validate_tool_args(tool_name, args)
    if not validation["valid"]:
        return {"status": "error", "error": validation["error"]}

    # 2. 演示模式
    if DEMO_MODE:
        return _DEMO_RESPONSES.get(
            tool_name,
            {"status": "error", "error": f"No demo data for tool: {tool_name}"}
        )

    # 3. 异常注入
    inject = None
    if _INJECT and _INJECT.get("tool") == tool_name:
        inject = _INJECT

    # 4. 执行（超时保护）
    executor = _TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        return {"status": "error", "error": f"Unknown tool: {tool_name}"}

    result = _exec_with_timeout(executor, {**args, "_inject": inject})

    # 5. unavailable 自动重试一次
    if result.get("status") == "unavailable" and max_retries > 0:
        result = _exec_with_timeout(executor, {**args, "_inject": inject})

    return result


# --------------------------------------------------------------------------
# 核心编排器
# --------------------------------------------------------------------------
def ask(question: str, max_turns: int = 10) -> str:
    """将用户问题交给大模型，支持多轮工具调用，返回最终自然语言答案。

    特点：
    - LLM 全权决定调用哪个工具、传什么参数
    - 参数自动验证，错误时返回明确信息而非静默失败
    - 工具执行有 10s 超时保护
    - unavailable 自动重试一次
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a greenhouse assistant with access to 4 tools. "
                "When a user asks a question, decide whether to call any tools. "
                "If you call tools, wait for the results and then give a natural language answer. "
                "If the knowledge base has no relevant content (all scores=0), say so honestly. "
                "If weather API returns unavailable, tell the user to retry later. "
                "If a tool returns an error, report it clearly to the user. "
                "Answer in Chinese. Keep answers concise (under 200 characters)."
            ),
        },
        {"role": "user", "content": question},
    ]

    called_tools: list[str] = []  # 记录本轮已执行的工具

    for _turn in range(max_turns):
        resp = client.chat.completions.create(
            model=os.environ["LLM_MODEL"],
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        msg = resp.choices[0].message
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        # 没有工具调用，直接返回
        if not msg.tool_calls:
            return msg.content or "(模型未返回内容)"

        # 执行每个工具调用
        for call in msg.tool_calls:
            tool_name = call.function.name
            raw_args = call.function.arguments

            # 解析参数（Gap 1 fix: 解析失败不再静默掉错）
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError as e:
                args = {}
                result = {
                    "status": "error",
                    "error": f"Failed to parse tool arguments: {e}. Raw: {raw_args[:100]}",
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                called_tools.append(tool_name)
                continue  # 这个工具失败，继续下一个

            # 执行工具（验证 + 超时 + 重试）
            result = _run_executor(tool_name, args)
            called_tools.append(tool_name)

            # 将结果追加为工具返回消息
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False, indent=2),
            })

    # Gap 4: max_turns 耗尽时返回已执行工具摘要
    tools_summary = ", ".join(called_tools) if called_tools else "无"
    return f"(已达到最大调用轮次 {max_turns}，已执行工具: {tools_summary}，未能生成最终回答)"
