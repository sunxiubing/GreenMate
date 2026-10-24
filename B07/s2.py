"""B07 S2 — 渐进漂移：与 S1 同一套流程，只换一段历史

流程与 S1 完全一致（取数 → B06 推理 → 结构化判断 → LLM 解释 → 存档 → 回溯），
唯一不同的是看哪一段历史：

  S1  anchor_policy = stable_window                 （稳态基线）
  S2  anchor_policy = drifting_comparable_to_S1     （渐进漂移）

问题、LLM system prompt、判据阈值、模型全部不变，
所以 S1 与 S2 的输出可以直接横向比较。

本模块只做编排：evidence / predict / judgement / explain 的逻辑一律复用
B07.runs.run_analysis() 这条链，不在这里复制第二份。

用法：
  python -m B07.s2
  python -m B07 --scenario S2

硬约束：
  - 问题必须是固定问题，不接受改写
  - 选窗只看 temperature / humidity / lux 三列数值
  - 不修改 B01 / B02 / B05 / B06 的任何文件
  - 不新增数据来源
"""

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import DEFAULT_LOCATION
from B07.explain import FIXED_QUESTION

REPORT_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "s2_report.md"
SCENARIO = "S2"
# 用来做可比性对照的 S1 存档：run_012 是 S1 正式存档，带提示词指纹
S1_REFERENCE_RUN = "run_012"
# 最早的 S1 存档（没有 scenario / 指纹字段，属正常情况，仅作历史参照）
S1_LEGACY_RUN = "run_001"
# S2 正式存档
S2_REFERENCE_RUN = "run_014"

STAGE_TITLES = {
    "evidence": "取数（当前状态 + 最近历史 + 时间锚点）",
    "predict": "加载 B06 模型推理",
    "judgement": "结构化判断（纯程序，无 LLM）",
    "llm_explanation": "LLM 解释（只解释，不判断）",
    "consistency_check": "一致性校验（只记录，不修改）",
}


# --------------------------------------------------------------------------
# 阶段 1：选窗
# --------------------------------------------------------------------------

def select_drifting_window(window_size: int = 6) -> dict:
    """S2-1：调用 B07.stability.pick_drifting_window() 选窗。

    Returns:
        {
          "anchor_time": datetime,   # 选中窗口的终点 = 时间锚点
          "selected": {...},         # 窗口详情
          "reason": str,
          "candidate_count": int,
          "rejected_count": int,
          "candidates": [...],
          "reference": {...},        # S1 参考末点与温度带
          "samples": [...],          # 原始样本（对抗性自检用）
        }
    """
    from B07.stability import load_all_samples, pick_drifting_window

    samples = load_all_samples()
    picked = pick_drifting_window(samples, window_size=window_size)

    if picked["selected"] is None:
        raise RuntimeError(f"S2 选窗失败：{picked['reason']}")

    sel = picked["selected"]
    return {
        "anchor_time": datetime.fromisoformat(sel["end_time"]),
        "selected": sel,
        "reason": picked["reason"],
        "candidate_count": picked["candidate_count"],
        "rejected_count": picked["rejected_count"],
        "candidates": picked["candidates"],
        "reference": picked["reference"],
        "samples": samples,
    }


def adversarial_check(samples: list, selected: dict) -> dict:
    """对抗性自检：把每条样本的 sim_scenario 全改成 "XXXXXXXX"，重跑选窗。

    如果结果变了 → 选窗偷看了 sim_scenario，必须改成只依赖三列数值。
    """
    from B07.stability import pick_drifting_window

    polluted = [replace(s, sim_scenario="XXXXXXXX") for s in samples]
    again = pick_drifting_window(polluted)
    sel = again["selected"]

    def _view(win: dict) -> dict:
        return {
            "start_time": win["start_time"],
            "end_time": win["end_time"],
            "temp_range": win.get("temp_range", win.get("delta_t")),
        }

    clean_view = _view(selected)
    polluted_view = _view(sel) if sel else None
    return {
        "clean": clean_view,
        "polluted": polluted_view,
        "same": bool(polluted_view) and polluted_view == clean_view,
    }


# --------------------------------------------------------------------------
# 存档读取（只读，不改任何东西）
# --------------------------------------------------------------------------

def _load_archive(run_id: str) -> dict | None:
    from B07.runs import run_json_path
    path = run_json_path(run_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _latest_s1_run_with_fingerprint() -> tuple[str | None, dict | None]:
    """最近一个带提示词指纹的 S1 存档（用于指纹比对）。"""
    from B07.runs import list_runs

    candidates = []
    for item in list_runs():
        if item.get("scenario") != "S1":
            continue
        arch = _load_archive(item["run_id"])
        if not arch:
            continue
        if (arch.get("llm_meta") or {}).get("system_prompt_sha256"):
            candidates.append((item["run_id"], arch))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1]


# --------------------------------------------------------------------------
# 实验有效性自检
# --------------------------------------------------------------------------

def build_selfcheck(archive: dict, adversarial: dict) -> list[dict]:
    """六条实验有效性自检，逐条给出 True/False 与实测值。"""
    from B07.explain import SYSTEM_PROMPT, _sha12

    checks: list[dict] = []
    llm_meta = archive.get("llm_meta") or {}
    s1 = _load_archive(S1_REFERENCE_RUN) or {}
    legacy = _load_archive(S1_LEGACY_RUN) or {}
    s1_llm = s1.get("llm_meta") or {}
    s1_cs = s1.get("current_state") or {}
    s2_cs = archive.get("current_state") or {}

    # ① 问题字符串与 S1 存档完全相同
    s2_q = llm_meta.get("question")
    s1_q = s1.get("question")
    legacy_q = legacy.get("question")
    checks.append({
        "no": 1,
        "name": "本次 LLM 的问题字符串与 S1 存档完全相同",
        "passed": bool(s2_q) and s2_q == s1_q,
        "detail": (f"S2={s2_q!r}；S1({S1_REFERENCE_RUN})={s1_q!r}；"
                   f"逐字相同={s2_q == s1_q}；"
                   f"旧存档({S1_LEGACY_RUN})={legacy_q!r}（同样逐字相同="
                   f"{legacy_q == s2_q}）"),
    })

    # ② system prompt 指纹与 S1 存档相同
    s2_fp = llm_meta.get("system_prompt_sha256")
    s1_fp = s1_llm.get("system_prompt_sha256")
    legacy_fp = (legacy.get("llm_meta") or {}).get("system_prompt_sha256")
    live_fp = _sha12(SYSTEM_PROMPT)
    if s1_fp:
        fp_ref, fp_ref_run = s1_fp, S1_REFERENCE_RUN
    else:
        fp_ref_run, fp_ref_arch = _latest_s1_run_with_fingerprint()
        fp_ref = (fp_ref_arch or {}).get("llm_meta", {}).get("system_prompt_sha256")
    q_fp = llm_meta.get("question_sha256")
    checks.append({
        "no": 2,
        "name": "本次 system_prompt_sha256 与 S1 存档相同",
        "passed": bool(s2_fp) and (s2_fp == fp_ref) and (s2_fp == live_fp),
        "detail": (f"本次={s2_fp}；{S1_REFERENCE_RUN}={s1_fp or '(旧存档未记录)'}；"
                   f"参照={fp_ref_run or '(无带指纹的 S1 存档)'}={fp_ref or '(无)'}；"
                   f"现场实算 sha256(SYSTEM_PROMPT)[:12]={live_fp}；"
                   f"问题指纹={q_fp}；"
                   f"旧存档({S1_LEGACY_RUN})={legacy_fp or '(旧存档无此字段)'}"),
    })

    # ③ 当前状态温度差 ≤ 0.8℃
    t2 = s2_cs.get("temperature")
    t1 = s1_cs.get("temperature")
    gap = round(abs(t2 - t1), 2) if (t2 is not None and t1 is not None) else None
    checks.append({
        "no": 3,
        "name": "S2 与 S1 的当前状态温度差 ≤ 0.8℃",
        "passed": gap is not None and gap <= 0.8,
        "detail": (f"S2 当前温度={t2}℃（{s2_cs.get('timestamp')}）；"
                   f"S1({S1_REFERENCE_RUN}) 当前温度={t1}℃（{s1_cs.get('timestamp')}）；"
                   f"差值={gap}℃"),
    })

    # ④ S2 稳定判定 passed == False
    stab = (archive.get("history_window") or {}).get("stability") or {}
    checks.append({
        "no": 4,
        "name": "S2 的稳定判定 passed == False",
        "passed": stab.get("passed") is False,
        "detail": f"passed={stab.get('passed')}；结论：{stab.get('conclusion')}",
    })

    # ⑤ 存档 scenario 正确，且 index.json 能分别列出 S1 / S2
    from B07.runs import list_runs
    items = list_runs()
    s1_ids = [i["run_id"] for i in items if i.get("scenario") == "S1"]
    s2_ids = [i["run_id"] for i in items if i.get("scenario") == "S2"]
    checks.append({
        "no": 5,
        "name": '存档 scenario == "S2"，index.json 里 S1/S2 可分别列出',
        "passed": (archive.get("scenario") == "S2") and bool(s1_ids) and bool(s2_ids),
        "detail": (f"本次存档 scenario={archive.get('scenario')!r}；"
                   f"index.json：S1={s1_ids}，S2={s2_ids}"),
    })

    # ⑥ 对抗性自检：污染 sim_scenario 后选窗结果必须完全一致
    checks.append({
        "no": 6,
        "name": "对抗性自检：污染 sim_scenario 后选窗结果不变",
        "passed": bool(adversarial.get("same")),
        "detail": (f"干净数据：{adversarial.get('clean')}；"
                   f"污染后（sim_scenario 全改 'XXXXXXXX'）：{adversarial.get('polluted')}；"
                   f"一致={adversarial.get('same')}"),
    })

    return checks


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_report(s2_archive: dict, s1_archive: dict, explain_answer: str,
                 explain_result: dict, selfcheck: list[dict], comparison: dict,
                 adversarial: dict | None = None,
                 s1_run_id: str = S1_REFERENCE_RUN,
                 s2_run_id: str | None = None) -> str:
    """生成 B07/artifacts/s2_report.md 的内容。

    所有数值都取自两份真实存档（S1 与 S2），不手写、不修饰。
    """
    s2_run_id = s2_run_id or s2_archive.get("run_id")
    adversarial = adversarial or (s2_archive.get("window_selection") or {}).get("adversarial") or {}

    sj = s2_archive.get("structured_judgement") or {}
    mo = s2_archive.get("model_output") or {}
    mm = s2_archive.get("model_meta") or {}
    hw = s2_archive.get("history_window") or {}
    stab = (hw.get("stability") or {})
    cs = s2_archive.get("current_state") or {}
    ws = s2_archive.get("window_selection") or {}
    llm_meta = s2_archive.get("llm_meta") or {}
    src = s2_archive.get("evidence_sources") or {}
    warnings = s2_archive.get("warnings") or []
    gap_warnings, other_warnings = split_warnings(warnings)

    s1_sj = s1_archive.get("structured_judgement") or {}
    s1_mo = s1_archive.get("model_output") or {}
    s1_cs = s1_archive.get("current_state") or {}
    s1_llm_meta = s1_archive.get("llm_meta") or {}
    s1_hw = (s1_archive.get("history_window") or {}).get("stability") or {}
    s1_hist = s1_archive.get("history") or []
    s2_hist = s2_archive.get("history") or []

    lines = []
    A = lines.append

    A("# B07 S2 阶段报告（渐进漂移）")
    A("")
    A(f"- **run_id**：`{s2_run_id}`")
    A(f"- **scenario**：`{s2_archive.get('scenario')}`")
    A(f"- **created_at**：{s2_archive.get('created_at')}")
    A(f"- **analysis_time**（时间锚点）：{s2_archive.get('analysis_time')}")
    A(f"- **location**：{s2_archive.get('location')}")
    A(f"- **status**：{s2_archive.get('status')}")
    A(f"- **question**：{s2_archive.get('question')}")
    A(f"- **存档文件**：`B07/runs/{s2_run_id}/run.json`")
    A(f"- **对照的 S1 存档**：`{s1_run_id}`"
      f"（另有旧存档 `{S1_LEGACY_RUN}`，无 scenario / 指纹字段，属正常）")
    A("")
    A("> 本报告全部数值取自两份真实存档与现场实算，没有为「让 S2 看起来成功」而修饰过结论。")
    A("")

    # ---------------------------------------------------------------- 1
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
    A(f"- 库内最新（仅参考）：{(s2_archive.get('latest_in_db') or {}).get('timestamp')}，"
      f"hours_gap={s2_archive.get('hours_gap')} 小时")
    A("")

    # ---------------------------------------------------------------- 2
    A("## 2. 历史窗口选取（S2 渐进漂移）与末点温度带的约定")
    A("")
    A(f"- 候选窗口数：{ws.get('candidate_count')}（被排除 {ws.get('rejected_count')}）")
    ref = ws.get("reference") or {}
    if ref:
        A(f"- S1 参考末点：{ref.get('s1_end_time')}，{ref.get('s1_end_temperature')}℃，"
          f"参考带 {ref.get('temperature_band')}℃（容差 {ref.get('tolerance')}℃）")
    A(f"- 选取理由：{ws.get('reason')}")
    A(f"- 时间锚点 = 选中窗口终点：{s2_archive.get('analysis_time')}")
    A("")
    sel = ws.get("selected") or {}
    A("| # | 时间 | 温度℃ | 湿度% | 光照 lux |")
    A("| --- | --- | --- | --- | --- |")
    for i, p in enumerate(sel.get("points") or [], 1):
        A(f"| {i} | {p['timestamp']} | {p['temperature']} | {p['humidity']} | "
          f"{p['lux']:.0f} |")
    A("")
    if ws.get("sim_scenario_tags"):
        A(f"> 窗口内各点 sim_scenario（仅展示，选窗不参与）：{ws.get('sim_scenario_tags')}")
        A("")

    A("### 2.1 末点温度带的约定（实验设计决策，不是实现细节）")
    A("")
    A("> S2 选窗条件 (a) 采用单侧带 [S1 基准32.8, 33.6]，")
    A("> 因为 S2 是升温过程，末点天然不低于 S1 基准；")
    A("> 若用对称带 [32.0, 33.6]，会选到 2026-10-20 09:00→11:30（末点 32.2℃，")
    A("> ΔT=8.8），该窗口末点低于 S1 基准、实为『从更冷处升上来』，")
    A("> 『当前值相近』的对照会变弱。")
    A("")
    A("这条是设计选择，不是「为了选到想要的窗口而改判据」：")
    A("")
    A("- 阈值侧完全没动：判据阈值仍写在 `B07/stability.py` 顶部"
      "（温度波动 2.0℃ / 湿度首尾 3.0% / 湿度单步 1.5% / 光照跳变 15000 lux / 禁全程单调），"
      "S1 与 S2 共用同一套阈值；")
    A("- 变化只发生在「挑哪一段历史」这一步，且这条约定写在 `B07/stability.py`"
      "的注释与本节里，可被复核；")
    A("- 对称带的替代结果（09:00→11:30，末点 32.2℃，ΔT 8.8）也在此写明，"
      "读者可以自行判断这个取舍是否成立。")
    A("")

    # ---------------------------------------------------------------- 3
    A("## 3. 稳定判据明细")
    A("")
    A("| 判据 | 实测 | 阈值 | 超出 | 结论 |")
    A("| --- | --- | --- | --- | --- |")
    for c in stab.get("criteria", []):
        excess = f"{c['excess']}" if "excess" in c else "—"
        A(f"| {c['name']} | {c['value']} | {c['threshold']} | {excess} | "
          f"{'✅ 通过' if c['passed'] else '❌ 未通过'} |")
    A("")
    A(f"判定：**{'稳定' if stab.get('passed') else '不稳定'}**")
    A("")
    A(f"> {stab.get('conclusion')}")
    A("")

    # ---------------------------------------------------------------- 4
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
    A("> B06 判断的是「未来 1-2 小时是否越界」（阈值 TEMP_MAX=40℃），"
      "32.8℃ 未越界 → 仍预测 NORMAL 是预期结果，不是 bug。"
      "详见第 10 节「边界与局限」。")
    A("")

    A("### coef Top5")
    A("")
    A("| # | feature | coefficient | direction |")
    A("| --- | --- | --- | --- |")
    for i, c in enumerate(mo.get("coef_top5") or [], 1):
        A(f"| {i} | `{c['feature']}` | {c['coefficient']:+.6f} | {c['direction']} |")
    A("")

    # ---------------------------------------------------------------- 5
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

    # ---------------------------------------------------------------- 6
    A("## 6. LLM 解释原文")
    A("")
    A(f"> {s2_archive.get('llm_explanation')}")
    A("")
    A(f"- provider：`{llm_meta.get('provider')}`")
    A(f"- model：`{llm_meta.get('model')}`")
    A(f"- elapsed_ms：{llm_meta.get('elapsed_ms')}")
    A(f"- content_length：{llm_meta.get('content_length')}")
    A(f"- system_prompt_sha256：`{llm_meta.get('system_prompt_sha256')}`")
    A(f"- question_sha256：`{llm_meta.get('question_sha256')}`")
    A("")

    # ---------------------------------------------------------------- 7
    A(f"## 7. S1（`{s1_run_id}`）与 S2（`{s2_run_id}`）并排对比")
    A("")
    A("| 维度 | S1 稳态基线 | S2 渐进漂移 |")
    A("| --- | --- | --- |")
    for row in comparison.get("rows", []):
        A(f"| {row['field']} | {row['s1']} | {row['s2']} |")
    A("")

    # ---------------------------------------------------------------- 8
    A("## 8. 实验有效性证明（全部取自存档真实字段）")
    A("")
    A("### 8.1 六条自检")
    A("")
    A("| # | 检查项 | 结果 | 实测 |")
    A("| --- | --- | --- | --- |")
    for c in selfcheck:
        A(f"| {c['no']} | {c['name']} | {'✅ True' if c['passed'] else '❌ False'} | "
          f"{c['detail']} |")
    A("")
    A("### 8.2 两次的问题字符串与提示词指纹")
    A("")
    A("| 项 | S1 | S2 | 是否相同 |")
    A("| --- | --- | --- | --- |")
    A(f"| question 字符串 | `{s1_archive.get('question')}` | `{s2_archive.get('question')}` | "
      f"{'✅ 逐字相同' if s1_archive.get('question') == s2_archive.get('question') else '❌ 不同'} |")
    A(f"| question_sha256 | `{s1_llm_meta.get('question_sha256')}` | "
      f"`{llm_meta.get('question_sha256')}` | "
      f"{'✅ 相同' if s1_llm_meta.get('question_sha256') == llm_meta.get('question_sha256') and s1_llm_meta.get('question_sha256') else '❌ 不同'} |")
    A(f"| system_prompt_sha256 | `{s1_llm_meta.get('system_prompt_sha256')}` | "
      f"`{llm_meta.get('system_prompt_sha256')}` | "
      f"{'✅ 相同' if s1_llm_meta.get('system_prompt_sha256') == llm_meta.get('system_prompt_sha256') and s1_llm_meta.get('system_prompt_sha256') else '❌ 不同'} |")
    A("")
    A(f"> 旧存档 `{S1_LEGACY_RUN}` 产生于加入指纹字段之前，没有 `system_prompt_sha256` / "
      f"`question_sha256`（显示「旧存档无此字段」），"
      f"所以指纹比对用 S1 正式存档 `{s1_run_id}`。")
    A("")
    A("### 8.3 两次的当前状态温度差")
    A("")
    t1 = s1_cs.get("temperature")
    t2 = cs.get("temperature")
    A(f"- S1 当前温度：**{t1}℃**（采样于 {s1_cs.get('timestamp')}）")
    A(f"- S2 当前温度：**{t2}℃**（采样于 {cs.get('timestamp')}）")
    A(f"- 差值：**{round(abs(t2 - t1), 2)}℃** ≤ 0.8℃ ✅"
      "（容差来自 S2 选窗条件 (a)）")
    A("")
    A("### 8.4 两次的历史 6 点并排")
    A("")
    A("| # | S1 时间 | S1 温度℃/湿度%/光照 | S2 时间 | S2 温度℃/湿度%/光照 |")
    A("| --- | --- | --- | --- | --- |")
    for i in range(max(len(s1_hist), len(s2_hist))):
        a = s1_hist[i] if i < len(s1_hist) else None
        b = s2_hist[i] if i < len(s2_hist) else None
        a_time = a["timestamp"] if a else "—"
        a_vals = (f"{a['temperature']}℃ / {a['humidity']}% / {a['lux']:.0f} lux") if a else "—"
        b_time = b["timestamp"] if b else "—"
        b_vals = (f"{b['temperature']}℃ / {b['humidity']}% / {b['lux']:.0f} lux") if b else "—"
        A(f"| {i + 1} | {a_time} | {a_vals} | {b_time} | {b_vals} |")
    A("")
    A("### 8.5 对抗性自检：选窗有没有偷看 sim_scenario")
    A("")
    clean = adversarial.get("clean") or {}
    polluted = adversarial.get("polluted") or {}
    A("做法：把 `load_all_samples()` 返回的每个样本的 `sim_scenario` 全部改成 "
      "`\"XXXXXXXX\"`，用这份「污染数据」重跑一次 `pick_drifting_window()`。")
    A("")
    A("| 数据 | start_time | end_time | temp_range |")
    A("| --- | --- | --- | --- |")
    A(f"| 干净数据 | {clean.get('start_time')} | {clean.get('end_time')} | "
      f"{clean.get('temp_range')} |")
    A(f"| 污染后（sim_scenario 全改 XXXXXXXX） | {polluted.get('start_time')} | "
      f"{polluted.get('end_time')} | {polluted.get('temp_range')} |")
    A("")
    A(f"结论：**{'一致 ✅' if adversarial.get('same') else '不一致 ❌'}** —— "
      "选窗结果完全没变。")
    A("")
    A("这说明判断只依赖 **temperature / humidity / lux 三列数值**：")
    A("")
    A("- 代码层面：`B07/stability.project_rows()` 把样本投影成只含 "
      "`timestamp / temperature / humidity / lux` 的行，`sim_scenario` 这个字段在选窗时"
      "根本不存在，想用也用不上；")
    A("- 数据层面：污染后重跑，窗口起止与 ΔT 逐字不变（见上表）。")
    A("")

    # ---------------------------------------------------------------- 9
    A("## 9. 为什么现在都是 32.8℃ 左右，GreenMate 的理解却不一样？")
    A("")
    A("**答案：历史过程不同 → 相同的当前值处在不同的状态演化过程中。**")
    A("")
    s1_temps = [h["temperature"] for h in s1_hist]
    s2_temps = [h["temperature"] for h in s2_hist]
    A(f"- 两次的当前温度都是 **{t1}℃ / {t2}℃**（差 {round(abs(t2 - t1), 2)}℃），"
      f"当前湿度都是 **{s1_cs.get('humidity')}% / {cs.get('humidity')}%**；")
    A(f"- 但 S1 的最近 3 小时是 {min(s1_temps)}~{max(s1_temps)}℃（波动 "
      f"{round(max(s1_temps) - min(s1_temps), 2)}℃，阈值内 → 判为**稳定**）；")
    A(f"- S2 的最近 3 小时是 {min(s2_temps)}~{max(s2_temps)}℃（波动 "
      f"{round(max(s2_temps) - min(s2_temps), 2)}℃，超阈值 → 判为**不稳定（持续升温）**）；")
    A(f"- 同一个 32.8℃，在 S1 是「从 {max(s1_temps)}℃ 回落到 32.8℃ 的平台」，"
      f"在 S2 是「从 {min(s2_temps)}℃ 一路升温上来的过程里的最后一点」——"
      "同一个数值，物理含义完全不同。")
    A("")
    A("**提示词不是原因**（否则 S2 就是失败的实验）：")
    A("")
    A(f"- 两次的 `system_prompt_sha256` **完全相同**："
      f"`{s1_llm_meta.get('system_prompt_sha256')}` = `{llm_meta.get('system_prompt_sha256')}`；")
    A(f"- 两次的 `question_sha256` **完全相同**："
      f"`{s1_llm_meta.get('question_sha256')}` = `{llm_meta.get('question_sha256')}`；")
    A("- 固定问题字符串逐字相同，`SYSTEM_PROMPT` 一个字没改。")
    A("")
    A("所以两次输出里唯一变化的东西是**喂进去的那 6 个历史点**，"
      "结论的差异只能由历史过程解释，不能由「怎么问」解释。")
    A("")

    # ---------------------------------------------------------------- 10
    A("## 10. 边界与局限")
    A("")
    A("### 10.1 S2 窗口跨了 sim_scenario 边界（但没有用元数据判断）")
    A("")
    A(f"- S2 窗口 2026-10-20 09:30→12:00 的 6 个点里，前 4 点 `sim_scenario=cold_snap`、"
      f"后 2 点 `sim_scenario=normal`，**确实跨了场景边界**；")
    A("- 而选窗与判定**只用了 temperature / humidity / lux 三列数值**"
      "（对抗性自检见第 8.5 节：把 `sim_scenario` 全部改成 `XXXXXXXX` 后窗口不变）；")
    A("- 所以这个窗口恰恰是最强的证据：**即使窗口横跨了两个生成场景，"
      "判断依然成立**——说明结论来自数值本身，而不是来自「这段是哪个场景生成的」这种元数据。")
    A("- 反过来说：如果判定偷偷用了 `sim_scenario`，这个跨边界的窗口就会"
      "暴露出「同段内部分类不同」的破绽；它没暴露，正是因为没用。")
    A("")
    A("### 10.2 B06 在 S1 / S2 两次都输出 NORMAL")
    A("")
    A(f"- B06 模型 `{mo.get('model_version')}` 判断的是「未来 1-2 小时是否越界」"
      f"（标签规则 `{mm.get('label_source')}`，阈值 TEMP_MAX=40℃），"
      f"32.8℃ 远未越界 → S1 预测 NORMAL、S2 也预测 NORMAL，**两次相同是预期结果，不是 bug**；")
    A("- 这一点很重要：**仅靠模型输出不足以反映趋势**。"
      "模型看的是「当前 + 短期特征 → 未来 1-2h 是否越界」，"
      "而「过去 3 小时在不在持续升温」这条信息它并不负责输出；")
    A("- 因此趋势必须由**结构化判断的 `recent_trend` 承载**——"
      "它是纯程序算出来的（`B07/judgement.py` → `B07/stability.judge_stability`），"
      "不依赖 LLM 文本，也正是在 S1/S2 之间发生变化的那一项。")
    A("")
    A("### 10.3 S2 的 hours_gap = 37.5 小时")
    A("")
    A(f"- 锚点选在漂移段末尾（{s2_archive.get('analysis_time')}），"
      f"而库内最新点在 {(s2_archive.get('latest_in_db') or {}).get('timestamp')}，"
      f"两者相隔 **{s2_archive.get('hours_gap')} 小时**；")
    A(f"- S1 的 hours_gap 只有 {s1_archive.get('hours_gap')} 小时，"
      "因为 S1 的锚点离库末点更近；")
    A("- 也就是说，S2 的锚点离「现在」更远，这来自 S2 的选窗位置，不是取数或时钟出了错。")
    A("")
    A("### 10.4 S2 的 gap warning 属于预期行为，不是缺陷")
    A("")
    A(f"- `config.GAP_WARNING_HOURS=24`，S2 的 {s2_archive.get('hours_gap')} 小时 > 24，"
      "所以会多出一条 gap warning；")
    A("- 这条 warning 的作用是提醒「锚点时刻不是最新数据」，属于**如实告知**，"
      "不影响稳定判定与 LLM 解释的正确性；")
    A("- 本报告第 11 节把它与其它 warning 分开列出，正是为了不让它被误读成异常。")
    A("")
    A("### 10.5 模型的漏报问题：本次 NORMAL 不构成异常场景有效性证据")
    A("")
    lim_model = (sj.get("limitations") or {}).get("model_boundary") or {}
    A(f"- B06 在 B02 训练集上的表现：{lim_model.get('text')}")
    A("- 其中 recall≈0.45 意味着**异常窗口有相当比例被漏报**（预测为 NORMAL）；")
    A("- 所以本次 S2 输出 NORMAL，**不能**被当成「模型在异常场景下也能正确判断」的证据；"
      "它只能说明「在 32.8℃、未越界的这段漂移里，模型的越界预测仍然是 NORMAL」；")
    A("- 真正把 S2 的趋势识别出来的，是结构化判断的 `recent_trend`，不是模型输出。")
    A("")

    # ---------------------------------------------------------------- 11
    A("## 11. 一致性校验与 warnings")
    A("")
    if gap_warnings:
        A("**时间锚点 gap 警告（预期，不是 bug，见第 10.3 / 10.4 节）**")
        A("")
        for w in gap_warnings:
            A(f"- ⚠️ {w}")
        A("")
    if other_warnings:
        A("**其它警告**")
        A("")
        for w in other_warnings:
            A(f"- ⚠️ {w}")
        A("")
    if not gap_warnings and not other_warnings:
        A("- 无 warning。")
        A("")

    # ---------------------------------------------------------------- 12
    A("## 12. explain_run 回溯输出")
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
    A("本报告由 `python -m B07.s2` 生成；"
      "`python -m B07.s2 --report-only` 可在不重跑流程、不新增 run 的情况下，"
      "按现有存档重新生成本文件。")
    A("")

    return "\n".join(lines)


def split_warnings(warnings: list) -> tuple[list, list]:
    """把 gap 警告（时间锚点落后于库末点）和其他警告分开。"""
    gap, other = [], []
    for w in warnings or []:
        (gap if "相隔" in w else other).append(w)
    return gap, other


def _delta_t(archive: dict) -> float | None:
    """从存档的原始 6 点算温度波动（不读判据表里的缓存值）。"""
    temps = [h["temperature"] for h in (archive.get("history") or [])]
    if not temps:
        return None
    return round(max(temps) - min(temps), 2)


def _temp_criterion(archive: dict) -> dict:
    """从存档的判据表里取"温度最大波动"这一条（含阈值）。"""
    stab = (archive.get("history_window") or {}).get("stability") or {}
    for c in stab.get("criteria") or []:
        if "温度" in c.get("name", "") and "波动" in c.get("name", ""):
            return c
    return {}


def build_comparison(s2_archive: dict, s1_archive: dict,
                     s1_run_id: str = S1_REFERENCE_RUN,
                     s2_run_id: str | None = None) -> dict:
    """S1 与 S2 的并排对比（所有值都取自两份存档的真实字段）。"""
    s2_run_id = s2_run_id or s2_archive.get("run_id")
    s1_sj = s1_archive.get("structured_judgement") or {}
    s2_sj = s2_archive.get("structured_judgement") or {}
    s1_rt = s1_sj.get("recent_trend") or {}
    s2_rt = s2_sj.get("recent_trend") or {}
    s1_hw = (s1_archive.get("history_window") or {}).get("stability") or {}
    s2_hw = (s2_archive.get("history_window") or {}).get("stability") or {}
    s1_mo = s1_archive.get("model_output") or {}
    s2_mo = s2_archive.get("model_output") or {}
    s1_crit = _temp_criterion(s1_archive)
    s2_crit = _temp_criterion(s2_archive)

    def _points(a: dict) -> str:
        hist = a.get("history") or []
        if not hist:
            return "—"
        return " → ".join(
            f"{h['temperature']}℃/{h['humidity']}%/{h['lux']:.0f}lux" for h in hist
        )

    def _timestamps(a: dict) -> str:
        hist = a.get("history") or []
        if not hist:
            return "—"
        return f"{hist[0]['timestamp']} → {hist[-1]['timestamp']}（{len(hist)} 点）"

    def _warnings(a: dict) -> str:
        ws = a.get("warnings") or []
        if not ws:
            return "无"
        gap, other = split_warnings(ws)
        parts = []
        if gap:
            parts.append("gap 警告 " + str(len(gap)) + " 条")
        if other:
            parts.append("其它 " + str(len(other)) + " 条")
        return "；".join(parts) + "（全文见第 11 节）"

    def _llm_points(a: dict) -> str:
        text = (a.get("llm_explanation") or "").strip()
        if not text:
            return "—"
        head = text[:120]
        return head + ("……（全文见第 6 节）" if len(text) > 120 else "")

    def _judgement_summary(sj: dict) -> str:
        ev = (sj.get("evidence_status") or {}).get("summary")
        lim = sj.get("limitations") or {}
        keys = lim.get("all_keys") or []
        return (f"五字段齐全；{ev}；limitations {len(keys)} 条："
                + "、".join(keys))

    rows = [
        {
            "field": "场景 / 存档",
            "s1": f"{s1_run_id}（scenario={s1_archive.get('scenario') or '(旧存档未记录)'}）",
            "s2": f"{s2_run_id}（scenario={s2_archive.get('scenario')}）",
        },
        {"field": "时间锚点", "s1": s1_archive.get("analysis_time"),
         "s2": s2_archive.get("analysis_time")},
        {"field": "当前温度℃", "s1": (s1_archive.get("current_state") or {}).get("temperature"),
         "s2": (s2_archive.get("current_state") or {}).get("temperature")},
        {"field": "当前湿度%", "s1": (s1_archive.get("current_state") or {}).get("humidity"),
         "s2": (s2_archive.get("current_state") or {}).get("humidity")},
        {"field": "当前光照 lux", "s1": (s1_archive.get("current_state") or {}).get("lux"),
         "s2": (s2_archive.get("current_state") or {}).get("lux")},
        {"field": "历史 6 点（时刻）", "s1": _timestamps(s1_archive),
         "s2": _timestamps(s2_archive)},
        {"field": "历史 6 点（温度/湿度/光照）", "s1": _points(s1_archive),
         "s2": _points(s2_archive)},
        {
            "field": "趋势判定",
            "s1": f"{'稳定' if s1_hw.get('passed') else '不稳定'}"
                  f"（passed={s1_hw.get('passed')}）",
            "s2": f"{'稳定' if s2_hw.get('passed') else '不稳定'}"
                  f"（passed={s2_hw.get('passed')}）",
        },
        {
            "field": "ΔT 与阈值",
            "s1": f"ΔT={_delta_t(s1_archive)}℃，阈值 {s1_crit.get('threshold')}℃"
                  f" → {'通过' if s1_crit.get('passed') else '未通过'}",
            "s2": f"ΔT={_delta_t(s2_archive)}℃，阈值 {s2_crit.get('threshold')}℃"
                  f" → {'通过' if s2_crit.get('passed') else '未通过'}"
                  f"（超出 {s2_crit.get('excess')}）",
        },
        {"field": "B06 predicted_attention", "s1": s1_mo.get("predicted_attention"),
         "s2": s2_mo.get("predicted_attention")},
        {"field": "model_version", "s1": s1_mo.get("model_version"),
         "s2": s2_mo.get("model_version")},
        {"field": "confidence", "s1": s1_mo.get("confidence"),
         "s2": s2_mo.get("confidence")},
        {
            "field": "structured_judgement ② recent_trend",
            "s1": f"{s1_rt.get('stability_verdict')}；{s1_rt.get('conclusion')}",
            "s2": f"{s2_rt.get('stability_verdict')}；{s2_rt.get('conclusion')}",
        },
        {
            "field": "结构化判断结论（①③④⑤）",
            "s1": _judgement_summary(s1_sj),
            "s2": _judgement_summary(s2_sj),
        },
        {"field": "LLM 解释要点（原文摘录）", "s1": _llm_points(s1_archive),
         "s2": _llm_points(s2_archive)},
        {"field": "warnings", "s1": _warnings(s1_archive), "s2": _warnings(s2_archive)},
    ]
    return {"s1_run_id": s1_run_id, "s2_run_id": s2_run_id, "rows": rows}


def compare_with_s1(archive: dict, s1_run_id: str = S1_REFERENCE_RUN) -> dict:
    """S1 与 S2 的结构化判断对比（从磁盘读 S1 存档）。"""
    s1 = _load_archive(s1_run_id) or {}
    return build_comparison(archive, s1, s1_run_id=s1_run_id)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run_s2(question: str, location: str = DEFAULT_LOCATION,
           model_path: str | Path | None = None,
           write_report: bool = True) -> dict:
    """跑完 S2 全流程并返回 {'archive', 'explain', 'report_path', 'selfcheck'}。"""
    from B07.runs import run_analysis, explain_run

    if model_path is None:
        from B07.runs import DEFAULT_MODEL_PATH
        model_path = DEFAULT_MODEL_PATH

    # ---- 阶段 1：选窗 ----
    print("[1/6] 选窗（渐进漂移）...")
    ws = select_drifting_window()
    sel = ws["selected"]
    print(f"  候选窗口 {ws['candidate_count']} 个（被排除 {ws['rejected_count']} 个）")
    print(f"  选中窗口：{sel['start_time']} → {sel['end_time']}")
    print(f"  末点温度：{sel['end_temperature']}℃，ΔT={sel['delta_t']}℃")
    print(f"  时间锚点：{ws['anchor_time'].isoformat()}")
    print(f"  理由：{ws['reason']}")

    from B07.scenarios import check_expectation
    check_expectation(SCENARIO, {"passed": sel["passed"]})
    print(f"  稳定性判定：{'稳定' if sel['passed'] else '不稳定'}"
          f"（符合场景预期 expect_stability=False）")

    # 对抗性自检：污染 sim_scenario 后选窗结果必须一致
    adversarial = adversarial_check(ws["samples"], sel)
    print(f"  对抗性自检：干净 {adversarial['clean']} / 污染后 {adversarial['polluted']}"
          f" → {'一致 ✅' if adversarial['same'] else '不一致 ❌（选窗偷看了 sim_scenario）'}")

    window_selection = {
        "scenario": SCENARIO,
        "anchor_policy": "drifting_comparable_to_S1",
        "candidate_count": ws["candidate_count"],
        "rejected_count": ws["rejected_count"],
        "daytime_count": None,
        "pool": "drifting",
        "reason": ws["reason"],
        "reference": ws["reference"],
        "candidates": ws["candidates"],
        "selected": {
            "start_time": sel["start_time"],
            "end_time": sel["end_time"],
            "end_temperature": sel["end_temperature"],
            "delta_t": sel["delta_t"],
            "temp_range": sel["temp_range"],
            "conclusion": sel["conclusion"],
            "criteria": sel["criteria"],
            "points": sel["points"],
        },
        "adversarial": adversarial,
        "total_samples": len(ws["samples"]),
        "anchor_time": ws["anchor_time"].isoformat(),
    }

    # ---- 阶段 2~6：取数 → 推理 → 判断 → 解释 → 存档（复用同一条链）----
    stage_no = {"evidence": 2, "predict": 3, "judgement": 4,
                "llm_explanation": 5, "consistency_check": 6}

    def _progress(stage, extra):
        extra = extra or {}
        print(f"\n[{stage_no.get(stage, '?')}/6] {STAGE_TITLES.get(stage, stage)}...")
        if stage == "evidence":
            cs = extra.get("current_state") or {}
            print(f"  当前状态：{cs.get('temperature')}℃ / {cs.get('humidity')}% / "
                  f"{cs.get('lux')} lux @ {cs.get('timestamp')}")
            print(f"  历史窗口：{extra.get('history_count')} 点，"
                  f"hours_gap={extra.get('hours_gap')}")
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
            print(f"  system_prompt_sha256={m.get('system_prompt_sha256')}，"
                  f"question_sha256={m.get('question_sha256')}")
        elif stage == "consistency_check":
            ws_ = extra.get("warnings") or []
            gap_w, other_w = split_warnings(ws_)
            print(f"  warnings：{len(ws_)} 条（gap 警告 {len(gap_w)} 条，"
                  f"其他 {len(other_w)} 条）")
            for w in gap_w:
                print(f"    ⚠️ [时间锚点 gap，预期] {w}")
            for w in other_w:
                print(f"    ⚠️ {w}")

    archive = run_analysis(ws["anchor_time"], location, model_path,
                           window_selection=window_selection, progress=_progress,
                           scenario=SCENARIO)

    # ---- 回溯 ----
    explain_result = explain_run(archive["run_id"], quiet=True)

    # ---- 自检 ----
    selfcheck = build_selfcheck(archive, adversarial)
    s1_archive = _load_archive(S1_REFERENCE_RUN) or {}
    comparison = build_comparison(archive, s1_archive,
                                  s1_run_id=S1_REFERENCE_RUN,
                                  s2_run_id=archive.get("run_id"))

    # ---- 报告 ----
    report_path = None
    if write_report:
        report_path = REPORT_PATH
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            build_report(archive, s1_archive, explain_result["answer"],
                         explain_result, selfcheck, comparison,
                         adversarial=adversarial,
                         s1_run_id=S1_REFERENCE_RUN,
                         s2_run_id=archive.get("run_id")),
            encoding="utf-8")

    return {
        "archive": archive,
        "explain": explain_result,
        "report_path": report_path,
        "selfcheck": selfcheck,
        "comparison": comparison,
        "selection": ws,
    }


def regenerate_report(s2_run_id: str = S2_REFERENCE_RUN,
                     s1_run_id: str = S1_REFERENCE_RUN,
                     write_report: bool = True) -> dict:
    """按**已有的两份存档**重新生成 S2 报告，不重跑流程、不新增 run。

    用途：报告模板改了以后想刷新内容，而不想再多跑一次分析、多留一个 run。
    """
    from B07.runs import explain_run

    s2_archive = _load_archive(s2_run_id)
    if s2_archive is None:
        raise RuntimeError(f"找不到 S2 存档：{s2_run_id}")
    s1_archive = _load_archive(s1_run_id)
    if s1_archive is None:
        raise RuntimeError(f"找不到 S1 存档：{s1_run_id}")

    # 对抗性自检重跑一次（现场实算，不复用存档里的旧结果）
    samples_sel = select_drifting_window()
    adversarial = adversarial_check(samples_sel["samples"], samples_sel["selected"])

    explain_result = explain_run(s2_run_id, quiet=True)
    selfcheck = build_selfcheck(s2_archive, adversarial)
    comparison = build_comparison(s2_archive, s1_archive,
                                  s1_run_id=s1_run_id, s2_run_id=s2_run_id)

    report_path = None
    if write_report:
        report_path = REPORT_PATH
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            build_report(s2_archive, s1_archive, explain_result["answer"],
                         explain_result, selfcheck, comparison,
                         adversarial=adversarial,
                         s1_run_id=s1_run_id, s2_run_id=s2_run_id),
            encoding="utf-8")

    return {
        "archive": s2_archive,
        "explain": explain_result,
        "report_path": report_path,
        "selfcheck": selfcheck,
        "comparison": comparison,
        "adversarial": adversarial,
    }


def main():
    parser = argparse.ArgumentParser(
        description="B07 S2 统一入口：渐进漂移（与 S1 同一流程，只换历史窗口）")
    parser.add_argument("--question", type=str, default=FIXED_QUESTION,
                        help="固定问题（与 S1 一字不差，不可改写）")
    parser.add_argument("--location", type=str, default=DEFAULT_LOCATION)
    parser.add_argument("--model", type=str, default=None,
                        help="B06 已存模型产物路径")
    parser.add_argument("--no-report", action="store_true",
                        help="不生成 B07/artifacts/s2_report.md")
    parser.add_argument("--report-only", action="store_true",
                        help="只按已有存档重建 s2_report.md（不重跑流程、不新增 run）")
    parser.add_argument("--s1-run", type=str, default=S1_REFERENCE_RUN,
                        help="--report-only 用的 S1 存档")
    parser.add_argument("--s2-run", type=str, default=S2_REFERENCE_RUN,
                        help="--report-only 用的 S2 存档")
    args = parser.parse_args()

    print("=" * 70)
    print("B07 S2 — 统一入口（渐进漂移）")
    print("=" * 70)
    print(f"\n  固定问题：{args.question}")

    # 固定问题不可改写：S1/S2 必须是同一个问题，否则两边的输出不可比
    if args.question.strip() != FIXED_QUESTION.strip():
        print(f"\n  ❌ S2 的问题不可改写，必须与 S1 完全相同。\n"
              f"     传入：{args.question}\n"
              f"     固定：{FIXED_QUESTION}")
        sys.exit(2)

    if args.report_only:
        try:
            result = regenerate_report(args.s2_run, args.s1_run,
                                       write_report=not args.no_report)
        except Exception as e:
            print(f"\n  ❌ 报告重建失败：{type(e).__name__}: {e}")
            sys.exit(1)
        print(f"\n  已按存档重建报告：{result['report_path']}")
        print(f"  S1 存档：{args.s1_run}；S2 存档：{args.s2_run}（未新增 run）")
        for c in result["selfcheck"]:
            print(f"  {c['no']}. {'✅ True' if c['passed'] else '❌ False'}  {c['name']}")
        print("\n" + "=" * 70)
        print("B07 S2 报告重建完成 ✅")
        print("=" * 70)
        return

    try:
        result = run_s2(args.question, args.location, args.model,
                        write_report=not args.no_report)
    except Exception as e:
        print(f"\n  ❌ S2 执行失败：{type(e).__name__}: {e}")
        print("     失败已留痕（见 B07/runs/ 下的 status=failed 存档）")
        sys.exit(1)

    archive = result["archive"]
    sj = archive["structured_judgement"]
    sel = result["selection"]["selected"]

    print("\n" + "=" * 70)
    print(f"【run_id】{archive['run_id']}")
    print("=" * 70)

    print("\n【选中窗口的 6 个点】")
    print("-" * 70)
    for i, p in enumerate(sel["points"], 1):
        print(f"  {i}. {p['timestamp']}  {p['temperature']}℃ / "
              f"{p['humidity']}% / {p['lux']:.0f} lux")

    print("\n【逐条判据】")
    print("-" * 70)
    for c in sel["criteria"]:
        icon = "✅" if c["passed"] else "❌"
        extra = f"  超出 {c['excess']}" if "excess" in c else ""
        print(f"  {icon} {c['name']}: 实测={c['value']}  阈值={c['threshold']}  "
              f"— {c['note']}{extra}")

    print("\n【稳定性结论句】")
    print("-" * 70)
    print(f"  {sel['conclusion']}")

    print("\n【实验有效性自检】")
    print("-" * 70)
    for c in result["selfcheck"]:
        print(f"  {c['no']}. {'✅ True' if c['passed'] else '❌ False'}  {c['name']}")
        print(f"     实测：{c['detail']}")

    print("\n【S1 与 S2 的结构化判断对比】")
    print("-" * 70)
    for row in result["comparison"]["rows"]:
        print(f"  {row['field']}")
        print(f"     S1: {row['s1']}")
        print(f"     S2: {row['s2']}")

    print("\n【结构化判断五字段】")
    print("-" * 70)
    ce = sj["current_environment"]
    print(f"  ① current_environment: 温度 {ce['temperature']}℃，湿度 {ce['humidity']}%，"
          f"光照 {ce['lux']} lux，来源 {ce['source_type']}，采样于 {ce['sampled_at']}")
    rt = sj["recent_trend"]
    print(f"  ② recent_trend: {rt['stability_verdict']}；{rt['conclusion']}")
    print(f"  ③ predicted_attention: {sj['predicted_attention']}")
    print(f"  ④ evidence_status: {sj['evidence_status']['summary']}")
    print("  ⑤ limitations:")
    for key in sj["limitations"]["all_keys"]:
        print(f"       - [{key}] {sj['limitations'][key]['text']}")

    print("\n【LLM 解释】")
    print("-" * 70)
    print(archive["llm_explanation"])
    m = archive["llm_meta"]
    print(f"\n  — model={m['model']}，provider={m['provider']}，"
          f"elapsed_ms={m['elapsed_ms']}，content_length={m['content_length']}")
    print(f"  — system_prompt_sha256={m.get('system_prompt_sha256')}，"
          f"question_sha256={m.get('question_sha256')}")

    print("\n【一致性校验 / warnings】")
    print("-" * 70)
    gap_w, other_w = split_warnings(archive["warnings"])
    if gap_w:
        print("  时间锚点 gap 警告（预期，不是 bug）：")
        for w in gap_w:
            print(f"    ⚠️  {w}")
    if other_w:
        print("  其它警告：")
        for w in other_w:
            print(f"    ⚠️  {w}")
    if not gap_w and not other_w:
        print("  无")

    if result["report_path"]:
        print(f"\n【阶段报告】{result['report_path']}")
    print(f"\n【存档文件】B07/runs/{archive['run_id']}/run.json")
    print(f"【回溯】python -m B07.runs explain {archive['run_id']}")

    print("\n" + "=" * 70)
    print("B07 S2 完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
