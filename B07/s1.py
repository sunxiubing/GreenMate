"""B07 S1 — 统一入口：一条命令跑完整个 S1 流程

流程（严格按序，与 S1-1 ~ S1-5 一一对应）：
  1. 选稳定窗口   B07/stability.py   pick_stable_window()
  2. 取数         B07/evidence.py    build_evidence(anchor)   ← 时间锚点 = 选中窗口的终点
  3. 加载 B06 模型推理  B07/predict.py   predict_for_run()
  4. 结构化判断   B07/judgement.py   build_judgement()        ← 纯程序，先于 LLM 成立
  5. LLM 解释     B07/explain.py     explain_judgement()      ← 只解释，不判断
  6. 落 run 存档  B07/runs.py        run_analysis() + explain_run()
  7. 生成阶段报告 B07/artifacts/s1_report.md

用法：
  python -m B07.s1 --question "分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。"

硬约束：
  - 问题必须是 S1-4 定的固定问题，不接受改写
  - 只加载 B06 已存模型，不重训、不重切数据
  - 不修改 B01 / B02 / B05 / B06 的任何文件
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import DEFAULT_LOCATION
from B07.explain import FIXED_QUESTION

REPORT_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "s1_report.md"

STAGE_TITLES = {
    "evidence": "取数（当前状态 + 最近历史 + 时间锚点）",
    "predict": "加载 B06 模型推理",
    "judgement": "结构化判断（纯程序，无 LLM）",
    "llm_explanation": "LLM 解释（只解释，不判断）",
    "consistency_check": "一致性校验（只记录，不修改）",
}


# --------------------------------------------------------------------------
# 阶段 1：选稳定窗口
# --------------------------------------------------------------------------

def select_stable_window(window_size: int = 6) -> dict:
    """S1-1：扫描全部窗口，选出一个稳定窗口。

    Returns:
        {
          "anchor_time": datetime,     # 选中窗口的终点，作为后续的时间锚点
          "selected": {...},           # 窗口详情
          "reason": str,
          "candidate_count": int,
          "daytime_count": int,
          "pool": str,
          "total_samples": int,
          "data_range": [str, str],
        }
    """
    from B07.stability import load_all_samples, pick_stable_window

    samples = load_all_samples()
    picked = pick_stable_window(samples, window_size=window_size)

    if picked["selected"] is None:
        raise RuntimeError(f"没有可选的稳定窗口：{picked['reason']}")

    sel = picked["selected"]
    return {
        "anchor_time": datetime.fromisoformat(sel["end_time"]),
        "selected": sel,
        "reason": picked["reason"],
        "candidate_count": picked["candidate_count"],
        "daytime_count": picked["daytime_count"],
        "pool": picked["pool"],
        "total_samples": len(samples),
        "data_range": [samples[0].timestamp.isoformat(),
                       samples[-1].timestamp.isoformat()],
    }


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def build_report(archive: dict, explain_answer: str, explain_result: dict) -> str:
    """生成 B07/artifacts/s1_report.md 的内容。"""
    sj = archive.get("structured_judgement") or {}
    mo = archive.get("model_output") or {}
    mm = archive.get("model_meta") or {}
    hw = archive.get("history_window") or {}
    stab = (hw.get("stability") or {})
    cs = archive.get("current_state") or {}
    ws = archive.get("window_selection") or {}
    llm_meta = archive.get("llm_meta") or {}
    src = archive.get("evidence_sources") or {}
    warnings = archive.get("warnings") or []

    lines = []
    A = lines.append

    A("# B07 S1 阶段报告")
    A("")
    A(f"- **run_id**：`{archive.get('run_id')}`")
    A(f"- **created_at**：{archive.get('created_at')}")
    A(f"- **analysis_time**：{archive.get('analysis_time')}")
    A(f"- **location**：{archive.get('location')}")
    A(f"- **status**：{archive.get('status')}")
    A(f"- **question**：{archive.get('question')}")
    A(f"- **存档文件**：`B07/runs/{archive.get('run_id', '')}/run.json`")
    A("")

    A("## 1. 五类证据来源清单")
    A("")
    A("| 模块 | 来源 |")
    A("| --- | --- |")
    for k, v in src.items():
        A(f"| {k} | {v} |")
    A("")
    A(f"- 当前状态（source_type=`{cs.get('source_type')}`，采样于 {cs.get('timestamp')}）："
      f"{cs.get('temperature')}℃ / {cs.get('humidity')}% / {cs.get('lux')} lux")
    A(f"- 历史窗口：{hw.get('start')} → {hw.get('end')}（{hw.get('count')} 点）")
    A(f"- 库内最新（仅参考）：{(archive.get('latest_in_db') or {}).get('timestamp')}，"
      f"hours_gap={archive.get('hours_gap')} 小时")
    A("")

    if ws:
        A("## 2. 稳定窗口选取（S1-1）")
        A("")
        A(f"- 候选稳定窗口数：{ws.get('candidate_count')}（其中白天段 {ws.get('daytime_count')}）")
        A(f"- 选取理由：{ws.get('reason')}")
        A(f"- 时间锚点 = 选中窗口终点：{archive.get('analysis_time')}")
        A("")

    A("## 3. 稳定判据明细")
    A("")
    A("| 判据 | 实测 | 阈值 | 结论 |")
    A("| --- | --- | --- | --- |")
    for c in stab.get("criteria", []):
        A(f"| {c['name']} | {c['value']} | {c['threshold']} | "
          f"{'✅ 通过' if c['passed'] else '❌ 未通过'} |")
    A("")
    A(f"判定：**{'稳定' if stab.get('passed') else '不稳定'}**")
    A("")
    A(f"> {stab.get('conclusion')}")
    A("")

    A("## 4. 模型预测")
    A("")
    A(f"- `predicted_attention`：**{mo.get('predicted_attention')}**")
    A(f"- `confidence`：{mo.get('confidence')}（NORMAL {mo.get('confidence_normal')} / "
      f"WATCH {mo.get('confidence_watch')}）")
    A(f"- `model_version`：`{mo.get('model_version')}`（标签规则 {mm.get('label_source')}）")
    A(f"- `window_id`：{mo.get('window_id')}")
    A(f"- `evidence_type`：{mo.get('evidence_type')}")
    A(f"- DTW：温度 {mo.get('dtw_temperature')} / 湿度 {mo.get('dtw_humidity')} / "
      f"光照 {mo.get('dtw_lux')}")
    A(f"- `feature_count`：{mo.get('feature_count')}")
    A(f"- `errors`：{mo.get('errors')}")
    A(f"- 特征影响方法：`{mo.get('feature_contribution_method')}`（shap 未安装，改用系数排序）")
    A("")
    A("### coef Top5")
    A("")
    A("| # | feature | coefficient | direction |")
    A("| --- | --- | --- | --- |")
    for i, c in enumerate(mo.get("coef_top5") or [], 1):
        A(f"| {i} | `{c['feature']}` | {c['coefficient']:+.6f} | {c['direction']} |")
    A("")

    A("## 5. 结构化判断五字段")
    A("")
    A("### ① current_environment")
    A("")
    A("```json")
    A(_json(sj.get("current_environment")))
    A("```")
    A("")
    A("### ② recent_trend")
    A("")
    A("```json")
    A(_json(sj.get("recent_trend")))
    A("```")
    A("")
    A("### ③ predicted_attention")
    A("")
    A(f"**{sj.get('predicted_attention')}**（原样照抄 model_output，未加工）")
    A("")
    A("### ④ evidence_status")
    A("")
    A(f"{sj.get('evidence_status', {}).get('summary')}")
    A("")
    A("| | 模块 | 条目 | 详情 |")
    A("| --- | --- | --- | --- |")
    for item in sj.get("evidence_status", {}).get("items", []):
        A(f"| {'✅' if item['present'] else '⭕'} | {item['module']} | {item['item']} | "
          f"{item['detail']} |")
    A("")
    A("### ⑤ limitations")
    A("")
    lim = sj.get("limitations") or {}
    for key in lim.get("all_keys", []):
        A(f"- **[{key}]** {lim.get(key, {}).get('text')}")
    A("")

    A("## 6. LLM 解释原文")
    A("")
    A(f"> {archive.get('llm_explanation')}")
    A("")
    A(f"- provider：`{llm_meta.get('provider')}`")
    A(f"- model：`{llm_meta.get('model')}`")
    A(f"- elapsed_ms：{llm_meta.get('elapsed_ms')}")
    A(f"- content_length：{llm_meta.get('content_length')}")
    A("")

    A("## 7. 一致性校验与 warnings")
    A("")
    if warnings:
        for w in warnings:
            A(f"- ⚠️ {w}")
    else:
        A("- 无 warning：结论无冲突，解释未引入未取得的事实。")
    A("")

    A("## 8. explain_run 回溯输出")
    A("")
    A("```text")
    A(explain_answer)
    A("```")
    A("")
    A("### 复算一致性")
    A("")
    A("| 检查项 | 存档值 | 复算值 | 一致 |")
    A("| --- | --- | --- | --- |")
    for c in explain_result.get("checks", []):
        A(f"| {c['name']} | {c['archived']} | {c['recomputed']} | "
          f"{'✅' if c['match'] else '⚠️'} |")
    A("")

    A("---")
    A("")
    A("本报告由 `python -m B07.s1` 自动生成。")
    A("")

    return "\n".join(lines)


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run_s1(question: str, location: str = DEFAULT_LOCATION,
            model_path: str | Path | None = None,
            write_report: bool = True) -> dict:
    """跑完 S1 全流程并返回 {'archive', 'explain', 'report_path'}。"""
    from B07.runs import run_analysis, explain_run

    if model_path is None:
        from B07.runs import DEFAULT_MODEL_PATH
        model_path = DEFAULT_MODEL_PATH

    # ---- 阶段 1：选稳定窗口 ----
    print("[1/6] 选稳定窗口...")
    ws = select_stable_window()
    print(f"  候选稳定窗口 {ws['candidate_count']} 个（白天段 {ws['daytime_count']} 个）")
    print(f"  选中窗口：{ws['selected']['start_time']} → {ws['selected']['end_time']}")
    print(f"  时间锚点：{ws['anchor_time'].isoformat()}")
    print(f"  理由：{ws['reason']}")

    window_selection = {
        "candidate_count": ws["candidate_count"],
        "daytime_count": ws["daytime_count"],
        "pool": ws["pool"],
        "reason": ws["reason"],
        "selected": {
            "start_time": ws["selected"]["start_time"],
            "end_time": ws["selected"]["end_time"],
            "is_daytime": ws["selected"]["is_daytime"],
            "temp_range": ws["selected"]["temp_range"],
            "humi_total": ws["selected"]["humi_total"],
            "humi_step_max": ws["selected"]["humi_step_max"],
            "lux_step_max": ws["selected"]["lux_step_max"],
            "conclusion": ws["selected"]["conclusion"],
        },
        "total_samples": ws["total_samples"],
        "data_range": ws["data_range"],
        "anchor_time": ws["anchor_time"].isoformat(),
    }

    # ---- 阶段 2~6：取数 → 推理 → 判断 → 解释 → 存档 ----
    stage_no = {"evidence": 2, "predict": 3, "judgement": 4,
                "llm_explanation": 5, "consistency_check": 6}

    def _progress(stage, extra):
        extra = extra or {}
        title = STAGE_TITLES.get(stage, stage)
        print(f"\n[{stage_no.get(stage, '?')}/6] {title}...")
        if stage == "evidence":
            cs = extra.get("current_state") or {}
            print(f"  当前状态：{cs.get('temperature')}℃ / {cs.get('humidity')}% / "
                  f"{cs.get('lux')} lux @ {cs.get('timestamp')}")
            print(f"  历史窗口：{extra.get('history_count')} 点，hours_gap={extra.get('hours_gap')}")
            print(f"  稳定判定：{'稳定' if (extra.get('stability') or {}).get('passed') else '不稳定'}")
        elif stage == "predict":
            print(f"  predicted_attention={extra.get('predicted_attention')}，"
                  f"confidence={extra.get('confidence')}，"
                  f"model_version={extra.get('model_version')}")
            print(f"  errors：{extra.get('errors')}")
        elif stage == "judgement":
            sc = extra.get("self_check") or {}
            print(f"  字段：{sc.get('fields')}")
            print(f"  复算自检：{'✅ 逐字段一致' if sc.get('identical') else '❌ 不一致'}")
        elif stage == "llm_explanation":
            m = extra.get("llm_meta") or {}
            print(f"  model={m.get('model')}，content_length={m.get('content_length')}，"
                  f"elapsed_ms={m.get('elapsed_ms')}")
        elif stage == "consistency_check":
            ws_ = extra.get("warnings") or []
            print(f"  warnings：{len(ws_)} 条")
            for w in ws_:
                print(f"    ⚠️ {w}")

    archive = run_analysis(ws["anchor_time"], location, model_path,
                           window_selection=window_selection, progress=_progress)

    # ---- 回溯（从落盘文件重读，重新算一遍）----
    explain_result = explain_run(archive["run_id"], quiet=True)

    # ---- 报告 ----
    report_path = None
    if write_report:
        report_path = REPORT_PATH
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            build_report(archive, explain_result["answer"], explain_result),
            encoding="utf-8")

    return {
        "archive": archive,
        "explain": explain_result,
        "report_path": report_path,
    }


def main():
    parser = argparse.ArgumentParser(
        description="B07 S1 统一入口：选稳定窗口 → 取数 → 推理 → 判断 → 解释 → 存档")
    parser.add_argument("--question", type=str, default=FIXED_QUESTION,
                        help="固定问题（S1-4 硬约束，不可改写）")
    parser.add_argument("--location", type=str, default=DEFAULT_LOCATION)
    parser.add_argument("--model", type=str, default=None,
                        help="B06 已存模型产物路径")
    parser.add_argument("--no-report", action="store_true",
                        help="不生成 B07/artifacts/s1_report.md")
    args = parser.parse_args()

    print("=" * 70)
    print("B07 S1 — 统一入口（稳态基线）")
    print("=" * 70)
    print(f"\n  固定问题：{args.question}")

    # 固定问题不可改写（S1-4 硬约束）：解释环节用的就是这个问题，
    # 换一个问法会让"本次问题"与存档里的问题对不上，事后回溯会失真。
    if args.question.strip() != FIXED_QUESTION.strip():
        print(f"\n  ❌ S1 的问题不可改写。\n"
              f"     传入：{args.question}\n"
              f"     固定：{FIXED_QUESTION}\n"
              f"     原因：LLM 解释环节用的就是固定问题，换问法会让存档里的 question "
              f"与实际提问不一致，事后回溯失真。")
        sys.exit(2)

    try:
        result = run_s1(args.question, args.location, args.model,
                        write_report=not args.no_report)
    except Exception as e:
        print(f"\n  ❌ S1 执行失败：{type(e).__name__}: {e}")
        print("     失败已留痕（见 B07/runs/ 下的 status=failed 存档）")
        sys.exit(1)

    archive = result["archive"]
    sj = archive["structured_judgement"]

    print("\n" + "=" * 70)
    print(f"【run_id】{archive['run_id']}")
    print("=" * 70)

    print("\n【结构化判断五字段】")
    print("-" * 70)
    ce = sj["current_environment"]
    print(f"  ① current_environment: 温度 {ce['temperature']}℃，湿度 {ce['humidity']}%，"
          f"光照 {ce['lux']} lux，来源 {ce['source_type']}，采样于 {ce['sampled_at']}")
    rt = sj["recent_trend"]
    print(f"  ② recent_trend: {rt['stability_verdict']}；{rt['conclusion']}")
    print(f"  ③ predicted_attention: {sj['predicted_attention']}")
    print(f"  ④ evidence_status: {sj['evidence_status']['summary']}")
    for item in sj["evidence_status"]["items"]:
        print(f"       {'✅' if item['present'] else '⭕'} [{item['module']}] "
              f"{item['item']}: {item['detail']}")
    print("  ⑤ limitations:")
    for key in sj["limitations"]["all_keys"]:
        print(f"       - [{key}] {sj['limitations'][key]['text']}")

    print("\n【LLM 解释】")
    print("-" * 70)
    print(archive["llm_explanation"])
    m = archive["llm_meta"]
    print(f"\n  — model={m['model']}，provider={m['provider']}，"
          f"elapsed_ms={m['elapsed_ms']}，content_length={m['content_length']}")

    print("\n【一致性校验 / warnings】")
    print("-" * 70)
    if archive["warnings"]:
        for w in archive["warnings"]:
            print(f"  ⚠️  {w}")
    else:
        print("  无")

    if result["report_path"]:
        print(f"\n【阶段报告】{result['report_path']}")
    print(f"\n【存档文件】B07/runs/{archive['run_id']}/run.json")
    print(f"【回溯】python -m B07.runs explain {archive['run_id']}")

    print("\n" + "=" * 70)
    print("B07 S1 完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
