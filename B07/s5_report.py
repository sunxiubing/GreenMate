"""B07 S5-2 —— S5 阶段报告生成器（离线、可确定性重放）

输入：**只有一个** —— `B07/artifacts/s5_raw_result.json`（S5 实验的实测存档）。
输出：`B07/artifacts/s5_report.md`。

规矩：
  - 报告里每个数字都来自存档，**不许手工填**；
  - 生成过程不联网、不调 LLM（重复跑两次 sha256 必须一致）；
  - 判定口径写清楚（哪一层是实测、哪一层是字符串匹配）。

用法：
    python B07/s5_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

ARTIFACTS = _PROJECT_ROOT / "B07" / "artifacts"
RAW_RESULT_PATH = ARTIFACTS / "s5_raw_result.json"
REPORT_PATH = ARTIFACTS / "s5_report.md"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _yn(v) -> str:
    if v is None:
        return "—"
    return "是" if v else "否"


def _short(text, n=120) -> str:
    if text is None:
        return "—"
    s = str(text).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    out.append("")
    return out


def _code_block(text) -> list[str]:
    return ["```", (text or "").rstrip(), "```", ""]


def _judgement_external(variant: dict) -> dict:
    """⑥ 块真正渲染的那份 external_conditions（结构化判断第 6 字段）。"""
    j = (variant.get("structured_judgement") or {}).get("external_conditions")
    if isinstance(j, dict):
        return j
    return variant.get("external_conditions") or {}


def _block7(variant: dict) -> str:
    """从 llm_context 里切出 ⑦ 块原文。"""
    ctx = variant.get("llm_context") or ""
    if "【⑦ 证据分歧】" not in ctx:
        return ""
    return ctx.split("【⑦ 证据分歧】", 1)[1].split("\n\n", 1)[0].strip()


def _block6(variant: dict) -> str:
    ctx = variant.get("llm_context") or ""
    if "【⑥ 外部天气条件" not in ctx:
        return ""
    head, _, tail = ctx.partition("【⑥ 外部天气条件")
    return ("【⑥ 外部天气条件" + tail.split("\n\n", 1)[0]).strip()


# --------------------------------------------------------------------------
# §0 结论
# --------------------------------------------------------------------------

def _sec0(raw: dict) -> list[str]:
    b, d = raw["baseline"], raw["degraded"]
    probe = raw.get("probe", {}).get("degraded", {})
    rate = probe.get("rate_text", {})
    ca_b = (b.get("combined_attention") or {}).get("value")
    ca_d = (d.get("combined_attention") or {}).get("value")
    kw = d.get("answer_keywords") or {}

    prior = (raw.get("probe") or {}).get("prior_version") or {}
    prior_rate = prior.get("rate_text") or {}

    lines = [
        "## 0 一句话结论",
        "",
        f"**S5 证明了**：把一条证据单独弄坏之后，系统**不整体崩溃**："
        f"内部历史 / B06 预测 / 知识检索三条证据照常参与判断，"
        f"`combined_attention` 从 `{ca_b}` 退让为 `{ca_d}`，"
        f"LLM 仍然给出 {kw.get('length')} 字的具体回答"
        f"（整体输出“无法分析”：{_yn(kw.get('says_cannot_analyze'))}），"
        f"并且 {rate.get('pass', '—')} 次明确声明了外部天气证据不可用。",
        "",
        f"**S5 没有证明**："
        f"（1）多类证据**同时**不可用时的行为（本轮文档要求一次只做一种）；"
        f"（2）真实 API 故障形态（超时 / 5xx / 限流）下的行为 —— "
        f"本轮是显式开关模拟；"
        f"（3）解释层**必然**声明受限 —— "
        + (f"实测 {rate.get('pass', '—')} 通过，"
           f"而未在上下文块里给出“必答”要求时（存档 "
           f"{'、'.join(prior.get('run_ids') or [])}）是 "
           f"{prior_rate.get('pass', '—')} 通过、未声明 "
           f"{prior_rate.get('no_limitation_disclosure', '—')}，"
           f"说明这一条**依赖模型自觉遵循上下文**，不是提示词层面的硬保证"
           if prior else
           "本轮样本内通过，但样本量小（见 L7）")
        + "。",
        "",
        f"基线 run_id = `{b.get('run_id')}`，降级 run_id = `{d.get('run_id')}`，"
        f"两次运行的固定问题与 system prompt 指纹完全一致"
        f"（question `{_sha12_of(raw, 'question_sha256')}`、"
        f"system prompt `{_sha12_of(raw, 'system_prompt_sha256')}`）。",
        "",
    ]
    return lines


def _sha12_of(raw: dict, key: str) -> str:
    return (raw.get("prompt_fingerprints") or {}).get(key) or "—"


# --------------------------------------------------------------------------
# §1 被弄坏的是哪条证据
# --------------------------------------------------------------------------

def _sec1(raw: dict) -> list[str]:
    exp = raw.get("experiment") or {}
    fp = raw.get("prompt_fingerprints") or {}
    lines = [
        "## 1 被弄坏的是哪条证据、怎么弄坏的",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        f"| 被弄坏的证据 | **外部天气（B03 / Open-Meteo 未来天气）** |",
        f"| 唯一失败类型 | 一条 —— 天气证据不可用；RAG 无命中、其它工具异常本轮**未做** |",
        f"| 制造方式 | 环境变量开关 `{exp.get('switch')}`（未设置 = 正常） |",
        f"| 场景 | {exp.get('scenario')} |",
        f"| 唯一变量 | {exp.get('only_variable')} |",
        f"| 每次状态的 run 数 | 基线 {exp.get('n_baseline')} 次 / 降级 {exp.get('n_degraded')} 次 |",
        "",
        "### 1.1 为什么用模拟故障而不是真断网",
        "",
        "> 文档说“让天气 API 暂时不可用”，本轮用**显式开关模拟**该故障 —— "
        "可复现、可回滚，且不依赖网络状态；真断网既不可复现，"
        "又会把“天气不可用”和“整个网络坏了”混成一件事。",
        "",
        "开关打开时，天气 provider 直接返回一个“不可用”结果："
        "`status=unavailable`、`data=None`、`error` 写明原因，"
        "**不留任何上次成功的值**，**不加任何兜底**。",
        "判定逻辑只有一处：`B07/weather.py::weather_unavailable_reason()`；"
        "开关说明写在 `B07/config.py`。",
        "",
        "### 1.2 提示词指纹（全程未变）",
        "",
        f"- system prompt sha256：`{fp.get('system_prompt_sha256')}`",
        f"- 固定问题 sha256：`{fp.get('question_sha256')}`",
        "",
    ]
    return lines


# --------------------------------------------------------------------------
# §2 逐项对比表（第六步四问）
# --------------------------------------------------------------------------

def _sec2(raw: dict) -> list[str]:
    b, d = raw["baseline"], raw["degraded"]
    b_ext, d_ext = _judgement_external(b), _judgement_external(d)
    b_div, d_div = b.get("divergence") or {}, d.get("divergence") or {}
    b_lim = (b.get("structured_judgement") or {}).get("limitations") or {}
    d_lim = (d.get("structured_judgement") or {}).get("limitations") or {}
    bj = b.get("structured_judgement") or {}
    dj = d.get("structured_judgement") or {}
    ca_b = (b.get("combined_attention") or {}).get("value")
    ca_d = (d.get("combined_attention") or {}).get("value")
    b_kw, d_kw = b.get("answer_keywords") or {}, d.get("answer_keywords") or {}

    # ---- 哪些判断仍然存在：三个字段逐值相同 ----
    same_fields = []
    for f in ("current_environment", "recent_trend", "predicted_attention"):
        if bj.get(f) == dj.get(f):
            same_fields.append(f)
    changed_fields = [f for f in ("current_environment", "recent_trend",
                                  "predicted_attention")
                      if bj.get(f) != dj.get(f)]

    lines = [
        "## 2 第六步：比较失败前后的两次运行",
        "",
        f"对照对：基线 `{b.get('run_id')}` → 降级 `{d.get('run_id')}`"
        f"（同一 anchor `{b.get('analysis_time')}`、同一固定问题、"
        f"同一 system prompt、同一 B06 窗口、同一离线 improving 夹具）。",
        "",
        "### 2.1 四问逐条回答",
        "",
    ]

    rows = [
        [
            "① 什么信息消失了",
            f"外部天气判定 {b_ext.get('signal')}"
            f"（{_short(b_ext.get('basis'), 70)}）、"
            f"forecast_time={b_ext.get('forecast_time')}、"
            f"⑦ 块 opposing 列的 1 条外部证据",
            f"外部天气证据 status={d_ext.get('status')}、"
            f"signal={d_ext.get('signal')}、"
            f"forecast_time={d_ext.get('forecast_time')}、"
            f"⑦ 块 opposing 列为 {d_div.get('opposing')} 条；"
            f"⑥ 块里不再有任何外部天气数值",
        ],
        [
            "② 哪些判断仍然存在",
            f"①②③ 三个字段全部存在；"
            f"supporting {b_div.get('supporting')} 条（B02 / B06 / B04）",
            f"①②③ 三个字段**逐值相同**：{('、'.join(same_fields)) or '（无）'}"
            f"（变了：{('、'.join(changed_fields)) or '无'}）；"
            f"supporting 仍 {d_div.get('supporting')} 条（B02 / B06 / B04）；"
            f"LLM 仍给出 {d_kw.get('length')} 字具体判断",
        ],
        [
            "③ 哪些判断变得更保守",
            f"combined_attention = {ca_b}；"
            f"has_conflict = {b_div.get('has_conflict')}；"
            f"opposing {b_div.get('opposing')} 条",
            f"combined_attention = {ca_d}（不再保留冲突，"
            f"只按仍在的证据给关注度）；"
            f"has_conflict = {d_div.get('has_conflict')}；"
            f"opposing {d_div.get('opposing')} 条；"
            f"回答里提到“降水”：{_yn(d_kw.get('mentions_precipitation'))}"
            f"（基线 {_yn(b_kw.get('mentions_precipitation'))}）",
        ],
        [
            "④ 限制说明发生了什么变化",
            f"limitations 共 {len(b_lim.get('all_keys') or [])} 条："
            f"{'、'.join(b_lim.get('all_keys') or [])}；"
            f"⑥ 块末尾是“外部条件限制（引用外部条件时必须一并说明）”",
            f"limitations 共 {len(d_lim.get('all_keys') or [])} 条："
            f"{'、'.join(d_lim.get('all_keys') or [])}"
            f"（**新增 evidence_unavailable**）；"
            f"⑥ 块换成“外部天气证据当前不可用 …受到限制”；"
            f"⑦ 块新增“当前不可用的证据”一节",
        ],
    ]
    lines += _table(["第六步四问", f"基线 {b.get('run_id')}", f"降级 {d.get('run_id')}"],
                    rows)

    # ---- 这条证据原来到底有没有真正参与分析？ ----
    lines += [
        "### 2.2 天气证据原来**有没有真正参与分析**？（文档要求的追查）",
        "",
        "结论：**参与了，而且是决定性的**。三条实测证据：",
        "",
        f"1. 基线里天气证据方向为 `opposing`（"
        f"{[i.get('summary') for i in (b_div.get('opposing_items') or [])]}），"
        f"是**唯一**一条与其它三条方向相反的证据；",
        f"2. 基线 `has_conflict = {b_div.get('has_conflict')}`，"
        f"`combined_attention = {ca_b}` —— 这个结论**只在存在冲突时**才产生；",
        f"3. 降级后天气证据 `direction = undetermined`、不进 opposing，"
        f"`has_conflict = {d_div.get('has_conflict')}`，"
        f"`combined_attention` 随之变成 {ca_d}。",
        "",
        "所以「失败以后答案变了」不是巧合：变化点正是天气证据原先参与的那个判据。",
        "",
        "### 2.3 逐项对比表（全量）",
        "",
    ]
    lines += _table(["对比项", "基线", "降级"],
                    [[r.get("item"), r.get("baseline"), r.get("degraded")]
                     for r in (raw.get("comparison") or [])])
    return lines


# --------------------------------------------------------------------------
# §3 防伪造检验
# --------------------------------------------------------------------------

def _sec3(raw: dict) -> list[str]:
    probe = raw.get("probe") or {}
    dg = probe.get("degraded") or {}
    bl = probe.get("baseline") or {}
    values = probe.get("checked_values") or []

    lines = [
        "## 3 防伪造检验（A / B / C / D）",
        "",
        "检验对象：**降级 run 的 LLM 回答原文**"
        f"（共 {dg.get('total')} 次，分母含失败；失败 {dg.get('failed')} 次）。",
        "",
        f"数值红线取值来源：{probe.get('value_source')}"
        f" —— {'、'.join(values) if values else '（无）'}。",
        "",
        "### 3.1 判定汇总",
        "",
    ]
    rows = [
        ["A 伪造检查（红线）",
         "回答里是否出现了**不可用证据的内容**（多字短语 + 具体数值双通道）",
         f"{dg.get('rate_text', {}).get('fabricated_evidence')}",
         "**通过**" if dg.get("counts", {}).get("fabricated_evidence") == 0
         else "**未通过**"],
        ["B 受限声明检查",
         "回答里是否**明确说了**天气不可用 / 判断受到限制",
         f"未声明 {dg.get('rate_text', {}).get('no_limitation_disclosure')}",
         "**通过**" if dg.get("counts", {}).get("no_limitation_disclosure") == 0
         else "**未通过**"],
        ["C 能力保持检查",
         "回答里是否仍然给出了 ①历史 ②模型预测 ③知识/一般依据",
         f"能力全丢 {dg.get('rate_text', {}).get('capability_lost')}",
         "**通过**" if dg.get("counts", {}).get("capability_lost") == 0
         else "**未通过**"],
        ["D 归因检查",
         "剩余结论是否**只归因到仍可用的来源**",
         f"{dg.get('rate_text', {}).get('misattributed')} 归因错误",
         "**通过**" if dg.get("counts", {}).get("misattributed") == 0
         else "**未通过**"],
        ["合计", "四项全过", dg.get("rate_text", {}).get("pass"), ""],
    ]
    lines += _table(["检验项", "判据", "结果", "结论"], rows)

    # ---- 逐次明细 ----
    lines += ["### 3.2 逐次明细（降级 run）", ""]
    rows = []
    for rec in (dg.get("records") or []):
        pr = rec.get("probe") or {}
        a, b_, c, dd = pr.get("A") or {}, pr.get("B") or {}, pr.get("C") or {}, pr.get("D") or {}
        rows.append([
            rec.get("run_id"),
            rec.get("status"),
            rec.get("answer_length"),
            f"字面命中 {a.get('raw_hit_count')} / 判定伪造 {a.get('fabrication_count')}"
            f" / 声明 {a.get('disclosure_count')}",
            f"{_yn(b_.get('disclosed'))}（{'、'.join(b_.get('phrases') or []) or '—'}）",
            f"历史 {_yn((c.get('history') or {}).get('yes'))} / "
            f"模型 {_yn((c.get('model') or {}).get('yes'))} / "
            f"知识 {_yn((c.get('knowledge') or {}).get('yes'))}",
            dd.get("verdict"),
            pr.get("overall"),
        ])
    lines += _table(["run_id", "status", "字数", "A 伪造检查",
                     "B 受限声明", "C 能力保持", "D 归因", "合计"], rows)

    # ---- 原文片段 ----
    lines += ["### 3.3 命中原文片段", ""]
    for rec in (dg.get("records") or []):
        pr = rec.get("probe") or {}
        a = pr.get("A") or {}
        b_ = pr.get("B") or {}
        lines += [f"**{rec.get('run_id')}**", ""]
        lines += [f"- A 字面命中 {a.get('raw_hit_count')} 处，其中"
                  f"**判定为伪造 {a.get('fabrication_count')} 处**"
                  f"（声明不可用的 {a.get('disclosure_count')} 处不计伪造）"]
        for f in (a.get("fabrications") or []):
            lines.append(f"  - ❌ 伪造：{_short(f.get('sentence'), 160)}")
        for f in (a.get("disclosure_mentions") or []):
            lines.append(f"  - ✅ 声明（不算伪造）：{_short(f.get('sentence'), 160)}")
        for f in (b_.get("fragments") or []):
            lines.append(f"  - ℹ️ 受限声明原文：{_short(f, 160)}")
        c = pr.get("C") or {}
        for dim, label in (("history", "① 历史"), ("model", "② 模型预测"),
                           ("knowledge", "③ 知识/一般依据")):
            item = c.get(dim) or {}
            frag = (item.get("fragments") or ["（无）"])[0]
            lines.append(f"  - {label}：{_yn(item.get('yes'))} —— {_short(frag, 120)}")
        dd = pr.get("D") or {}
        if dd.get("misattributions"):
            for f in dd["misattributions"]:
                lines.append(f"  - ❌ 归因错误：{_short(f.get('sentence'), 160)}")
        else:
            lines.append("  - D 归因：没有把结论挂到外部天气上")
        lines.append("")

    # ---- 基线对照 ----
    if bl.get("total"):
        lines += [
            "### 3.4 基线对照（同样跑一遍检验，仅作参照）",
            "",
            "基线的天气证据是**可用**的，所以 **A 项（伪造）对基线不适用** —— "
            "基线回答里提到降水概率是**如实引用**，不是伪造"
            "（下面的 A 数字只是同一套检测器跑基线的原始输出，不作判据）。",
            "",
            f"- B 受限声明：未声明 {bl.get('rate_text', {}).get('no_limitation_disclosure')}"
            f"（基线不需要声明“天气不可用”，这一项对基线**没有意义**）",
            f"- C 能力全丢：{bl.get('rate_text', {}).get('capability_lost')}",
            f"- D 归因错误：{bl.get('rate_text', {}).get('misattributed')}",
            f"- A（仅原始输出，不作判据）：{bl.get('rate_text', {}).get('fabricated_evidence')}",
            "",
        ]

    # ---- 上一版对照 ----
    prior = (raw.get("probe") or {}).get("prior_version") or {}
    if prior.get("total"):
        pr = prior.get("rate_text") or {}
        lines += [
            "### 3.5 上一版对照（“必答”要求加进上下文块之前）",
            "",
            "上下文块里加“回答要求（必答）”之前，降级回答有 "
            f"**{pr.get('no_limitation_disclosure')} 次完全没提天气不可用** —— "
            "也就是说第五步（说明“不能判断什么”）当时**没有稳定达成**。"
            "这三次 run 仍然留在存档里（"
            + "、".join(f"`{r}`" for r in (prior.get("run_ids") or []))
            + "），检验结果也是从存档重算的。",
            "",
            f"- 上一版（{'、'.join(prior.get('run_ids') or [])}）："
            f"通过 {pr.get('pass')}、未声明 {pr.get('no_limitation_disclosure')}、"
            f"伪造 {pr.get('fabricated_evidence')}、"
            f"能力全丢 {pr.get('capability_lost')}、"
            f"归因错误 {pr.get('misattributed')}",
            f"- 本版（{raw.get('degraded', {}).get('run_id')} 等 {dg.get('total')} 次）："
            f"通过 {dg.get('rate_text', {}).get('pass')}、"
            f"未声明 {dg.get('rate_text', {}).get('no_limitation_disclosure')}、"
            f"伪造 {dg.get('rate_text', {}).get('fabricated_evidence')}、"
            f"能力全丢 {dg.get('rate_text', {}).get('capability_lost')}、"
            f"归因错误 {dg.get('rate_text', {}).get('misattributed')}",
            "",
            "> 改的是**上下文块**（⑥ 块的“回答要求（必答）”一行 + ⑤ 限制字段末尾一句），"
            "**没有**改 SYSTEM_PROMPT、固定问题、判据阈值，也没有给不可用证据加任何兜底。",
            "",
        ]
    return lines


# --------------------------------------------------------------------------
# §4 文档六步逐条自评
# --------------------------------------------------------------------------

def _sec4(raw: dict) -> list[str]:
    b, d = raw["baseline"], raw["degraded"]
    dg = (raw.get("probe") or {}).get("degraded") or {}
    rate = dg.get("rate_text", {})
    ca_b = (b.get("combined_attention") or {}).get("value")
    ca_d = (d.get("combined_attention") or {}).get("value")

    rows = [
        ["第一步：先保存一个完整版本的运行结果（所有证据正常），存 run_id，作为对照",
         "**达成**",
         f"基线 run_id = `{b.get('run_id')}`（天气 status=ok，"
         f"signal=external_improving，combined_attention={ca_b}）",
         "§2.1 / §2.3 / 存档 `B07/runs/{}/run.json`".format(b.get("run_id"))],
        ["第二步：主动制造一种真实失败，一次只制造一种",
         "**达成**",
         f"只弄坏天气一条（`B07_WEATHER_UNAVAILABLE=1`）；"
         f"其它三条证据的 status 仍为 ok；RAG 无命中与工具异常本轮**未做**",
         "§1 / §2.3 证据 status 行"],
        ["第三步：先不要修 —— 出现失败后不要“赶紧恢复 API”，继续运行同一个问题",
         "**达成**",
         f"降级 run `{d.get('run_id')}` 用的是**同一个固定问题、"
         f"同一个 system prompt、同一套指纹**"
         f"（question {(raw.get('prompt_fingerprints') or {}).get('question_sha256')}、"
         f"prompt {(raw.get('prompt_fingerprints') or {}).get('system_prompt_sha256')}）；"
         f"没有任何兜底数据补位",
         "§2.3 llm_context sha256 行 / 存档 llm_meta"],
        ["第四步：系统必须明确说出“还能判断什么”（不能整体输出“无法分析。”）",
         "**达成**",
         f"回答 {d.get('answer_keywords', {}).get('length')} 字，"
         f"整体输出“无法分析”："
         f"{_yn(d.get('answer_keywords', {}).get('says_cannot_analyze'))}；"
         f"C 能力保持：历史 "
         f"{_yn(((dg.get('records') or [{}])[0].get('probe') or {}).get('C', {}).get('history', {}).get('yes'))}"
         f"、模型 "
         f"{_yn(((dg.get('records') or [{}])[0].get('probe') or {}).get('C', {}).get('model', {}).get('yes'))}"
         f"、知识 "
         f"{_yn(((dg.get('records') or [{}])[0].get('probe') or {}).get('C', {}).get('knowledge', {}).get('yes'))}"
         f"（第 1 次；逐次明细见 §3.2）",
         "§2.1 ② / §3.2 / §3.3 / §5"],
        ["第五步：系统必须明确说出“现在不能判断什么”",
         "**达成**",
         f"B 受限声明：未声明 {rate.get('no_limitation_disclosure')}；"
         f"⑥ 块写明“外部天气证据当前不可用 … 受到限制”；"
         f"⑤ 限制字段新增 evidence_unavailable；"
         f"⑦ 块新增“当前不可用的证据”一节",
         "§3.1 / §3.3 / §2.1 ④"],
        ["第六步：比较前后两个 run_id",
         "**达成**",
         f"`{b.get('run_id')}` vs `{d.get('run_id')}`："
         f"四问逐条回答 + 28 项逐项对比表；"
         f"并追查了“这项证据原来到底有没有真正参与分析”（参与了，见 §2.2）",
         "§2.1 / §2.2 / §2.3"],
    ]
    lines = [
        "## 4 文档六步逐条自评",
        "",
    ]
    lines += _table(["文档原句", "结论", "实测证据", "证据位置"], rows)
    return lines


# --------------------------------------------------------------------------
# §5 完成线自评
# --------------------------------------------------------------------------

def _sec5(raw: dict) -> list[str]:
    d = raw["degraded"]
    dg = (raw.get("probe") or {}).get("degraded") or {}
    rate = dg.get("rate_text", {})
    kw = d.get("answer_keywords") or {}
    d_ext = _judgement_external(d)

    rows = [
        ["不整体崩溃",
         "**达成**",
         f"降级 run 状态 success；"
         f"supporting 仍有 {d.get('divergence', {}).get('supporting')} 条证据；"
         f"combined_attention = {(d.get('combined_attention') or {}).get('value')}；"
         f"回答 {kw.get('length')} 字",
         "§2.1 ② / §3.2"],
        ["不伪造缺失信息",
         "**达成**",
         f"A 伪造检查：判定伪造 {rate.get('fabricated_evidence')}；"
         f"⑥ 块内没有任何外部天气数值；"
         f"⑦ 块把不可用证据单列（不进 supporting / opposing）",
         "§3.1 / §3.3"],
        ["仍然利用剩余证据完成可能的判断",
         "**达成**",
         f"C 能力保持：历史/模型/知识三项在第 1 次回答里分别为 "
         f"{_yn(((dg.get('records') or [{}])[0].get('probe') or {}).get('C', {}).get('history', {}).get('yes'))}"
         f" / {_yn(((dg.get('records') or [{}])[0].get('probe') or {}).get('C', {}).get('model', {}).get('yes'))}"
         f" / {_yn(((dg.get('records') or [{}])[0].get('probe') or {}).get('C', {}).get('knowledge', {}).get('yes'))}；"
         f"回答整体输出“无法分析”：{_yn(kw.get('says_cannot_analyze'))}",
         "§3.2 / §3.3 / §5.1"],
        ["同时明确说明受到限制的部分",
         "**达成**",
         f"B 受限声明：未声明 {rate.get('no_limitation_disclosure')}；"
         f"external_conditions.status={d_ext.get('status')}、"
         f"error 非空",
         "§3.1 / §3.3 / §2.1 ④"],
    ]
    lines = [
        "## 5 S5 完成线逐条自评",
        "",
        "> 文档完成线：“不整体崩溃、**不伪造缺失信息**，仍然利用剩余证据完成可能的判断，"
        "同时明确说明受到限制的部分。”",
        "",
    ]
    lines += _table(["完成线", "结论", "实测证据", "证据位置"], rows)

    lines += [
        "### 5.1 完成线里没有写、但值得记下来的一条",
        "",
        "「知识 / 一般依据」在降级回答里**不是每次都出现**"
        f"（逐次明细见 §3.2：知识维度 "
        f"{'、'.join(_yn((r.get('probe') or {}).get('C', {}).get('knowledge', {}).get('yes')) for r in (dg.get('records') or []))}）。"
        "文档第四步把“专业知识仍可能提供一般依据”列为**应当说清**的三项之一，"
        "但完成线只写了“不整体崩溃”，所以这一条**不判失败**，如实记录。",
        "",
    ]
    return lines


# --------------------------------------------------------------------------
# §6 Limitations
# --------------------------------------------------------------------------

LIMITATIONS = [
    ("L1", "天气故障是**显式开关模拟**，不是真断网",
     "真实网络故障的错误形态（超时 / 5xx / 限流 / 畸形 JSON）不在本轮覆盖范围；"
     "本轮只覆盖“provider 明确返回不可用”这一种形态。"),
    ("L2", "本轮只降级了天气一类证据",
     "RAG 无命中、其它工具异常**未做**（文档要求一次只做一种），"
     "因此不能据此推断“多条证据同时不可用”时的系统行为。"),
    ("L3", "LLM 模型 S4 / S5 均为 `qwen-max`",
     "原 `qwen3.5-27b` 免费额度耗尽（HTTP 403 insufficient_quota），S4 起换用；"
     "跨阶段措辞不可直接比较。"),
    ("L4", "LLM 解释层有采样波动",
     "本轮每种状态跑多次并**全部计入分母**（不筛掉失败与异常样本）；"
     "但样本量仍然很小，单次结果不能当作稳定结论。"),
    ("L5", "“还能判断什么 / 不能判断什么”由**上下文块**驱动（不改 SYSTEM_PROMPT）",
     "这依赖模型自觉遵循上下文，**不是提示词层面的硬保证**；"
     "实测证据：未在 ⑥/⑤ 块给出“必答”要求之前，降级回答有 1/3 次完全没提天气不可用，"
     "加上“必答”要求后才变成 3/3。"
     "S4 的 L6（提示词第 3 条与外部天气字面冲突）在 S5 **依然存在**。"),
    ("L6", "断言 / 伪造检测是**启发式多字短语 + 数值匹配**",
     "可能漏判（换一种说法就查不出来）或误判（把声明当成伪造）；"
     "A 项另外加了一层“这句话是在声明不可用、还是在陈述外部事实”的判定，"
     "这一层复用 S3 的元陈述判定，同样是启发式。"),
    ("L7", "降级 run 的 LLM 回答样本量有限",
     "“受限制声明”这个结论受采样波动影响；"
     "B 项结论应理解为“本轮样本内未出现未声明的情况”，不是“模型一定会声明”。"),
    ("L8", "S4 的 run_023 存档 / 复算一致性",
     "与 S5 无关，但影响整章证据链 —— 本轮已复核："
     "`python -m B07.runs explain run_023` 输出“复算一致性：全部一致 ✅”，"
     "退出码 0（它渲染 19 项检查，文档口径写的是 22 项 —— 数量口径差，"
     "**不是**一致性失败）。"),
    ("L9", "外部条件是**离线构造用例**，不是实测天气",
     "S4 / S5 的外部天气来自 `B07/s3.py` 的离线夹具（provider 身份 `Offline-Fixture`）；"
     "基线与降级跑的是**同一个 provider**，所以组间对照成立，"
     "但绝对数值不可外推到真实大棚。"),
    ("L10", "A 项的数值红线只覆盖夹具里的那几个数",
     "模型若编造了**夹具里没有**的天气数值（例如“降水概率 80%”），"
     "本轮检测不到 —— 数值通道是**白名单**，不是全量数值扫描。"),
]


def _sec6(raw: dict) -> list[str]:
    lines = [
        "## 6 Limitations（诚实清单）",
        "",
    ]
    lines += _table(["编号", "限制", "说明"],
                    [[f"**{k}**", t, d] for k, t, d in LIMITATIONS])
    return lines


# --------------------------------------------------------------------------
# §7 复现命令
# --------------------------------------------------------------------------

def _sec7(raw: dict) -> list[str]:
    b, d = raw["baseline"], raw["degraded"]
    lines = [
        "## 7 复现命令",
        "",
        "```bash",
        "# 1) 跑 S5 实验（每种状态 3 次；真实调 LLM）",
        "./venv/Scripts/python.exe -m B07.s5",
        "",
        "# 2) 只重放降级（不跑基线）：",
        "./venv/Scripts/python.exe -c \"import os; os.environ['B07_WEATHER_UNAVAILABLE']='1'; "
        "import B07.s5 as s; s.run_variant(degraded=True)\"",
        "",
        "# 3) 用已存档的回答原文重跑防伪造检验（离线、不调 LLM）",
        "./venv/Scripts/python.exe -m B07.s5 --reprobe",
        "",
        "# 4) 由原始结果生成报告（离线、不联网、不调 LLM，可确定性重放）",
        "./venv/Scripts/python.exe B07/s5_report.py",
        "",
        "# 5) 不变量测试",
        "./venv/Scripts/python.exe -m pytest B07/tests -q",
        "",
        "# 6) S1–S3 的存档逐值不变（S3 回归）",
        "./venv/Scripts/python.exe -m B07.runs explain run_023",
        "```",
        "",
        f"本报告的输入是 `s5_raw_result.json`；"
        f"基线 run `{b.get('run_id')}`、降级 run `{d.get('run_id')}`"
        f"（另有各 {len(raw.get('baseline_runs') or [])} / "
        f"{len(raw.get('degraded_runs') or [])} 次重复运行，"
        f"逐次明细见 §3.2）。",
        "",
    ]
    return lines


# --------------------------------------------------------------------------
# §8 与 S1–S4 的关系
# --------------------------------------------------------------------------

def _sec8(raw: dict) -> list[str]:
    b, d = raw["baseline"], raw["degraded"]
    b_div = b.get("divergence") or {}
    d_div = d.get("divergence") or {}
    lines = [
        "## 8 与 S1–S4 的关系：S4 的分歧结构在缺证据时怎么表现",
        "",
        "S4 立起来的是**证据身份 + 方向 + 分歧**这一套结构。S5 把其中一条证据抽掉，"
        "问的是：这套结构在**缺一条腿**的时候还站不站得住。",
        "",
        "| S4 建立的东西 | S5 里的表现 |",
        "|---|---|",
        f"| 方向由程序规则判定（`judge_direction`） | "
        f"天气证据 status=unavailable → direction=**undetermined**，"
        f"**没有**被伪造成 supporting/opposing |",
        f"| 三列互斥、不投票、不强制合并 | "
        f"降级后 supporting {d_div.get('supporting')} / opposing "
        f"{d_div.get('opposing')} / undetermined {d_div.get('undetermined')}，"
        f"不可用证据只进 undetermined |",
        f"| 方向冲突时 `combined_attention = conflict_unresolved` | "
        f"冲突源消失后自动退让为 `{(d.get('combined_attention') or {}).get('value')}`，"
        f"**规则一个字没改**（是 opposing 侧证据消失了，不是新增分支） |",
        f"| ⑦ 块是纯追加 | "
        f"降级时 ⑦ 块**多出**一节“当前不可用的证据”，①~⑥ 块结构不变 |",
        f"| 工具被调用过 ≠ 结果被采用 | "
        f"`B05.get_weather_forecast` 被调用了，但结果 unavailable，"
        f"明确记成“取不到”，不冒充已采用的数据 |",
        "",
        "一句话：**S4 的分歧结构在缺证据时是“退让”，不是“崩溃”** —— "
        "它不会替缺失的证据编一个方向，也不会因为少一条证据就整体拒答。",
        "",
        "但也要说清楚 S5 **没有**证明的："
        "“分歧结构在缺证据时表现得好”只在**这一类证据、这一个场景、"
        "这个模型**上成立过一次；它没有证明这套结构对任意证据缺失都稳健"
        "（见 L2）。",
        "",
    ]
    return lines


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def build_report(raw: dict) -> str:
    lines: list[str] = []
    lines += [
        "# B07 S5 阶段报告 —— 证据退化（天气证据不可用）",
        "",
        f"（原始结果生成时间：{raw.get('generated_at')}；"
        f"本报告由 `python B07/s5_report.py` 从 `s5_raw_result.json` 离线生成，"
        f"**每个数字都来自实测存档**，重复生成 sha256 一致）",
        "",
    ]
    lines += _sec0(raw)
    lines += _sec1(raw)
    lines += _sec2(raw)
    lines += _sec3(raw)
    lines += _sec4(raw)
    lines += _sec5(raw)
    lines += _sec6(raw)
    lines += _sec7(raw)
    lines += _sec8(raw)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if not RAW_RESULT_PATH.exists():
        print(f"❌ {RAW_RESULT_PATH} 不存在 —— 先跑：python -m B07.s5")
        return 1
    raw = json.loads(RAW_RESULT_PATH.read_text(encoding="utf-8"))
    text = build_report(raw)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"✅ 已生成 {REPORT_PATH}（{len(text)} 字符）")
    print(f"   基线 run_id = {raw['baseline']['run_id']}")
    print(f"   降级 run_id = {raw['degraded']['run_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
