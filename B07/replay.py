"""B07 视图四 · 场景回放器（文档 p.66–68）

★ 架构定位（本轮唯一的分界线）：

  · **回放器（本文件）** 是视图四里**唯一**允许发起新运行的地方。
    它复用 B07 现有的 S1–S5 入口，不另写一套分析流程。
  · **对比器 / 归因器**（`B07/views_diff.py`、视图四页面）
    **一律只读存档**，绝不重新计算。

回放 ≠ 复现。回放会真的调一次 LLM、真的落一个新 run：

  * 真实天气走真实世界时间 → 同一个场景两次回放的外部条件可能不同；
  * LLM 采样本身有波动 → 同样的输入也可能给出不同的措辞。

所以默认 **离线优先**：外部天气用 B07 已有的离线构造夹具
（`B07.s3.OFFLINE_CASES` / `B07.s4_offline`），
这样除 LLM 采样外其余输入是确定的；`--real` 才去调真实天气 API。

用法：

    python -m B07.replay S1                 # 真回放（调 LLM 1 次，落 1 个新 run）
    python -m B07.replay S3 --dry-run       # 只打印计划，不调 LLM、不落盘
    python -m B07.replay S3 --real          # 用真实天气 API
    python -m B07.replay S5 --degraded      # S5 降级变体

硬约束：
  · 不改 s1.py / s2.py / s3.py / s4.py / s5.py / runs.py / judgement.py 任何一行；
  · 不改 SYSTEM_PROMPT / 固定问题 / 任何判据阈值；
  · run.json 存档结构一个字不改（回放日志另存 artifacts/replay_log.jsonl）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import DEFAULT_LOCATION
from B07.explain import FIXED_QUESTION

#: 回放日志（**另存**，不动 run.json 的存档结构）
REPLAY_LOG_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "replay_log.jsonl"

#: 支持回放的场景 → 复用的既有入口
SCENARIOS = ("S1", "S2", "S3", "S3_without", "S4", "S5")

#: 离线构造天气时必须写进 warnings 的那句话（原文）
CONSTRUCTED_WARNING = (
    "本次回放使用离线构造的天气用例（is_constructed=true），不是实时天气；"
    "实时天气会随真实时间变化，回放结果不可跨时间比较。"
)

#: 真实天气时的对应说明
REAL_WARNING = (
    "本次回放使用实时天气 API（is_constructed=false）；"
    "实时天气会随真实时间变化，回放结果不可跨时间比较。"
)

#: 额度提示（每次真回放前打印，dry-run 也打印，因为它描述的是"计划"）
QUOTA_NOTICE = "⚠ 本次回放会调用 LLM 1 次并落盘 1 个新 run（run_XXX）。"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _base_run_for(scenario_label: str) -> str | None:
    """回放前，同场景最近的一个既有存档（用来当对比基线）。

    只读索引，不重算。找不到就返回 None（**不静默换锚点**）。
    """
    from B07.runs import list_runs

    same = [i["run_id"] for i in list_runs()
            if (i.get("scenario") or "") == scenario_label]
    return sorted(same)[-1] if same else None


def _append_log(scenario: str, run_id: str | None, offline: bool,
                base_run_id: str | None, notes: str) -> None:
    """往 artifacts/replay_log.jsonl 追加一行回放日志。

    ★ 这是**另存**的日志文件；run.json 的存档结构一个字都没动。
    """
    REPLAY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _now_iso(),
        "scenario": scenario,
        "run_id": run_id,
        "offline": bool(offline),
        "base_run_id": base_run_id,
        "notes": notes,
    }
    with open(REPLAY_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# 各场景的外部条件构造（只做"取数来源"这一步，分析流程一律复用原入口）
# --------------------------------------------------------------------------

def _s3_external_context(offline: bool, case: str) -> tuple[dict, bool]:
    """S3 的外部天气上下文。

    Returns:
        (context, is_constructed)

    offline=True → 用 B07.s3 的离线夹具（不联网、数值确定）；
    offline=False → 真实调 B05 → B03 Open-Meteo。
    """
    from datetime import datetime as _dt

    from B07.s3 import (ANCHOR, OFFLINE_CASES, WEATHER_LOCATION, WEATHER_WINDOW,
                        _install_fake_weather, _make_offline_weather)
    from B07.weather import build_external_context

    anchor = _dt.fromisoformat(ANCHOR)
    if offline:
        if case not in OFFLINE_CASES:
            raise KeyError(
                f"未知离线构造用例 {case!r}，"
                f"B07.s3.OFFLINE_CASES 现有：{sorted(OFFLINE_CASES)}")
        restore = _install_fake_weather(None,
                                        _make_offline_weather(OFFLINE_CASES[case]))
        try:
            ctx = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)
        finally:
            restore()
        return ctx, True

    ctx = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)
    return ctx, False


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def replay(scenario: str, *, offline: bool = True, dry_run: bool = False,
           degraded: bool = False, case: str = "deteriorating",
           location: str = DEFAULT_LOCATION,
           model_path: str | Path | None = None) -> dict:
    """回放一个场景。

    Args:
        scenario:  S1 / S2 / S3 / S3_without / S4 / S5
        offline:   True（默认）= 外部天气用离线构造夹具；
                   False = 真实天气 API。
        dry_run:   True = 只打印计划，不调 LLM、不落盘。
        degraded:  仅 S5：True 走降级变体（天气证据不可用）。
        case:      仅 S3：离线构造用例名（B07.s3.OFFLINE_CASES 的键）。
        location:  位置标识。
        model_path: B06 模型产物路径（None = 用默认）。

    Returns:
        {"run_id", "archive", "source", "warnings", ...}
        dry_run 时 run_id / archive 为 None。
    """
    scenario = (scenario or "").upper()
    if scenario not in SCENARIOS:
        raise ValueError(
            f"未知场景 {scenario!r}，可用：{' / '.join(SCENARIOS)}")

    warnings: list[str] = []
    notes: list[str] = []
    source = ""
    scenario_label = scenario

    # ---------------- dry-run：只打印计划 ----------------
    if dry_run:
        plan = _plan(scenario, offline, degraded, case)
        source = plan["source"]
        warnings.append("dry-run：本次只打印计划，**没有调用 LLM、没有落盘新 run**。")
        if plan.get("constructed"):
            warnings.append(CONSTRUCTED_WARNING)
        else:
            warnings.append(REAL_WARNING if not offline else
                            "本场景不接入外部天气，无离线构造天气用例。")
        return {
            "run_id": None,
            "archive": None,
            "source": source,
            "warnings": warnings,
            "dry_run": True,
            "plan": plan,
        }

    # ---------------- 真回放 ----------------
    if scenario == "S1":
        from B07.s1 import run_s1

        res = run_s1(FIXED_QUESTION, location, model_path, write_report=False)
        archive = res["archive"]
        source = "B07.s1.run_s1"
        scenario_label = "S1"
        warnings.append("S1 不接入外部天气，本次回放的输入只有内部历史与 B06 模型。")

    elif scenario == "S2":
        # ★ S2 的选窗参考带来自 S1（s2._latest_s1_run_with_fingerprint）。
        #   没有可用的 S1 就**显式报错**，绝不静默换锚点。
        from B07.s2 import _latest_s1_run_with_fingerprint, run_s2

        s1_run_id, _s1_arch = _latest_s1_run_with_fingerprint()
        if not s1_run_id:
            raise RuntimeError(
                "S2 回放失败：找不到带提示词指纹的 S1 存档。\n"
                "  S2 的选窗参考带依赖 S1（s2._latest_s1_run_with_fingerprint）。\n"
                "  请先跑一次 S1（python -m B07.s1），再回放 S2。\n"
                "  本回放器**不会**静默换一个锚点继续跑。")
        res = run_s2(FIXED_QUESTION, location, model_path, write_report=False)
        archive = res["archive"]
        source = "B07.s2.run_s2"
        scenario_label = "S2"
        warnings.append(f"S2 的选窗参考带取自 S1 存档 {s1_run_id}。")

    elif scenario in ("S3", "S3_without"):
        if scenario == "S3":
            ctx, constructed = _s3_external_context(offline, case)
            from B07.s3 import run_with_weather_only

            archive = run_with_weather_only(ctx, location=location,
                                            model_path=model_path)
            source = "B07.s3.run_with_weather_only"
            scenario_label = "S3_with"
            notes.append(f"离线构造用例 case={case}" if constructed else "实时天气")
        else:
            from B07.runs import run_analysis
            from B07.s3 import ANCHOR

            archive = run_analysis(ANCHOR, location, model_path,
                                   scenario="S3_without",
                                   external_conditions=None)
            source = "B07.runs.run_analysis(scenario='S3_without')"
            scenario_label = "S3_without"
            constructed = False
            warnings.append("S3_without 不接入外部天气，本次是「不带天气」那一侧。")
        if scenario == "S3":
            if constructed:
                warnings.append(CONSTRUCTED_WARNING)
            else:
                warnings.append(REAL_WARNING)

    elif scenario == "S4":
        from B07.s4 import run_s4

        archive = run_s4(verbose=False)
        source = "B07.s4.run_s4"
        scenario_label = "S4"
        constructed = True
        warnings.append(CONSTRUCTED_WARNING)
        warnings.append("S4 的外部条件用 B07.s4_offline 的离线构造夹具"
                        "（case=improving），不联网。")

    elif scenario == "S5":
        from B07.s5 import run_variant

        res = run_variant(degraded=degraded)
        archive = res["archive"]
        source = f"B07.s5.run_variant(degraded={degraded})"
        scenario_label = "S5"
        constructed = True
        warnings.append(CONSTRUCTED_WARNING)
        warnings.append("S5 的外部条件用 B07.s4_offline 的离线构造夹具，不联网。")
        warnings.append(
            "本次是**降级变体**（天气证据不可用）。"
            if degraded else "本次是**基线变体**（一切正常）。")

    else:                                          # pragma: no cover
        raise RuntimeError(f"未处理的场景：{scenario}")

    run_id = archive.get("run_id")
    base_run_id = _base_run_for(scenario_label)

    # 回放日志（另存，不动 run.json）
    _append_log(scenario, run_id, offline, base_run_id,
                "；".join(notes + warnings)[:800])

    return {
        "run_id": run_id,
        "archive": archive,
        "source": source,
        "warnings": warnings,
        "dry_run": False,
        "base_run_id": base_run_id,
        "scenario_label": scenario_label,
        "notes": notes,
    }


def _plan(scenario: str, offline: bool, degraded: bool, case: str) -> dict:
    """dry-run 的计划描述（不产生任何副作用）。"""
    src = {
        "S1": "B07.s1.run_s1",
        "S2": "B07.s2.run_s2",
        "S3": "B07.s3.run_with_weather_only",
        "S3_without": "B07.runs.run_analysis(scenario='S3_without')",
        "S4": "B07.s4.run_s4",
        "S5": f"B07.s5.run_variant(degraded={degraded})",
    }[scenario]
    constructed = scenario in ("S3", "S4", "S5")
    return {
        "scenario": scenario,
        "source": src,
        "offline": bool(offline),
        "constructed": constructed and offline,
        "case": case if (scenario == "S3" and offline) else None,
        "degraded": degraded if scenario == "S5" else None,
        "llm_calls": 1,
        "new_runs": 1,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B07 视图四 · 场景回放器（唯一允许发起新运行的地方）")
    ap.add_argument("scenario", help="S1 / S2 / S3 / S3_without / S4 / S5")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划：不调 LLM、不落盘")
    ap.add_argument("--real", action="store_true",
                    help="用真实天气 API（默认离线构造夹具）")
    ap.add_argument("--offline", action="store_true",
                    help="离线构造天气（默认）")
    ap.add_argument("--degraded", action="store_true",
                    help="仅 S5：跑降级变体（天气证据不可用）")
    ap.add_argument("--case", default="deteriorating",
                    help="仅 S3：离线构造用例名（B07.s3.OFFLINE_CASES 的键）")
    ap.add_argument("--location", default=DEFAULT_LOCATION)
    ap.add_argument("--model", default=None, help="B06 已存模型产物路径")
    args = ap.parse_args(argv)

    offline = not args.real          # 默认离线；--offline 显式再确认一次
    if args.offline:
        offline = True

    print("=" * 70)
    print("B07 视图四 · 场景回放")
    print("=" * 70)
    print(f"\n  场景    ：{args.scenario}")
    print(f"  天气来源：{'离线构造夹具（不联网）' if offline else '真实天气 API'}")
    if args.scenario == "S3" and offline:
        print(f"  构造用例：case={args.case}")
    if args.scenario == "S5":
        print(f"  变体    ：{'降级（天气证据不可用）' if args.degraded else '基线（一切正常）'}")
    print(f"\n  {QUOTA_NOTICE}")

    try:
        res = replay(args.scenario, offline=offline, dry_run=args.dry_run,
                     degraded=args.degraded, case=args.case,
                     location=args.location, model_path=args.model)
    except Exception as exc:                    # 失败显式暴露
        print(f"\n  ❌ 回放失败：{type(exc).__name__}: {exc}")
        return 1

    if res["dry_run"]:
        print("\n  ── 回放计划 ──")
        p = res["plan"]
        print(f"  将调用入口 ：{p['source']}")
        print(f"  LLM 调用   ：{p['llm_calls']} 次")
        print(f"  新增 run   ：{p['new_runs']} 个")
        if p.get("case"):
            print(f"  构造用例   ：case={p['case']}")
        if p.get("degraded") is not None:
            print(f"  降级开关   ：{p['degraded']}")
        print("\n  警告：")
        for w in res["warnings"]:
            print(f"    · {w}")
        print("\n  （dry-run 到此为止：没有调用 LLM，没有落盘新 run。）")
        return 0

    print(f"\n  ✅ 回放完成：{res['run_id']}")
    print(f"     入口    ：{res['source']}")
    print(f"     对比基线：{res['base_run_id'] or '(该场景暂无既有存档)'}")
    print("\n  警告：")
    for w in res["warnings"]:
        print(f"    · {w}")
    print(f"\n  回放日志：{REPLAY_LOG_PATH.relative_to(_PROJECT_ROOT)}")
    print(f"  对比    ：python -m B07.views compare "
          f"{res['base_run_id'] or '<基线>'} {res['run_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
