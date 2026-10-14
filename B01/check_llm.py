"""Step 3 — 独立验证 LLM 配置：API Key / Base URL / 模型名称。

刻意不依赖 FastAPI 和页面：这样一旦 Step 4 的回答不对劲，可以先跑这个脚本，
确定问题出在"配置根本连不上"还是"提示词写得不好"，不用两边一起猜。

用法（在 B01 目录下）：
    venv/Scripts/python.exe check_llm.py
"""

import os
import sys
from pathlib import Path

# 从根目录起服务时，B01 模块找不到。加上路径。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from openai import OpenAI

# 输出重定向到文件或管道时，Windows 会退回 GBK 编码导致中文乱码，这里固定为 UTF-8。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

REQUIRED = ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"]


def mask(secret: str) -> str:
    """只露头尾，避免密钥被完整打印进终端或日志。"""
    if len(secret) <= 12:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]} (len={len(secret)})"


def main() -> int:
    missing = [key for key in REQUIRED if not os.getenv(key)]
    if missing:
        print(f"[FAIL] .env 缺少配置项: {', '.join(missing)}")
        return 1

    api_key = os.environ["LLM_API_KEY"]
    base_url = os.environ["LLM_BASE_URL"]
    model = os.environ["LLM_MODEL"]

    print("[配置]")
    print(f"  LLM_API_KEY  = {mask(api_key)}")
    print(f"  LLM_BASE_URL = {base_url}")
    print(f"  LLM_MODEL    = {model}")
    print()

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)

    prompt = "用一句话说明你是什么模型。"
    print(f"[请求] {prompt}")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"\n[FAIL] 调用失败：{type(exc).__name__}: {exc}")
        print("\n排查顺序：")
        print("  1. Base URL 是否正确、结尾是否带 /compatible-mode/v1")
        print("  2. 模型名在该工作空间是否已开通")
        print("  3. API Key 是否有效、是否过期")
        return 1

    print("\n[OK] 调用成功")
    print(f"  实际返回的模型: {resp.model}")
    print(f"  回答内容: {resp.choices[0].message.content.strip()}")

    usage = getattr(resp, "usage", None)
    if usage:
        print(f"  token 用量: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
