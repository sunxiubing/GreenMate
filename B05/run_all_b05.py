"""B05 全量测试串行执行器

执行顺序：
1. test_functions.py      - 四个底层业务函数（ok/unavailable/error 三态）
2. test_connect_error.py  - 网络异常注入（断网/超时/HTTP 错误）
3. test_multi_tool.py     - 多工具任务验证（五步日志格式）
4. test_orchestrator.py   - 编排器 ask() 主流程
5. test_boundary.py       - 边界幻觉测试
6. verify_weather_source.py - 气象数据源验证
7. test_soilgrids.py      - SoilGrids MCP 五步 + Step 6 失败验证

只跑 test_soilgrids 最后一个时保留手动交互环节。
"""

import sys
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TESTS = [
    ("1. test_functions.py",       "test_functions.py"),
    ("2. test_connect_error.py",   "test_connect_error.py"),
    ("3. test_multi_tool.py",      "test_multi_tool.py"),
    ("4. test_orchestrator.py",    "test_orchestrator.py"),
    ("5. test_boundary.py",        "test_boundary.py"),
    ("6. verify_weather_source.py","verify_weather_source.py"),
    ("7. test_soilgrids.py",       "test_soilgrids.py"),
]

SEPARATOR = "=" * 70


def run_test(title: str, filename: str, keep_stdin: bool = False) -> int:
    """串行执行单个测试文件。keep_stdin=True 时保留终端输入（手动交互）。"""
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(f"{SEPARATOR}\n")

    cmd = [sys.executable, str(SCRIPT_DIR / filename)]
    kwargs = {
        "cwd": str(SCRIPT_DIR),
    }

    if keep_stdin:
        # 最后一个测试：保留 stdin，交互式运行
        kwargs["stdin"] = sys.stdin
        kwargs["stdout"] = sys.stdout
        kwargs["stderr"] = sys.stderr
        proc = subprocess.Popen(cmd, **kwargs)
        proc.wait()
        return proc.returncode
    else:
        # 前置测试：自动运行，stdout/stderr 实时输出到同一终端
        result = subprocess.run(cmd, **kwargs)
        return result.returncode


def main():
    print(f"\n{SEPARATOR}")
    print("  B05 全量测试执行")
    print(f"{SEPARATOR}")

    failed = []
    for title, filename in TESTS:
        is_last = (filename == TESTS[-1][1])
        rc = run_test(title, filename, keep_stdin=is_last)
        if rc != 0 and not is_last:
            failed.append((title, rc))
            print(f"\n  ⚠️  {title} 返回非零退出码 {rc}，继续执行...")

    if failed:
        print(f"\n{SEPARATOR}")
        print("  以下测试失败：")
        for t, rc in failed:
            print(f"    ❌ {t} (exit {rc})")
        print(f"{SEPARATOR}")
    elif TESTS[-1][1] == "test_soilgrids.py":
        print(f"\n{SEPARATOR}")
        print("  全部自动测试执行完毕")
        print("  手动交互环节已转交 test_soilgrids Step 6 第二段")
        print(f"{SEPARATOR}")


if __name__ == "__main__":
    main()
