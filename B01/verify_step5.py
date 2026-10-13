"""Step 5 验收：主动制造错误，确认接口明确失败，不伪装成功。

为什么单独写一个脚本，而不是手动改 .env 试一次：

1. 不改 .env。故障靠【环境变量注入】制造——load_dotenv 默认不覆盖已存在的
   环境变量，所以先给子进程设好坏值，.env 里那行好 key 就压不进来。
   测试可以反复跑，永远不会把真实密钥改坏。

2. 起真实的 main:app 子进程，走真实的 HTTP 请求，不是直接调 llm.ask()。
   直接调函数只能证明"函数会抛异常"，证明不了"页面/接口会失败"——
   而 Step 5 的验收条件说的正是后者。

3. 判定标准是【明确失败】：HTTP 状态码非 200，且每个面板都带着可读的原因。
   如果哪天这里变成 200 + 一段编出来的回答，就是这个脚本该报警的时候。

用法：
    venv/Scripts/python.exe verify_step5.py
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# 输出含中文，重定向到管道时 Windows 会退回 GBK。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
PYTHON = BASE_DIR / "venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

QUESTION = "当前环境怎么样？"
STATES = [
    {"id": "A", "temperature": 24.0, "humidity": 65.0, "light": 12000},
    {"id": "B", "temperature": 36.0, "humidity": 35.0, "light": 22000},
]

BROKEN_KEY = "sk-this-key-is-deliberately-wrong-for-step5"
BROKEN_URL = "http://127.0.0.1:9/v1"  # 9 是 discard 端口，保证连不上


def free_port() -> int:
    """让系统挑一个没人用的端口，避免撞上正在跑的服务。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int, overrides: dict) -> tuple[subprocess.Popen, list[str]]:
    """在指定端口起一个真实的 uvicorn 子进程，返回 (进程, 日志行列表)。

    日志走管道并由后台线程持续排空，不落盘。两个坑都是踩过的：
      - PIPE 不排空会被写满，子进程随后卡死在写日志上；
      - 写临时文件则会在 Windows 上留下"进程已退出但文件仍被占用"的清理问题。
    """
    env = os.environ.copy()
    # 先清干净，确保"正常"那组真的从 .env 取值，而不是继承了我们注入的坏值
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        env.pop(k, None)
    env.update(overrides)

    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    lines: list[str] = []
    threading.Thread(
        target=lambda: [lines.append(l.rstrip("\n")) for l in proc.stdout],
        daemon=True,
    ).start()
    return proc, lines


def wait_ready(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/limits", timeout=1)
            return True
        except urllib.error.HTTPError:
            return True  # 有响应就算起来了，状态码不重要
        except Exception:
            time.sleep(0.3)
    return False


def post_compare(port: int) -> tuple[int, str]:
    """POST /api/compare，返回 (状态码, 响应体原文)。"""
    payload = json.dumps({"question": QUESTION, "states": STATES}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/compare",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"(请求本身失败) {type(e).__name__}: {e}"


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def show_config_log(lines: list[str]) -> None:
    """打印子进程的启动配置行——它就是"进程到底读到什么"的直接证据。"""
    for line in lines:
        if "[GreenMate]" in line:
            print("     " + line.replace("[GreenMate] ", ""))


def run_case(title: str, overrides: dict, expect_fail: bool) -> bool:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    port = free_port()
    proc, lines = start_server(port, overrides)
    try:
        if not wait_ready(port):
            print("  ✗ 服务没起来，日志尾部：")
            for line in lines[-20:]:
                print("     " + line)
            return False

        show_config_log(lines)

        t0 = time.time()
        status, body = post_compare(port)
        cost = time.time() - t0

        print(f"  → POST /api/compare  状态码 {status}   耗时 {cost:.1f}s")

        try:
            data = json.loads(body)
        except Exception:
            print(f"  响应体（非 JSON）：{body[:400]}")
            return False

        results = data.get("results") or []
        if results:
            for r in results:
                tag = f"State {r['state']['state_id']}"
                if "answer" in r:
                    print(f"     回答 {tag}: {r['answer'][:60]}…")
                else:
                    print(f"     失败 {tag}: {r.get('error')}")
        else:
            print(f"     失败原因：{data.get('detail')}")

        if expect_fail:
            # 三条必须同时成立，缺一条都算"伪装成功"：
            #   1. HTTP 状态码本身是失败，只检查 body 的调用方（curl -f、监控探针）才不会被骗
            #   2. 两侧都给出了原因，不是一句笼统的报错
            #   3. 没有任何一侧返回 answer —— 失败时绝不允许编出回答
            ok = status == 502
            ok &= len(results) == 2 and all("error" in r for r in results)
            ok &= not any("answer" in r for r in results)
            print(f"  {'✓ 明确失败，没有伪造回答' if ok else '✗ 期望失败但没失败 —— 这就是伪装成功'}")
            return ok

        ok = status == 200 and len(results) == 2 and all("answer" in r for r in results)
        print(f"  {'✓ 正常调用成功（前置条件满足）' if ok else '✗ 正常调用也失败了 —— 先修配置，不是 Step 5 的问题'}")
        return ok
    finally:
        stop(proc)


def main() -> int:
    print("Step 5 验收：主动制造错误 —— 期望「明确失败」，不允许伪装成功")

    cases = [
        ("① 前置条件：配置正常时，应当调用成功", {}, False),
        ("② 故障注入：API Key 写错", {"LLM_API_KEY": BROKEN_KEY}, True),
        ("③ 故障注入：Base URL 指向不通的地址", {"LLM_BASE_URL": BROKEN_URL}, True),
    ]

    passed = [run_case(*c) for c in cases]

    print(f"\n{'=' * 72}")
    for (title, _, _), ok in zip(cases, passed):
        print(f"  {'PASS' if ok else 'FAIL'}  {title[2:]}")
    print(f"{'=' * 72}")

    if all(passed):
        print("\nStep 5 通过：成功就是成功，失败就是明确失败。")
        return 0
    print("\nStep 5 未通过，看上面哪一条不符合预期。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
