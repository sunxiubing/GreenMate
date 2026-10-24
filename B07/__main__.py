"""B07 包入口

  python -m B07                              → 等价于 python -m B07.s1（S1 全流程）
  python -m B07.s1 --question "..."          → S1 全流程（推荐入口）
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


def main():
    argv = sys.argv[1:]

    # 透传子命令给 B07.runs
    if argv and argv[0] == "runs":
        sys.argv = ["B07.runs"] + argv[1:]
        from B07.runs import main as runs_main
        runs_main()
        return

    if argv and argv[0] in _RUNS_SUBCOMMANDS:
        sys.argv = ["B07.runs"] + argv
        from B07.runs import main as runs_main
        runs_main()
        return

    # 默认：S1 全流程
    from B07.s1 import main as s1_main
    s1_main()


if __name__ == "__main__":
    main()
