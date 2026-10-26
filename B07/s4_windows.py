"""B07 S4-1 —— 冲突场景的历史窗口选取（WATCH 窗口扫描）

S4 文档第一步要求："B06 输出 WATCH" 与 "未来降温"这类反向证据**并存**。
这一层负责把那个 "B06 输出 WATCH" 的窗口**真实找出来**。

规矩：

  1. **不改模型、不篡改模型输出**。扫描直接调 B06 的
     `load_for_inference` / `predict_window`，模型产物原样加载、
     预测结果原样记录。B06 模型 recall=0.448，漏报是已知事实。
  2. **两套 DTW 参考集都跑**。因为 B07 生产路径
     (`B07.predict.predict_for_run`) 用的历史窗口集合是
     `_build_history_windows(limit=50)`，即**全库末 50 个窗口**，
     对靠前的窗口来说含时间前视。为了不把"前视"当成"信号"，
     这里同时算一套**因果**版本：参考集只含结束时间早于当前窗口起点的
     窗口（与 B06 训练时 `compute_dtw(windows[i], windows[:i])` 一致）。
     两套都判 WATCH 的窗口才算**稳健**候选。
  3. **如实记录实际选中的窗口与理由**。找不到 WATCH 就如实说找不到，
     绝不含糊成"已找到"。

硬约束：

  - 不改 B01 / B02 / B03 / B04 / B05 / B06 任何文件
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不重训模型
  - 不接进任何既有流程（本轮只产出可独立调用的能力）

用法：

    from B07.s4_windows import scan_watch_windows, pick_watch_window
    scan = scan_watch_windows()
    picked = pick_watch_window(scan)

自检（打印全部候选 + 选中项 + 理由）：

    python -m B07.s4_windows
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import HISTORY_POINTS
from B07.stability import judge_stability, load_all_samples

#: B06 模型产物路径（与 B07.predict.DEFAULT_MODEL_PATH 一致）
DEFAULT_MODEL_PATH = _PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1"

#: S2 渐进漂移窗口（2026-10-20T12:00:00，末点 32.8℃，ΔT=7.5）
#: 实测 B06 在此窗口输出 **NORMAL**（recall=0.448 的漏报）。
#: 这个不一致本身就是 S4 要处理的分歧之一，所以明确记在这里。
S2_DRIFT_ANCHOR = "2026-10-20T12:00:00"
S2_DRIFT_END_TEMP = 32.8
S2_DRIFT_DELTA_T = 7.5
S2_DRIFT_MODEL_OUTPUT = "NORMAL"


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

def scan_watch_windows(model_path=None, window_size: int = HISTORY_POINTS) -> dict:
    """扫描 B02 全部可用窗口，找 predicted_attention == "WATCH" 的窗口。

    Args:
        model_path:  B06 模型产物路径。
        window_size: 窗口点数（与 S1/S2 一致，取 6）。

    Returns:
        {
          "rows": [ {index, end_time, delta_t, stable, temps, lux,
                     causal_label, causal_confidence,
                     prod_label, prod_confidence,
                     both_watch, any_watch}, ... ],
          "watch_rows": [ 只含任一参考集判 WATCH 的行 ],
          "robust_rows": [ 只含两套都判 WATCH 的行 ],
          "counts": {...},
          "model_version": str,
        }
    """
    from B06.inference.inference import (
        _build_history_windows,
        load_for_inference,
        predict_window,
    )
    from B06.generate import generate_windows
    from B06.SPEC import TimeWindow

    model_path = Path(model_path or DEFAULT_MODEL_PATH)
    model, meta = load_for_inference(str(model_path))

    samples = load_all_samples()
    all_windows = generate_windows(samples)      # windows[j] = history samples[j:j+6]
    prod_history = _build_history_windows(limit=50)   # B07 生产路径用的那套

    rows = []
    for i in range(window_size - 1, len(samples)):
        hist = list(samples[i - window_size + 1:i + 1])

        # ---- 因果 DTW 参考集：只含结束时间早于本窗口起点的窗口 ----
        past = [w for w in all_windows
                if w.history[-1].timestamp < hist[0].timestamp][-50:]

        o_causal = predict_window(
            TimeWindow(window_id=f"causal_{i}", source="SIMULATED",
                       history=list(hist), target=[]),
            model, meta, history_windows=past if past else None)
        o_prod = predict_window(
            TimeWindow(window_id=f"prod_{i}", source="SIMULATED",
                       history=list(hist), target=[]),
            model, meta, history_windows=prod_history)

        temps = [s.temperature for s in hist]
        judged = judge_stability(hist)

        causal_label = str(o_causal.predicted_attention)
        prod_label = str(o_prod.predicted_attention)

        rows.append({
            "index": i,
            "end_time": hist[-1].timestamp.isoformat(),
            "start_time": hist[0].timestamp.isoformat(),
            "points": len(hist),
            "delta_t": round(temps[-1] - temps[0], 2),
            "temp_min": round(min(temps), 1),
            "temp_max": round(max(temps), 1),
            "stable": bool(judged["passed"]),
            "stability_conclusion": judged["conclusion"],
            "temps": temps,
            "lux": [s.lux for s in hist],
            "causal_label": causal_label,
            "causal_confidence": round(float(o_causal.confidence), 4),
            "causal_confidence_watch": round(float(o_causal.confidence_watch), 4),
            "prod_label": prod_label,
            "prod_confidence": round(float(o_prod.confidence), 4),
            "prod_confidence_watch": round(float(o_prod.confidence_watch), 4),
            "both_watch": causal_label == "WATCH" and prod_label == "WATCH",
            "any_watch": causal_label == "WATCH" or prod_label == "WATCH",
            "dtw_temperature": round(float(o_causal.dtw_temperature), 3),
        })

    watch_rows = [r for r in rows if r["any_watch"]]
    robust_rows = [r for r in rows if r["both_watch"]]

    return {
        "rows": rows,
        "watch_rows": watch_rows,
        "robust_rows": robust_rows,
        "counts": {
            "total": len(rows),
            "any_watch": len(watch_rows),
            "both_watch": len(robust_rows),
            "causal_watch": sum(1 for r in rows if r["causal_label"] == "WATCH"),
            "prod_watch": sum(1 for r in rows if r["prod_label"] == "WATCH"),
            "causal_only": sum(1 for r in rows
                               if r["causal_label"] == "WATCH"
                               and r["prod_label"] != "WATCH"),
            "prod_only": sum(1 for r in rows
                             if r["causal_label"] != "WATCH"
                             and r["prod_label"] == "WATCH"),
        },
        "model_version": meta.get("model_version"),
    }


# --------------------------------------------------------------------------
# 选窗
# --------------------------------------------------------------------------

def pick_watch_window(scan: dict) -> dict:
    """从扫描结果里选一个 WATCH 窗口作为 S4 的历史锚点。

    选窗规则（显式、可复述）：

      1. **必须真实输出 WATCH** —— 以两套 DTW 参考集都判 WATCH
         （robust_rows）为准。理由：只在一套参考集下判 WATCH 的窗口，
         其信号可能来自 DTW 参考集的时间前视，不可靠。
      2. 在 robust 里优先取**不稳定**窗口（stable=False）——
         S4 要的是"模型报了 WATCH 但内部状态本身在动"的场景，
         稳定窗口上的 WATCH 没有内部矛盾可展示。
      3. 稳定窗口与不稳定窗口都合格时，取**离 B02 数据末点最近**者，
         以便与 S1/S2/S3 的存档时间线可比。
      4. 若一个 WATCH 窗口都没有 → 回落到 S2 漂移窗口，并**显式写明**
         该窗口上 B06 输出为 NORMAL（漏报），这个不一致本身就是
         S4 要处理的分歧之一。

    Returns:
        {
          "selected": {...} | None,
          "reason": str,
          "fallback_to_s2": bool,
          "notes": [str, ...],
        }
    """
    robust = scan.get("robust_rows") or []
    notes = []

    if robust:
        unstable = [r for r in robust if not r["stable"]]
        pool = unstable if unstable else robust
        selected = max(pool, key=lambda r: r["end_time"])

        if unstable:
            reason = (
                f"共扫描 {scan['counts']['total']} 个窗口，"
                f"其中 {scan['counts']['both_watch']} 个在因果 DTW 与生产路径 DTW "
                f"两套参考集下都真实输出 WATCH（robust）；"
                f"robust 候选中 {len(unstable)} 个内部状态不稳定；"
                f"在不稳定候选里取时间最晚者 → {selected['end_time']}"
                f"（ΔT={selected['delta_t']:+.1f}℃，"
                f"温度 {selected['temp_min']}~{selected['temp_max']}℃，"
                f"causal confidence={selected['causal_confidence']}，"
                f"prod confidence={selected['prod_confidence']}）"
            )
        else:
            reason = (
                f"共扫描 {scan['counts']['total']} 个窗口，"
                f"{scan['counts']['both_watch']} 个两套参考集下都输出 WATCH；"
                f"但全部落在内部稳定窗口上（无温度/湿度/光照越阈），"
                f"取时间最晚者 → {selected['end_time']}"
                f"（ΔT={selected['delta_t']:+.1f}℃）"
            )

        notes.append(
            "本窗口的 WATCH 是 B06 模型的真实输出，未改模型、未篡改结果；"
            "两套 DTW 参考集（因果 / 生产路径）都给出 WATCH，说明信号不依赖"
            "DTW 参考集的时间前视。"
        )
        if unstable:
            notes.append(
                "该窗口同时被判为内部不稳定（温度/湿度/光照至少一项越阈），"
                "因此存在『模型报 WATCH、内部状态也在动』的可展示矛盾。"
            )

        return {
            "selected": selected,
            "reason": reason,
            "fallback_to_s2": False,
            "notes": notes,
        }

    # ---- 没有 robust WATCH → 回落 S2 漂移窗口，如实写明漏报 ----
    notes.append(
        f"在全部 {scan['counts']['total']} 个窗口中，"
        f"没有任何窗口在两套 DTW 参考集下都真实输出 WATCH"
        f"（因果口径 WATCH {scan['counts']['causal_watch']} 个，"
        f"生产路径口径 WATCH {scan['counts']['prod_watch']} 个，"
        f"两套一致 {scan['counts']['both_watch']} 个），"
        f"因此回落到 S2 渐进漂移窗口。"
    )
    notes.append(
        f"**本场景中 B06 在 S2 漂移窗口（{S2_DRIFT_ANCHOR}，末点 "
        f"{S2_DRIFT_END_TEMP}℃，ΔT={S2_DRIFT_DELTA_T}）输出为 "
        f"{S2_DRIFT_MODEL_OUTPUT}** —— 模型在此处未发现升温趋势（漏报，"
        f"B06 recall=0.448）。该不一致本身就是 S4 要处理的分歧之一："
        f"内部证据与外部证据不同向时，系统必须显式呈现分歧，"
        f"而不是由某一层单方面盖棺定论。"
    )

    s2_row = next((r for r in scan.get("rows") or []
                   if r["end_time"] == S2_DRIFT_ANCHOR), None)
    return {
        "selected": s2_row,
        "reason": (
            f"未找到在两套 DTW 参考集下都真实输出 WATCH 的窗口，"
            f"回落到 S2 渐进漂移窗口 {S2_DRIFT_ANCHOR}"
            f"（末点 {S2_DRIFT_END_TEMP}℃，ΔT={S2_DRIFT_DELTA_T}）；"
            f"该窗口上 B06 实际输出为 {S2_DRIFT_MODEL_OUTPUT}（漏报）"
        ),
        "fallback_to_s2": True,
        "notes": notes,
    }


def selected_anchor(scan: dict | None = None) -> str:
    """S4 冲突场景的历史锚点（ISO 字符串）。

    唯一真源是 pick_watch_window() 的实测结果 —— 这里不硬编码任何时刻，
    避免"选窗改了但锚点没改"的两处漂移。
    """
    if scan is None:
        scan = scan_watch_windows()
    picked = pick_watch_window(scan)
    sel = picked.get("selected")
    if not sel:
        raise LookupError("选窗结果里没有选中窗口，无法确定锚点")
    return sel["end_time"]


# --------------------------------------------------------------------------
# 自检入口
# --------------------------------------------------------------------------

def _fmt_row(r: dict) -> str:
    return (f"{r['end_time']}  ΔT={r['delta_t']:+6.1f}℃  "
            f"stable={'Y' if r['stable'] else 'N'}  "
            f"causal={r['causal_label']:<6}({r['causal_confidence']:.4f})  "
            f"prod={r['prod_label']:<6}({r['prod_confidence']:.4f})  "
            f"both={'Y' if r['both_watch'] else 'N'}")


def main() -> int:
    print("=" * 78)
    print("B07 S4-1 —— WATCH 窗口扫描（B06 真实输出，不改模型）")
    print("=" * 78)

    scan = scan_watch_windows()
    c = scan["counts"]
    print(f"\n模型版本：{scan['model_version']}")
    print(f"扫描窗口总数：{c['total']}")
    print(f"  因果 DTW 口径判 WATCH：{c['causal_watch']}")
    print(f"  生产路径 DTW 口径判 WATCH：{c['prod_watch']}")
    print(f"  两套都判 WATCH（robust）：{c['both_watch']}")
    print(f"  仅因果判 WATCH：{c['causal_only']}；仅生产路径判 WATCH：{c['prod_only']}")

    print("\n" + "-" * 78)
    print("全部 WATCH 候选窗口（任一参考集判 WATCH）")
    print("-" * 78)
    print(f"{'end_time':<22}{'ΔT':>9}  stable  causal(prob)         prod(prob)         both")
    for r in scan["watch_rows"]:
        print(f"{r['end_time']:<22}{r['delta_t']:>+9.1f}  "
              f"{'Y' if r['stable'] else 'N'}       "
              f"{r['causal_label']:<6}({r['causal_confidence']:.4f})  "
              f"{r['prod_label']:<6}({r['prod_confidence']:.4f})  "
              f"{'Y' if r['both_watch'] else 'N'}")

    print("\n" + "-" * 78)
    print("实际选中的窗口")
    print("-" * 78)
    picked = pick_watch_window(scan)
    print(f"理由：{picked['reason']}\n")
    for n in picked["notes"]:
        print(f"  · {n}")
    sel = picked["selected"]
    if sel:
        print(f"\n选中窗口明细：")
        for k in ("index", "start_time", "end_time", "points", "delta_t",
                  "temp_min", "temp_max", "stable", "causal_label",
                  "causal_confidence", "prod_label", "prod_confidence"):
            print(f"  {k:<18}: {sel[k]}")
        print(f"  temps              : {sel['temps']}")
        print(f"  stability_conclusion: {sel['stability_conclusion']}")

    print("\n" + "=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
