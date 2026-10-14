"""Step 4 — 把【当前大棚状态 + 用户问题】一起发给 LLM。

Step 3 已验证配置（API Key / Base URL / 模型名）可用，见 check_llm.py。
这里只负责两件事：把状态渲染进提示词、发起调用。
"""

import os
import sys
from pathlib import Path

# 从根目录起服务时，B01 模块找不到。加上路径。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# 提示词里明确要求"引用具体数值"，这是 Step 4 验收条件（回答随状态变化）的关键：
# 只把状态塞进上下文而不要求引用，模型很容易忽略它，给出和状态无关的通用回答。
SYSTEM_PROMPT = """你是 GreenMate 大棚监测系统的助手，根据下方【当前大棚状态】回答用户问题。

回答要求：
1. 必须引用下方给出的具体数值（温度、湿度、光照），不要泛泛而谈。
2. 指出哪一项或哪几项数据是影响你这次判断的关键因素。
3. 如果某项数值超出常见作物适宜范围，明确指出是哪一项、偏了多少，并给出处理建议。
4. 数据不足以判断时直接说明缺什么，不要编造。
5. 用中文回答，控制在 200 字以内。
6. 输出纯文本，不要使用 Markdown 标记（**加粗**、# 标题、``` 代码块等）。

{state_block}"""

_client: OpenAI | None = None


def mask(secret: str) -> str:
    """只露头尾，避免密钥被完整打印进终端或日志。"""
    if not secret:
        return "(未设置)"
    if len(secret) <= 12:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]} (len={len(secret)})"


def log_config() -> None:
    """启动时把生效的配置打到日志。

    存在的理由：.env 是进程启动时读进内存的，之后改文件对运行中的进程毫无影响。
    没有这行日志，"改了 key 但没重启"会表现为"改了个寂寞"——而现在
    对比日志里的 key 和 .env 里的 key，一眼就能看出是不是没重启。
    """
    # flush=True 是必须的：输出被重定向到管道/文件时会被缓冲，
    # 不 flush 就看不到这几行——而看不到恰恰是它要解决的问题。
    print(f"[GreenMate] LLM_MODEL    = {os.environ.get('LLM_MODEL', '(未设置)')}", flush=True)
    print(f"[GreenMate] LLM_BASE_URL = {os.environ.get('LLM_BASE_URL', '(未设置)')}", flush=True)
    print(f"[GreenMate] LLM_API_KEY  = {mask(os.environ.get('LLM_API_KEY', ''))}", flush=True)
    print("[GreenMate] 配置在启动时读取；改完 .env 必须重启服务才会生效", flush=True)


def describe_exception(exc: Exception) -> str:
    """把 SDK 的原始异常翻成人话。

    Step 5 要求「页面/日志明确失败」。直接把 401 的原始 JSON 甩给用户
    不算明确——认不出是哪类故障、该去改什么。这里只翻译常见几类，
    其余原样返回并保留类型名，不吞掉信息。
    """
    name = type(exc).__name__
    text = str(exc)

    if "AuthenticationError" in name or "401" in text or "invalid_api_key" in text:
        return "API Key 无效或已失效（401）—— 检查 .env 的 LLM_API_KEY，改完必须重启服务"
    if "APIConnectionError" in name or "Connection error" in text:
        return "连不上 LLM 服务 —— 网络不通，或 LLM_BASE_URL 填错"
    if "Timeout" in name or "timeout" in text.lower():
        return "调用 LLM 超时 —— 网络太慢或服务端无响应"
    if "NotFoundError" in name or "404" in text or "model_not_found" in text:
        return "模型不存在或未开通（404）—— 检查 .env 的 LLM_MODEL"
    if "RateLimit" in name or "429" in text:
        return "触发限流（429）—— 稍后重试"
    return f"{name}: {text}"


def get_client() -> OpenAI:
    """复用同一个客户端，A/B 对照要连发多次请求，避免每次重建连接。"""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["LLM_API_KEY"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=60.0,
        )
    return _client


def format_state(state: dict) -> str:
    """把状态字典渲染成提示词里的文本块。"""
    return (
        "【当前大棚状态】\n"
        f"数据来源：{state['source']}（模拟数据，非真实传感器读数）\n"
        f"状态：{state['label']}\n"
        f"temperature 温度：{state['temperature']} {state['units']['temperature']}\n"
        f"humidity 湿度：{state['humidity']} {state['units']['humidity']}\n"
        f"light 光照：{state['light']} {state['units']['light']}"
    )


def ask(question: str, state: dict) -> dict:
    """把当前状态和用户问题一起发给 LLM。

    返回值带上 context，页面可以直接展示"这次到底注入了什么"——
    证明回答变化确实来自状态，而不是模型自由发挥。
    """
    context = format_state(state)
    resp = get_client().chat.completions.create(
        model=os.environ["LLM_MODEL"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(state_block=context)},
            {"role": "user", "content": question},
        ],
    )
    return {
        "answer": resp.choices[0].message.content.strip(),
        "model": resp.model,
        "context": context,
    }
