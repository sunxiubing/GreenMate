"""B06 Step 6 CLI 入口 — python -m B06.inference infer/verify"""
from B06.inference.inference import cmd_infer, run_failure_verification

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="B06 Step 6: 实时窗口推理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    infer_p = sub.add_parser("infer", help="实时推理")
    infer_p.add_argument("--model", required=True, help="模型路径")

    verify_p = sub.add_parser("verify", help="失败验证（三类场景）")
    verify_p.add_argument("--model", required=True, help="模型路径")

    args = parser.parse_args()

    if args.cmd == "infer":
        cmd_infer(args.model)
    elif args.cmd == "verify":
        run_failure_verification(args.model)
