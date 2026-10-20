"""B06 Step 5 CLI 入口 — python -m B06.persist save/load"""
from B06.persist.persist import cmd_save, cmd_load
import sys

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="B06 Step 5: 模型保存与重载")
    sub = parser.add_subparsers(dest="cmd", required=True)

    save_p = sub.add_parser("save", help="保存模型")
    save_p.add_argument("--model", required=True, help="输出路径，如 artifacts/b06-20261020-v1")

    load_p = sub.add_parser("load", help="加载模型并验证")
    load_p.add_argument("--model", required=True, help="模型路径，如 artifacts/b06-20261020-v1")

    args = parser.parse_args()

    if args.cmd == "save":
        cmd_save(args.model)
    elif args.cmd == "load":
        cmd_load(args.model)
