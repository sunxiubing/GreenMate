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

    # B08 第二遍新增：**固定场景回放**（条件不漂移）
    python -m B07.replay --scenario-file B07/scenarios/S3.json
    python -m B07.replay --scenario-file B07/scenarios/S3.json --dry-run

    ★ 固定场景回放与上面那套的区别：外部条件**不当场取**，而是注入
      场景文件里冻结的那份快照（不重新调天气 API、不重新构造夹具），
      所以同一个 scenario_id 反复回放时主要条件一致。

硬约束：
  · 不改 s1.py / s2.py / s3.py / s4.py / s5.py / runs.py / judgement.py 任何一行；
  · 不改 SYSTEM_PROMPT / 固定问题 / 任何判据阈值；
  · run.json 存档结构一个字不改（回放日志另存 artifacts/replay_log.jsonl）。
"""

from __future__ import annotations

import argparse
import json
import re
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


def _run_seq(run_id: str) -> int:
    """run_id 的数字后缀（run_120 → 120）。不是数字就返回 -1。"""
    m = re.search(r"(\d+)\s*$", run_id or "")
    return int(m.group(1)) if m else -1


def _base_run_for(scenario_label: str,
                  exclude_run_id: str | None = None) -> str | None:
    """回放前，同场景最近的一个既有存档（用来当对比基线）。

    只读索引，不重算。找不到就返回 None（**不静默换锚点**）。

    ★ B08 第二遍修了两个 bug（只改这一个函数，其它行为不动）：

      1. **排除刚生成的那个 run** —— 原实现是在 run_analysis 落盘**之后**
         才取基线，`sorted(same)[-1]` 会把刚生成的 run 自己选出来，
         于是 base_run_id 永远等于 run_id（基线失去意义）。
      2. **按数字后缀排序** —— 原来用的是字符串排序，"run_99" 会排在
         "run_120" 后面（'9' > '1'），取出来的不是"最近"的那一条。
    """
    from B07.runs import list_runs

    same = [i["run_id"] for i in list_runs()
            if (i.get("scenario") or "") == scenario_label
            and i["run_id"] != exclude_run_id]
    if not same:
        return None
    return max(same, key=lambda r: (_run_seq(r), r))


def _append_log(scenario: str, run_id: str | None, offline: bool,
                base_run_id: str | None, notes: str,
                extra: dict | None = None) -> None:
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
    if extra:
        row.update(extra)
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
# 固定场景回放（B08 第二遍新增）
# --------------------------------------------------------------------------
#
# ★ 与上面那套"按场景入口回放"的区别：
#
#   按场景入口回放（S1/S2/S3/S4/S5）：走各场景自己的入口，外部条件**当场取**
#     （离线夹具或真实 API），所以同一个场景两次回放的外部条件可能不同。
#
#   固定场景回放（--scenario-file）：输入**全部来自场景文件**
#     （analysis_time / location / history / 天气证据快照），
#     **不重新取数、不重新构造夹具**，所以条件不漂移 —— 这才是"固定场景"的含义。
#
# ★ 场景文件里的天气证据是**快照**：
#   · 快照里的 external_conditions 原样注入（逐字，不重算 signal）；
#   · 有证据台账的场景（S4/S5），台账按快照里的 B03 证据 ext 重建；
#   · 没有台账的场景（S3），证据身份（provider/retrieved_at/forecast_time）
#     落在注入的 external_conditions 里，否则身份会整个丢掉。

def _load_scenario_file(path: str | Path) -> dict:
    """读场景文件，并校验它没被手改过（scenario_sha256）。"""
    from B07.scenarios_build import scenario_digest

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到场景文件：{p}")
    obj = json.loads(p.read_text(encoding="utf-8"))
    stored = obj.get("scenario_sha256")
    recomputed = scenario_digest(obj)
    if stored != recomputed:
        raise ValueError(
            f"场景文件 {p} 的 scenario_sha256 与文件内容不符"
            f"（记录 {stored}，重算 {recomputed}）—— 文件被手改过，拒绝回放。")
    return obj


def _scenario_inputs(obj: dict) -> tuple[dict | None, dict | None]:
    """从场景文件里取出 (注入用的 external_conditions, 台账原料)。

    Returns:
        (inject, offline)
          inject  —— 传给 run_analysis 的 external_conditions（None = 不接外部条件）
          offline —— 台账装配用的原料（None = 本场景不装配台账）
    """
    snap = obj.get("weather_evidence_snapshot") or {}
    ec = snap.get("external_conditions")
    ext = snap.get("ledger_b03_ext") or {}
    has_ledger = bool(obj.get("evidence_ledger_enabled"))

    offline = None
    if has_ledger:
        if not ec:
            raise ValueError(
                f"场景 {obj.get('scenario_id')!r} 声明要装配证据台账，"
                "但它的天气快照里没有 external_conditions —— 场景文件不完整。")
        offline = {
            # 外部条件上下文：逐字来自存档
            "context": ec,
            # provider 这一层的原始返回：台账里的证据身份与状态都从这里取
            "raw_forecast": {
                "status": ext.get("status") or snap.get("status"),
                "provider": ext.get("provider"),
                "retrieved_at": ext.get("retrieved_at"),
                "error": ext.get("error"),
            },
            "case": ext.get("case"),
            "degraded": bool(ext.get("degraded")),
            "is_constructed": snap.get("is_constructed"),
            "label": snap.get("label"),
        }

    inject = None
    if ec:
        inject = dict(ec)
        if not has_ledger:
            # 没有台账 → 证据身份只能落在这里（否则回放出来的 run 会丢掉身份）
            for key in ("provider", "retrieved_at", "forecast_time"):
                if snap.get(key) is not None:
                    inject[key] = snap[key]
    return inject, offline


def replay_scenario_file(path: str | Path, *, dry_run: bool = False,
                         location: str | None = None,
                         model_path: str | Path | None = None) -> dict:
    """按**固定场景文件**回放一次（条件不漂移的那种回放）。

    Args:
        path:       场景文件路径，如 B07/scenarios/S3.json
        dry_run:    True = 只打印计划，不调 LLM、不落盘
        location:   覆盖场景文件里的位置（默认用场景文件里的）
        model_path: B06 模型产物路径（None = 用默认）

    Returns:
        {"run_id", "archive", "source", "warnings", "base_run_id", ...}
    """
    from B07.runs import DEFAULT_MODEL_PATH, run_analysis

    obj = _load_scenario_file(path)
    sid = obj.get("scenario_id")
    anchor = obj.get("analysis_time")
    loc = location or obj.get("location") or DEFAULT_LOCATION
    run_scenario = obj.get("run_scenario") or sid
    inject, offline = _scenario_inputs(obj)

    warnings: list[str] = []
    notes: list[str] = [
        f"固定场景回放：{path}",
        f"条件来自场景文件（analysis_time={anchor}，location={loc}），"
        "外部天气用快照，**没有重新取数**。",
    ]
    if offline is not None:
        notes.append("证据台账按场景快照里的 B03 证据重建（provider/状态/error 逐字保留）。")
    else:
        notes.append("本场景不装配证据台账（与来源存档一致）。")

    if dry_run:
        return {
            "run_id": None,
            "archive": None,
            "source": "B07.runs.run_analysis(scenario_file)",
            "warnings": warnings + ["dry-run：没有调用 LLM、没有落盘新 run。"],
            "dry_run": True,
            "base_run_id": obj.get("source_run_id"),
            "scenario_label": run_scenario,
            "notes": notes,
            "plan": {
                "scenario": sid,
                "run_scenario": run_scenario,
                "analysis_time": anchor,
                "location": loc,
                "scenario_file": str(path),
                "source_run_id": obj.get("source_run_id"),
                "external_conditions": bool(inject),
                "evidence_ledger": offline is not None,
                "llm_calls": 1,
                "new_runs": 1,
            },
        }

    factory = None
    if offline is not None:
        from B07.s4 import build_ledger

        def factory(bundle, _offline=offline, _anchor=anchor):
            return build_ledger(bundle, _offline, _anchor)

    archive = run_analysis(
        anchor=anchor,
        location=loc,
        model_path=model_path or DEFAULT_MODEL_PATH,
        scenario=run_scenario,
        external_conditions=inject,
        evidence_ledger=factory,
    )

    run_id = archive.get("run_id")
    # ★ 场景文件回放：基线固定为**场景文件里的 source_run_id**
    #   （不是"同场景最近的一条"——固定场景回放的对比对象就是它冻结时那一条）
    base_run_id = obj.get("source_run_id")

    _append_log(
        sid, run_id, offline=True, base_run_id=base_run_id,
        notes="；".join(notes + warnings)[:800],
        extra={
            "scenario_id": sid,
            "scenario_file": str(path),
            "run_scenario": run_scenario,
            "evidence_ledger": offline is not None,
            "source_run_id": obj.get("source_run_id"),
            "base_run_id_source": "scenario_file.source_run_id",
        },
    )

    return {
        "run_id": run_id,
        "archive": archive,
        "source": "B07.runs.run_analysis(scenario_file)",
        "warnings": warnings,
        "dry_run": False,
        "base_run_id": base_run_id,
        "scenario_label": run_scenario,
        "notes": notes,
    }


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
    # ★ 排除刚生成的这个 run —— 否则基线会选到自己（见 _base_run_for 的说明）
    base_run_id = _base_run_for(scenario_label, exclude_run_id=run_id)

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
    ap.add_argument("scenario", nargs="?",
                    help="S1 / S2 / S3 / S3_without / S4 / S5"
                         "（与 --scenario-file 二选一）")
    ap.add_argument("--scenario-file", default=None,
                    help="按固定场景文件回放（如 B07/scenarios/S3.json）："
                         "输入全部来自场景文件，外部天气用快照，不重新取数")
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
    ap.add_argument("--location", default=None,
                    help="位置标识（--scenario-file 时默认用场景文件里的）")
    ap.add_argument("--model", default=None, help="B06 已存模型产物路径")
    args = ap.parse_args(argv)

    if args.scenario_file:
        if args.scenario:
            ap.error("--scenario-file 与位置参数只能二选一")
        print("=" * 70)
        print("B07 视图四 · 固定场景回放（条件不漂移）")
        print("=" * 70)
        print(f"\n  场景文件：{args.scenario_file}")
        print(f"  天气来源：场景文件里的固定快照（**不重新取数**）")
        print(f"\n  {QUOTA_NOTICE}")
        try:
            res = replay_scenario_file(
                args.scenario_file, dry_run=args.dry_run,
                location=args.location, model_path=args.model)
        except Exception as exc:                # 失败显式暴露
            print(f"\n  ❌ 回放失败：{type(exc).__name__}: {exc}")
            return 1
        if res["dry_run"]:
            p = res["plan"]
            print("\n  ── 回放计划 ──")
            print(f"  场景        ：{p['scenario']}（run_scenario={p['run_scenario']}）")
            print(f"  analysis_time: {p['analysis_time']}")
            print(f"  位置        ：{p['location']}")
            print(f"  来源存档    ：{p['source_run_id']}（对比基线）")
            print(f"  外部条件    ：{'注入快照' if p['external_conditions'] else '不接入'}")
            print(f"  证据台账    ：{'按快照重建' if p['evidence_ledger'] else '不装配'}")
            print(f"  LLM 调用   ：{p['llm_calls']} 次")
            print("\n  说明：")
            for n in res["notes"]:
                print(f"    · {n}")
            print("\n  （dry-run 到此为止：没有调用 LLM，没有落盘新 run。）")
            return 0
        print(f"\n  ✅ 回放完成：{res['run_id']}")
        print(f"     入口    ：{res['source']}")
        print(f"     对比基线：{res['base_run_id']}（场景文件里的 source_run_id）")
        print(f"\n  回放日志：{REPLAY_LOG_PATH.relative_to(_PROJECT_ROOT)}")
        return 0

    if not args.scenario:
        ap.error("缺少场景参数（或用 --scenario-file 指定固定场景文件）")

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
                     location=args.location or DEFAULT_LOCATION,
                     model_path=args.model)
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
