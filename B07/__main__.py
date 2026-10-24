"""B07 包入口

  python -m B07                              → 等价于 python -m B07.s1（S1 全流程）
  python -m B07 --scenario S1                → S1 全流程（默认，行为不变）
  python -m B07 --scenario S2                → S2 全流程（渐进漂移）
  python -m B07.s1 --question "..."          → S1 全流程（推荐入口）
  python -m B07.s2 --question "..."          → S2 全流程（渐进漂移）
  python -m B07 list                         → 列出所有 run
  python -m B07 explain run_001              → 回溯某次运行
  python -m B07 runs list|explain|run        → 透传给 B07.runs
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_RUNS_SUBCOMMANDS = {"list", "explain", "run"}
_SCENARIOS = {"S1", "S2"}


def _split_scenario(argv: list) -> tuple[str, list]:
    """把 --scenario 从 argv 里取出来，返回 (场景, 剩余参数)。

    未知场景直接报错退出，不静默回退到 S1——
    悄悄跑成另一个场景，比报错难查得多。
    """
    scenario = "S1"
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--scenario":
            if i + 1 >= len(argv):
                print("  ❌ --scenario 需要一个场景值（S1 / S2）")
                sys.exit(2)
            scenario = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--scenario="):
            scenario = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1

    if scenario not in _SCENARIOS:
        print(f"  ❌ 未知场景 {scenario!r}，可选：{sorted(_SCENARIOS)}")
        sys.exit(2)

    return scenario, rest


def main():
    argv = sys.argv[1:]
    scenario, rest = _split_scenario(argv)

    # 透传子命令给 B07.runs
    if rest and rest[0] == "runs":
        sys.argv = ["B07.runs"] + rest[1:]
        from B07.runs import main as runs_main
        runs_main()
        return

    if rest and rest[0] in _RUNS_SUBCOMMANDS:
        sys.argv = ["B07.runs"] + rest
        from B07.runs import main as runs_main
        runs_main()
        return

    # 全流程：按场景分发
    sys.argv = ["B07.s1" if scenario == "S1" else "B07.s2"] + rest
    if scenario == "S1":
        from B07.s1 import main as s1_main
        s1_main()
    else:
        from B07.s2 import main as s2_main
        s2_main()


if __name__ == "__main__":
    main()
