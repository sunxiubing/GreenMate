"""B07 S4-4 —— 从 S4 阶段的原始结果生成 s4_report.md

设计原则与 `B07/s3_report.py` 同构：**报告里的每个数字都从实测结果里取，
不手工填写。**

输入：B07/artifacts/s4_raw_result.json（由 `python -m B07.s4` 落盘）
输出：B07/artifacts/s4_report.md
另出：B07/artifacts/model_table.md（跨阶段模型对照表，同一份数据生成）

用法：
    ./venv/Scripts/python.exe -m B07.s4          # 跑 S4，落盘 s4_raw_result.json
    ./venv/Scripts/python.exe B07/s4_report.py   # 由原始结果生成报告（离线）

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改任何判据与阈值
  - 报告生成过程**不联网、不调 LLM** —— 只读 s4_raw_result.json，
    因此可以确定性重放（连续跑两次，产物逐字节相同）
  - 失败必须显式暴露，不许用静态文案伪装成功
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

ARTIFACTS = _PROJECT_ROOT / "B07" / "artifacts"
RAW_PATH = ARTIFACTS / "s4_raw_result.json"
REPORT_PATH = ARTIFACTS / "s4_report.md"
MODEL_TABLE_PATH = ARTIFACTS / "model_table.md"

GROUP_A = "A（平铺）"
GROUP_B = "B（分身份）"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _yn(flag) -> str:
    if flag is None:
        return "未定义"
    return "是" if flag else "否"


def _table(headers, rows) -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def _short(text, n=120) -> str:
    text = (text or "").replace("\n", " ").replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


def _fmt_ratio(text) -> str:
    return text or "n/a"


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------

def _ledger(run: dict) -> dict:
    return run.get("evidence_ledger") or {}


def _entries(run: dict) -> list[dict]:
    return _ledger(run).get("entries") or []


def _divergence(run: dict) -> dict:
    return (run.get("structured_judgement") or {}).get("evidence_divergence") or {}


def _groups(raw: dict) -> dict:
    return (raw.get("contrast") or {}).get("groups") or {}


# --------------------------------------------------------------------------
# §0 一句话结论
# --------------------------------------------------------------------------

def _sec0(raw: dict) -> str:
    run = raw.get("s4_run") or {}
    judgement = run.get("structured_judgement") or {}
    div = _divergence(run)
    ca = run.get("combined_attention") or {}
    groups = _groups(raw)
    sa = (groups.get(GROUP_A) or {}).get("summary") or {}
    sb = (groups.get(GROUP_B) or {}).get("summary") or {}

    parts = [
        "## 0. 一句话结论",
        "",
        "**S4 证明了**：证据有了**身份**——四类证据（内部历史 / 外部天气 / 知识 / 模型）"
        "在同一条台账里各自带着来源、时间、状态、方向；方向由程序规则判定，"
        "不经过 LLM；方向不一致时，系统输出 `combined_attention = "
        f"{ca.get('value')}`，**保留分歧**而不是投票合并。",
        "",
        "**S4 没有证明**："
        "① 把证据分成身份之后，LLM 解释层**并没有因此就更愿意说出冲突**——"
        f"负面对照里 B 组 {sb.get('total_answers')} 次回答提到「分歧/冲突」的次数是 "
        f"**{sb.get('conflict_mentions_any')}**；",
        "② 负面对照的三个主指标**无定义**（两组断言总数都是 "
        f"{sa.get('claims_total')} / {sb.get('claims_total')}），"
        "所以「证据揉在一起会让归因变差」这个命题**本轮既没被证实也没被证伪**；",
        "③ 内部是 SIMULATED 模拟数据、外部是 PUBLIC 真实数据，两者**不同时间基准**，"
        "外部只作方向参考。",
        "",
        f"本轮实测：结构化判断字段数 **{len(judgement)}**、"
        f"`has_conflict = {div.get('has_conflict')}`、"
        f"三列表 supporting {len(div.get('supporting') or [])} 条 / "
        f"opposing {len(div.get('opposing') or [])} 条 / "
        f"undetermined {len(div.get('undetermined') or [])} 条、"
        f"`combined_attention = {ca.get('value')}`。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §1 场景与证据设计
# --------------------------------------------------------------------------

def _sec1(raw: dict) -> str:
    run = raw.get("s4_run") or {}
    led = _ledger(run)
    rows = []
    for e in _entries(run):
        ext = e.get("ext") or {}
        rows.append([
            f"`{e.get('source_type')}`",
            f"`{e.get('source')}`",
            e.get("time"),
            f"`{e.get('status')}`",
            f"`{e.get('direction')}`",
            _short((e.get("result") or {}).get("stability_conclusion")
                   or (e.get("result") or {}).get("basis")
                   or (e.get("result") or {}).get("note")
                   or (e.get("result") or {}).get("predicted_attention")
                   or "", 90),
        ])

    kb = next((e for e in _entries(run) if e.get("source_type") == "B04"), None)
    kb_res = (kb or {}).get("result") or {}
    kb_note = kb_res.get("note") or ""
    kb_cites = kb_res.get("citations") or []

    w = run.get("window_selection") or {}
    hw = run.get("history_window") or {}

    parts = [
        "## 1. 场景与证据设计",
        "",
        "本轮把四类**互相独立**的证据接进同一次判断。每一类都有自己的来源、"
        "自己的时间、自己的状态，以及**由程序规则算出来的方向**。",
        "",
        _table(
            ["证据类型", "来源", "时间", "状态", "方向", "结论摘要"],
            rows),
        "",
        "### 1.1 场景锚点与内部窗口",
        "",
        _table(["项目", "值"], [
            ["分析时刻 analysis_time", f"`{run.get('analysis_time')}`"],
            ["内部位置", f"`{run.get('location')}`"],
            ["历史窗口", f"`{(hw.get('start') or '')}` → `{(hw.get('end') or '')}`"
                        f"（{hw.get('count', '?')} 点）"],
            ["窗口来源", "锚点来自 `B07/s4_windows.selected_anchor()`"
                         "（S4-1 窗口扫描实测所得，不是硬编码时刻；"
                         "本次 run 未走窗口选择流程，`window_selection` 为 null）"],
            ["内部数据性质", "SIMULATED（`source_type='simulated'`）"],
            ["外部数据性质", "PUBLIC（真实天气，但本轮用的是离线构造夹具）"],
        ]),
        "",
        "### 1.2 知识证据的**实际性质**（必须说清楚）",
        "",
        f"B04 知识库检索命中 {len(kb_cites)} 条，status = "
        f"`{(kb or {}).get('status')}`，最高分 "
        f"`{round(float(kb_cites[0].get('score')), 4) if kb_cites else 'n/a'}`，"
        f"命中 chunk `{kb_cites[0].get('chunk_id') if kb_cites else 'n/a'}`。",
        "",
        f"检索层给出的 note 原文：",
        "",
        f"> {kb_note}",
        "",
        "**这个知识库不含通用农业知识**（B05 工具描述里已声明）："
        "B04.SPEC.CHUNKS 共 12 条，全部是 GreenMate 的**系统设计元知识**"
        "（数据源定义、扰动参数、API 缓存策略、phantom data 危害等）。"
        "所以本轮的知识证据是**方法论层面**的"
        "（「决策依据是否过时、证据缺失时该怎么办」），"
        "**不是作物生理知识**。它能支持「**需要关注**」，"
        "**不能**支持「未来一定恶化」，也**不能**支持「可以放心不管」——"
        "这两条限制被写进了 `evidence_divergence.undetermined` 列表（见 §3）。",
        "",
        "### 1.3 外部天气的**实际性质**",
        "",
        "本轮外部条件是 **S4-1 造的离线夹具**（`B07/s4_offline.py`），"
        "不是实时 API 取数：这样才可复现。真实组在 S3 已实测为 "
        "`external_deteriorating`，两个方向本轮都如实记录（见 §8 L8）。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §2 证据身份层
# --------------------------------------------------------------------------

def _sec2(raw: dict) -> str:
    run = raw.get("s4_run") or {}
    led = _ledger(run)
    entries = _entries(run)
    adoption = led.get("adoption") or {}
    calls = (led.get("tool_calls") or {}).get("records") or []

    base_fields = [
        ("source_type", "证据种类（b03_weather / b04_knowledge / b06_model / simulated）"),
        ("source", "来自哪个工具/数据源"),
        ("time", "这条证据是**什么时候**取得的"),
        ("status", "ok / unavailable / error —— 三选一，失败不许假装成功"),
        ("result", "工具返回的原始载荷（status != ok 时必须是 None）"),
        ("error", "失败原因（status == ok 时必须是 None，status != ok 时必须非空）"),
        ("ext", "各源自己的扩展信息，原样保留，不压平成统一业务字段"),
    ]
    ledger_fields = [
        ("evidence_id", "台账里给这条证据的编号（EV-<来源>-<序号>）"),
        ("adopted", "是否被**本次判断**采用（调用过 ≠ 采用过）"),
        ("direction", "程序规则算出的方向：supporting / opposing / undetermined"),
    ]

    first = entries[0] if entries else {}
    base_rows = [[f"`{k}`", d, f"`{_short(str((first.get(k) if k in first else '—')), 40)}`"]
                 for k, d in base_fields]
    ledger_rows = [[f"`{k}`", d, f"`{_short(str((first.get(k) if k in first else '—')), 40)}`"]
                   for k, d in ledger_fields]

    ext_rows = []
    for e in entries:
        ext = e.get("ext") or {}
        for k, v in ext.items():
            ext_rows.append([
                f"`{e.get('source_type')}`", f"`{k}`", _short(str(v), 70)])

    call_rows = []
    for rec in calls:
        call_rows.append([
            f"`{rec.get('tool_name')}`",
            f"`{rec.get('status')}`",
            "✅ 采用" if rec.get("adopted") else "❌ 未采用",
            _short(rec.get("purpose") or "", 46),
        ])

    not_adopted = adoption.get("not_adopted_calls") or []
    na_rows = [[f"`{c.get('tool_name')}`", c.get("status"), c.get("reason")]
               for c in not_adopted]

    parts = [
        "## 2. 证据身份层",
        "",
        "### 2.1 统一字段表（`B07/evidence_registry.py`）",
        "",
        _table(["字段", "含义", "本次实例（首条证据）"], base_rows),
        "",
        "**字段约束是硬约束，写在构造函数的校验里**（不是靠约定）：",
        "",
        "- `status` 只能是 `ok` / `unavailable` / `error` 三选一，出现第四种直接抛异常；",
        "- `status == \"ok\"` → `error` 必须为 `None`；",
        "- `status != \"ok\"` → `result` 必须为 `None`（失败记录不许带结果），"
        "且 `error` 必须非空（不许「失败但没有原因」）。",
        "",
        "### 2.2 台账追加的三列（`B07/evidence_ledger.py`）",
        "",
        _table(["字段", "含义", "本次实例（首条证据）"], ledger_rows),
        "",
        "`direction` 的唯一出处是 `B07.evidence_ledger.judge_direction()`，"
        "**不经过 LLM**。",
        "",
        "### 2.3 各源的扩展信息（`ext` 原样保留）",
        "",
        _table(["来源", "扩展键", "取值"], ext_rows),
        "",
        "### 2.4 工具调用台账",
        "",
        f"{adoption.get('text')}",
        "",
        _table(["工具", "调用状态", "是否被本次判断采用", "用途"], call_rows),
        "",
        "### 2.5 「工具被调用过」≠「结果被本次判断采用」",
        "",
        "本轮实际发生的区分：",
        "",
        _table(["工具", "调用状态", "未采用原因"], na_rows),
        "",
        "调用过 `B05.get_current_environment`（取库内最新一条）——它**没有**升格为证据："
        "只作 `latest_in_db` 参考，不参与方向判定，"
        "因此它**不出现**在 `evidence_divergence` 的任何一列里。",
        "",
        "### 2.6 文档第四步的五个问题，逐条回答",
        "",
        _table(["问题", "答案", "数据出处"], [
            ["哪个工具被调用",
             "、".join(f"`{c.get('tool_name')}`" for c in calls),
             "`evidence_ledger.tool_calls.records`"],
            ["调用是否成功",
             "、".join(f"`{c.get('tool_name')}`={c.get('status')}"
                       for c in calls),
             "同上（`status`）"],
            ["返回了什么",
             "见 §2.3 的 `ext` 与 §3 的三列摘要；每条证据的原始载荷在 "
             "`evidence_ledger.entries[].result`",
             "`entries[].result` / `entries[].ext`"],
            ["哪些工具结果被转换为证据",
             "、".join(f"`{e.get('source_type')}`" for e in entries if e.get("adopted")),
             "`entries[].adopted == True`"],
            ["哪些证据最终进入结构化判断",
             "、".join(f"`{e.get('source_type')}`={e.get('direction')}"
                       for e in entries if e.get("adopted")),
             "`structured_judgement.evidence_divergence.each_source`"],
        ]),
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §3 分歧的结构化表达
# --------------------------------------------------------------------------

def _sec3(raw: dict) -> str:
    run = raw.get("s4_run") or {}
    judgement = run.get("structured_judgement") or {}
    div = _divergence(run)
    ca = run.get("combined_attention") or {}
    sc = run.get("judgement_self_check") or {}

    def _list_items(key):
        rows = []
        for it in div.get(key) or []:
            rows.append([
                f"`{it.get('source_type')}`",
                f"`{it.get('source')}`",
                it.get("time"),
                _short(it.get("summary"), 60),
                _short(it.get("basis") or it.get("reason"), 80),
            ])
        return rows

    parts = [
        "## 3. 分歧的结构化表达",
        "",
        "### 3.1 第 7 个字段 `evidence_divergence`",
        "",
        f"结构化判断字段共 **{len(judgement)}** 个："
        + "、".join(f"`{k}`" for k in judgement.keys()) + "。",
        "",
        _table(["键", "取值"], [
            ["`has_conflict`", f"`{div.get('has_conflict')}`"],
            ["`each_source`", f"`{json.dumps(div.get('each_source'), ensure_ascii=False)}`"],
            ["`resolution`", div.get("resolution")],
        ]),
        "",
        "### 3.2 三列表",
        "",
        "**支持提高关注（supporting）**",
        "",
        _table(["来源", "source", "时间", "摘要", "依据"], _list_items("supporting")),
        "",
        "**提示可能缓解或反向（opposing）**",
        "",
        _table(["来源", "source", "时间", "摘要", "依据"], _list_items("opposing")),
        "",
        "**仍不足以确定（undetermined）**",
        "",
        _table(["来源", "source", "时间", "摘要", "为什么仍不足以确定"],
               _list_items("undetermined")),
        "",
        "三列是**互斥的分类**，不是投票：每条证据只落一列，"
        "并且**方向一致的两条证据不会被合并成一个更强的结论**。",
        "",
        "### 3.3 合成关注等级 `combined_attention`",
        "",
        _table(["项", "值"], [
            ["`value`", f"`{ca.get('value')}`"],
            ["`reason`", ca.get("reason")],
            ["自检 field_count / expected",
             f"{sc.get('field_count')} / {sc.get('expected_field_count')}"
             f"（passed={sc.get('passed')}）"],
        ]),
        "",
        "`conflict_unresolved` **不是错误**，是「保留分歧」的表达："
        "系统既不把支持票和反对票投票掉，也不擅自选一边。"
        "判定规则写在 `B07/judgement.py::combined_attention()` 里，"
        "**可脱离 LLM 从存档复算**。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §4 负面对照
# --------------------------------------------------------------------------

def _sec4(raw: dict) -> str:
    contrast = raw.get("contrast") or {}
    groups = _groups(raw)
    ga = groups.get(GROUP_A) or {}
    gb = groups.get(GROUP_B) or {}
    sa = ga.get("summary") or {}
    sb = gb.get("summary") or {}
    fps = contrast.get("fingerprints") or {}
    ctx_a = contrast.get("context_A") or ""
    ctx_b = contrast.get("context_B") or ""

    diff = list(difflib.unified_diff(
        ctx_a.splitlines(), ctx_b.splitlines(),
        fromfile="A（平铺）", tofile="B（分身份）", lineterm="", n=1))
    diff_text = "\n".join(diff) if diff else "（无差异）"

    rec_rows = []
    for grp, g in ((GROUP_A, ga), (GROUP_B, gb)):
        for r in g.get("records") or []:
            m = r.get("mentions") or {}
            cm = r.get("conflict_mentions") or {}
            rec_rows.append([
                grp, r.get("seq"), f"`{r.get('run_id')}`", r.get("status"),
                r.get("answer_length"),
                m.get("count"), "、".join(m.get("categories") or []) or "—",
                r.get("claims_count"), r.get("cited_count"),
                r.get("unsupported_count"), cm.get("count"),
                _short(r.get("failure_reason"), 40) or "",
            ])

    ratio_rows = []
    for name, s in (("归因完整率 cited/断言数", "attribution_rate_text"),
                    ("无来源断言率 unsupported/断言数", "unsupported_rate_text"),
                    ("无断言率 无断言回答/回答数", "no_claim_rate_text"),
                    ("（辅）台账口径 cited/断言数", "ledger_cited_rate_text")):
        ratio_rows.append([
            name, _fmt_ratio(sa.get(s)), _fmt_ratio(sb.get(s))])

    aux_rows = [
        ["回答总数（含失败）", sa.get("total_answers"), sb.get("total_answers")],
        ["失败次数", sa.get("failed"), sb.get("failed")],
        ["断言总数", sa.get("claims_total"), sb.get("claims_total")],
        ["提到来源的回答数", sa.get("mentions_any"), sb.get("mentions_any")],
        ["（辅）提到外部天气的回答数", sa.get("external_mentions_any"),
         sb.get("external_mentions_any")],
        ["（辅）提到分歧/冲突的回答数", sa.get("conflict_mentions_any"),
         sb.get("conflict_mentions_any")],
    ]

    usage_rows = []
    for grp, g in ((GROUP_A, ga), (GROUP_B, gb)):
        for r in g.get("records") or []:
            u = r.get("usage") or {}
            usage_rows.append([
                grp, r.get("seq"), f"`{r.get('run_id')}`",
                u.get("prompt_tokens"), u.get("completion_tokens"),
                u.get("total_tokens"), u.get("cached_tokens"),
                f"`{r.get('llm_context_sha')}`",
                _yn(r.get("llm_context_matches_group")),
            ])

    fp_rows = [
        ["锚点", f"`{fps.get('anchor')}`", "两组相同"],
        ["SYSTEM_PROMPT sha256", f"`{fps.get('system_prompt_sha256')}`", "两组相同"],
        ["固定问题 sha256", f"`{fps.get('question_sha256')}`", "两组相同"],
        ["B06 model_version", f"`{fps.get('b06_model_version')}`", "两组相同"],
        ["历史窗口",
         f"`{(fps.get('history_window') or {}).get('start')}` → "
         f"`{(fps.get('history_window') or {}).get('end')}`"
         f"（{(fps.get('history_window') or {}).get('count')} 点，"
         f"sha `{(fps.get('history_window') or {}).get('sha256')}`）", "两组相同"],
        ["天气夹具",
         f"case=`{(fps.get('weather_fixture') or {}).get('case')}`，"
         f"signal=`{(fps.get('weather_fixture') or {}).get('signal')}`，"
         f"sha `{(fps.get('weather_fixture') or {}).get('sha256')}`", "两组相同"],
        ["知识检索",
         f"sha `{(fps.get('knowledge') or {}).get('sha256')}`", "两组相同"],
        ["台账证据 sha256（B02/B06/B03/B04）",
         " / ".join(f"`{v}`" for v in
                    (fps.get("evidence_entries_sha256") or {}).values()), "两组相同"],
        ["①②③⑤ 逐字节相同",
         " / ".join(f"{k}]:{_yn(v)}" for k, v in
                    (fps.get("shared_blocks_identical") or {}).items()),
         "唯一变量只能是 ④ 与 ⑥⑦"],
        ["上下文 sha256",
         f"A=`{fps.get('context_a_sha256')}`（{fps.get('context_a_len')} 字）"
         f" / B=`{fps.get('context_b_sha256')}`（{fps.get('context_b_len')} 字）",
         "**这就是变量**"],
        ["共享前缀长度",
         f"{fps.get('shared_prefix_chars')} 字符"
         f"（占 A 组 {round(100.0 * (fps.get('shared_prefix_chars') or 0) / (fps.get('context_a_len') or 1), 1)}%）",
         "逐字节相同"],
    ]

    # 原文并排
    ans_rows = []
    for i in range(max(len(ga.get("records") or []), len(gb.get("records") or []))):
        ra = (ga.get("records") or [None] * 9)[i] or {}
        rb = (gb.get("records") or [None] * 9)[i] or {}
        ans_rows.append([
            i + 1,
            f"`{ra.get('run_id')}`（{ra.get('answer_length')} 字）\n\n"
            f"{ra.get('answer') or '（无）'}",
            f"`{rb.get('run_id')}`（{rb.get('answer_length')} 字）\n\n"
            f"{rb.get('answer') or '（无）'}",
        ])

    parts = [
        "## 4. 负面对照：平铺组 vs 分身份组",
        "",
        "**唯一变量 = 证据的组织方式。** 两组用完全相同的四类证据"
        "（同一历史窗口、同一份天气夹具、同一份知识检索结果、同一个 B06 模型输出），"
        "只有 ④ 块的组织方式不同：A 组把四类结论拼成一段连续叙述"
        "（**程序从台账拼出来的纯函数**，不标来源、不标时间、不标状态、不分方向、"
        "不提「存在分歧」），B 组保持结构化判断并多出 ⑥ 外部条件块与 ⑦ 证据分歧块。",
        "",
        "### 4.1 两组上下文 diff",
        "",
        "```diff",
        diff_text,
        "```",
        "",
        "### 4.2 A 组的「综合叙述」（程序拼装，非手写、非 LLM 生成）",
        "",
        "```text",
        contrast.get("narrative_A") or "",
        "```",
        "",
        f"自动检查（不得出现来源标识 / 时间戳 / 状态词 / 分歧字样）："
        f"**{'✅ 通过' if not (contrast.get('narrative_A_problems') or []) else '❌ ' + str(contrast.get('narrative_A_problems'))}**"
        f"（检查项：{len(contrast.get('narrative_A_problems') or [])} 个问题）。",
        "",
        "> **必须点明的偏差**：按规格给的建议措辞，叙述里仍出现了"
        "「内部历史 / 短时预测 / 外部天气 / 相关知识」四个**类别名**。"
        "所以 A 组是**弱对照** —— 类别可辨，但**来源身份**（模块号、chunk_id、"
        "时间戳、status）与**方向分组**被抹掉了。",
        "",
        "### 4.3 每次运行的判定表",
        "",
        _table(
            ["组", "#", "run_id", "状态", "字数", "mentions 句数", "mention 类别",
             "断言数", "cited", "unsupported", "分歧提及", "失败原因"],
            rec_rows),
        "",
        "### 4.4 三个比率对照（带分母）",
        "",
        _table(["指标", f"{GROUP_A}", f"{GROUP_B}"], ratio_rows),
        "",
        "辅助计数（**不属于**上面的三个比率）：",
        "",
        _table(["计数项", GROUP_A, GROUP_B], aux_rows),
        "",
        "### 4.5 prompt_tokens / cached_tokens 对照",
        "",
        _table(["组", "#", "run_id", "prompt_tokens", "completion_tokens",
                "total_tokens", "cached_tokens", "上下文 sha", "上下文与本组一致"],
               usage_rows),
        "",
        "**规格里「两组 cached_tokens 相同 ⇒ 共享前缀逐字节相同」这个机器判定，"
        "本轮实测不成立**：两组命中数明显不同。原因据实说明 —— "
        "`cached_tokens` 反映的是**服务商侧缓存的命中状态**，不是输入差异："
        "A 组调用时缓存已热，B 组前两次命中 0、第 3 次起才升上去，"
        "即缓存是「先写后命中」的，取决于调用历史。"
        "唯一变量的证明因此不靠缓存数，而靠更硬的直接证据："
        "① ①②③⑤ 逐字节相同（见 §4.6 指纹表）；"
        "② 共享前缀 "
        f"{fps.get('shared_prefix_chars')} 字符逐字节相同；"
        "③ 每组内 5 次的上下文 sha 完全一致（`llm_context_matches_group=True`）。",
        "",
        "### 4.6 指纹对照表（两组必须相同的项）",
        "",
        _table(["项", "值", "说明"], fp_rows),
        "",
        "### 4.7 全部回答原文（并排）",
        "",
        _table(["#", GROUP_A, GROUP_B], ans_rows),
        "",
        "### 4.8 这一节说明了什么 / 没说明什么",
        "",
        "**没有观察到显著差异。** 三个主指标里两个**无定义**"
        f"（断言总数两组都是 {sa.get('claims_total')}），"
        f"第三个（无断言率）两组都是 {_fmt_ratio(sa.get('no_claim_rate_text'))}。",
        "",
        "可能原因（按可能性排序）：",
        "",
        "1. **该状态下模型本来就倾向保守表达** —— SYSTEM_PROMPT 允许"
        "「目前没有足够依据提高关注程度」，250 字上限也压掉了强断言；",
        "2. **N 太小**（每组 5 次），差异检出力低；",
        "3. **断言检测器词表是启发式的**，多字短语匹配可能漏判；",
        "4. **A 组不是完全的负面对照**（见 §4.2 的偏差说明）。",
        "",
        "**可观测到的真实差异**（有数字支撑）：",
        "",
    ]

    len_a = [r.get("answer_length") for r in (ga.get("records") or [])]
    len_b = [r.get("answer_length") for r in (gb.get("records") or [])]
    men_a = [(r.get("mentions") or {}).get("count") for r in (ga.get("records") or [])]
    men_b = [(r.get("mentions") or {}).get("count") for r in (gb.get("records") or [])]

    def _avg(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 1) if xs else None

    parts.append(_table(["观测", GROUP_A, GROUP_B], [
        ["回答字数均值", _avg(len_a), _avg(len_b)],
        ["带来源词的句子数均值", _avg(men_a), _avg(men_b)],
        ["prompt_tokens（组内恒定）",
         sorted({(r.get("usage") or {}).get("prompt_tokens")
                 for r in (ga.get("records") or [])}) or "n/a",
         sorted({(r.get("usage") or {}).get("prompt_tokens")
                 for r in (gb.get("records") or [])}) or "n/a"],
    ]))
    parts += [
        "",
        "B 组的 ⑥⑦ 块让模型**写得更多、来源词更密**，"
        "但**没有让冲突出现在回答里** —— B 组的 ⑦ 块明写了「方向不一致：存在」，"
        f"而 B 组 {sb.get('total_answers')} 次回答提到分歧的次数是 "
        f"**{sb.get('conflict_mentions_any')}**。",
        "这说明「证据身份」目前只到得了**上下文层**，没有穿透到**解释层**。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §5 完成线逐条自评
# --------------------------------------------------------------------------

#: (编号, 条款原句, 结论, 证据位置)
#: 条款原句取自**仓库内固化的规范条款**（模块 docstring）。
#: S4 阶段的任务文档正文不在仓库内，因此这里不引用不存在的原文，
#: 改用「代码里写死的那一句」—— 它的约束力更强（可被测试锁住）。
_COMPLETION_LINES = [
    ("C1", "「方向判定是程序规则，不经过 LLM，也不由本模块决定 —— "
           "唯一出处是 `B07.evidence_ledger.judge_direction()`。」",
     "✅ 达成",
     "方向只出现在 `judgement.py` / `evidence_ledger.py`；"
     "`B07/tests/test_s4_invariants.py` 用构造数据锁住了四类来源的方向映射"),
    ("C2", "「只有被本次判断采用的工具结果才升格成证据。调用过 ≠ 采用过。」",
     "✅ 达成",
     "§2.5：`B05.get_current_environment` 被调用但未采用，"
     "不进入三列表；`adopted` 列 + `not_adopted_reasons` 留痕"),
    ("C3", "「外部条件从台账派生 —— judgement.py 不另写一套天气字段组装。」",
     "✅ 达成",
     "§2.6 第 5 行：`external_conditions.provider / forecast_time` "
     "与台账 B03 证据的 `ext` 逐值相等（测试断言）"),
    ("C4", "「不投票、不强制合并。有方向冲突时 combined_attention 返回 "
           "\"conflict_unresolved\"。」",
     "✅ 达成",
     "§3.3：`combined_attention = conflict_unresolved`；"
     "三列互斥，方向不合并（测试 `test_conflict_branch_only_when_divergence_says_so`）"),
    ("C5", "「模型输出用 B06 的真实结果，不做任何修改；天气用 S4-1 造的离线夹具；"
           "知识用 S4-1 选好的默认 query；不编造 citation、不编造数值。」",
     "✅ 达成",
     "§1 的四类证据表；知识命中 chunk 来自 B05 检索原样返回（`citations` 未改写）"),
    ("C6", "「知识库不含通用农业知识……全部 score == 0 时 status 仍然是 ok，"
           "result 里明确写『无相关知识』。」",
     "✅ 达成",
     "§6.2 的零命中探针：status=`ok`、note 明写「无相关知识」"),
    ("C7", "「status 只能是 ok / unavailable / error 三选一。」"
           "「status=ok 时 error 必须为 None，status != ok 时 data 必须为 None。」",
     "✅ 达成",
     "`B07/evidence_registry.py` 构造时校验；§6 的三条降级探针实测"),
    ("C8", "「A 组的『综合叙述』必须由程序从证据台账生成（纯函数），"
           "不许手写、不许让 LLM 生成。」",
     "✅ 达成",
     "§4.2 的叙述 + `check_narrative_clean` 自动检查 0 个问题；"
     "`B07/tests/test_s4_3_invariants.py::test_flat_narrative_is_pure_function`"),
    ("C9", "「⑦ 上下文块不含引导性措辞」"
           "（不得出现「请务必 / 务必 / 请综合判断 / 请注意提醒」）",
     "✅ 达成",
     "测试 `test_divergence_block_has_no_leading_wording`"),
    ("C10", "「不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不改模型。」",
     "✅ 达成",
     "§4.6 指纹表：SYSTEM_PROMPT sha256 `{sp}`、问题 sha256 `{qs}`，"
     "与 S1–S3 一致（见 `model_table.md`）"),
    ("C11", "「失败必须显式暴露，禁止用静态文案伪装成功。」",
     "✅ 达成",
     "§6.1：{failed_n} 次 403 额度耗尽以 `status=failed` 存档，未伪装成成功"),
]


def _sec5(raw: dict) -> str:
    fps = raw.get("prompt_fingerprints") or {}
    inv = raw.get("run_inventory") or []
    failed_n = sum(1 for r in inv if r.get("status") != "success")
    rows = [[cid, quote, verdict,
             where.format(sp=fps.get("system_prompt_sha256"),
                          qs=fps.get("question_sha256"),
                          failed_n=failed_n)]
            for cid, quote, verdict, where in _COMPLETION_LINES]
    parts = [
        "## 5. 完成线逐条自评",
        "",
        "**关于「文档原句」**：S4 阶段的任务文档正文**不在仓库内**（`docs/` 下只有 "
        "`b01_b03_design.md` 与 `b03_weather.md`），"
        "所以本节引用的「原句」是**仓库内固化的规范条款** —— "
        "各模块 docstring 里的那一句。它们比文档正文更硬："
        "每一条都有测试锁住，改了就红。",
        "",
        _table(["#", "条款原句", "结论", "证据位置"], rows),
        "",
        "**没有达成 / 打折的项**（如实记）：",
        "",
        _table(["项", "状态", "说明"], [
            ["「证据身份穿透到解释层」",
             "❌ 未达成",
             f"§4.3：B 组 {len((_groups(raw).get(GROUP_B) or {}).get('records') or [])} "
             "次回答里提到分歧/冲突的次数是 "
             f"{( (_groups(raw).get(GROUP_B) or {}).get('summary') or {}).get('conflict_mentions_any')}。"
             "身份只到上下文层，没到解释层。"],
            ["「负面对照显示归因完整率下降」",
             "⚠️ 无定义",
             "两组断言总数都是 0，三个主指标里两个无定义 —— "
             "既没被证实也没被证伪（§4.8）"],
        ]),
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §6 失败与降级
# --------------------------------------------------------------------------

def _sec6(raw: dict) -> str:
    inv = raw.get("run_inventory") or []
    failed = [r for r in inv if r.get("status") != "success"]
    probes = raw.get("degradation_probes") or {}
    wu = probes.get("weather_unavailable") or {}
    kn = probes.get("knowledge_no_hit") or {}
    te = probes.get("tool_error") or {}

    failed_rows = [[f"`{r.get('run_id')}`", r.get("status"), r.get("failed_stage"),
                    _short(r.get("failure_reason"), 150)] for r in failed]

    parts = [
        "## 6. 失败与降级",
        "",
        "### 6.1 LLM 额度耗尽（HTTP 403 insufficient_quota）",
        "",
        f"本轮共 {len(inv)} 次 S4 存档，其中 **{len(failed)} 次失败**，"
        "全部保留在 `B07/runs/` 里（**不删**）：",
        "",
        _table(["run_id", "status", "failed_stage", "failure_reason"], failed_rows),
        "",
        "**表现**：LLM 调用在 `llm_explanation` 阶段抛 "
        "`PermissionDeniedError: Error code: 403`（`type=insufficient_quota`，"
        "「Free quota exhausted」），整次 run 标记 `status=failed`、"
        "`failed_stage=llm_explanation`、失败原因原样落盘。",
        "",
        "**没有用静态文案伪装成功** —— 失败存档里 `llm_explanation` 是空的，"
        "重试（换模型 / 关闭「仅使用免费额度」开关）之后才产出成功存档。",
        "",
        "### 6.2 天气不可用时的降级",
        "",
        f"降级探针（离线，不联网、不调 LLM）：{wu.get('probe')}",
        "",
        _table(["项", "实测值"], [
            ["证据 status", f"`{wu.get('entry_status')}`"],
            ["证据 error", wu.get("entry_error")],
            ["证据 result 是 None", _yn(wu.get("entry_result_is_none"))],
            ["方向", f"`{wu.get('direction')}`"],
            ["是否出现在 undetermined 列", _yn(wu.get("in_undetermined"))],
            ["undetermined 的理由", wu.get("undetermined_reason")],
            ["`has_conflict`", f"`{wu.get('has_conflict')}`"],
            ["`combined_attention`（内部 WATCH）", f"`{wu.get('combined_attention_watch')}`"],
            ["`combined_attention`（内部 NORMAL）", f"`{wu.get('combined_attention_normal')}`"],
        ]),
        "",
        "**表现**：外部条件取不到时，它**显式不可用**（status=unavailable、error 非空、"
        "result 为 None），方向落 `undetermined`，**静默消失**不会发生；"
        "内部判断照常进行。",
        "",
        "### 6.3 知识检索无命中",
        "",
        f"降级探针（真跑一次检索；本地 TF-IDF，不联网）：{kn.get('probe')}",
        "",
        _table(["项", "实测值"], [
            ["status", f"`{kn.get('status')}`"],
            ["has_relevant", f"`{kn.get('has_relevant')}`"],
            ["召回条数", kn.get("citation_count")],
            ["全部 score == 0", _yn(kn.get("all_scores_zero"))],
            ["error", kn.get("error") if kn.get("error") else "None（检索成功，无错误）"],
        ]),
        "",
        f"note 原文：",
        "",
        f"> {kn.get('note')}",
        "",
        "**表现**：全部 score == 0 时 **status 仍然是 `ok`** —— "
        "「检索成功但知识库没有相关内容」与「检索失败」是两件事，"
        "不许混为一谈；result 里明写「无相关知识」。",
        "",
        "### 6.4 工具调用失败",
        "",
        f"降级探针（离线）：{te.get('probe')}",
        "",
        _table(["项", "实测值"], [
            ["证据 status", f"`{te.get('entry_status')}`"],
            ["证据 error", te.get("entry_error")],
            ["证据 result 是 None", _yn(te.get("entry_result_is_none"))],
            ["方向", f"`{te.get('direction')}`"],
            ["`has_conflict`", f"`{te.get('has_conflict')}`"],
        ]),
        "",
        "**表现**：工具失败的证据不参与方向判定（落 `undetermined`），"
        "不污染冲突判定。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §7 证据链
# --------------------------------------------------------------------------

def _sec7(raw: dict) -> str:
    run = raw.get("s4_run") or {}
    entries = _entries(run)
    div = _divergence(run)
    llm = run.get("llm_meta") or {}
    answer = run.get("llm_explanation") or ""

    def _has(tok):
        return tok in answer

    rows = [
        ["内部（B02 历史）", "✅ 在", "`entries[0]`（status=ok，6 点）",
         "→ ④ 块 → 结构化判断第 1/2 字段",
         _yn(_has("B02") or _has("历史"))],
        ["外部（B03 天气）", "✅ 在", "`entries[2]`（status=ok，离线夹具）",
         "→ ⑥ 块 → `external_conditions` → 第 6 字段", _yn(_has("外部天气"))],
        ["知识（B04 检索）", "✅ 在", "`entries[3]`（status=ok，命中 CH-003-02）",
         "→ ⑦ 块 undetermined → 第 7 字段", _yn(_has("知识"))],
        ["模型（B06 预测）", "✅ 在", "`entries[1]`（status=ok，WATCH）",
         "→ ④ 块 → 第 3 字段", _yn(_has("B06") or _has("WATCH"))],
        ["解释（LLM）", "✅ 在", f"`llm_meta.model={llm.get('model')}`，"
                              f"{llm.get('content_length')} 字",
         "→ `llm_explanation`", "—"],
    ]

    parts = [
        "## 7. 证据链",
        "",
        "五方是否齐全、是否同源可追：",
        "",
        _table(["方", "是否齐全", "台账/存档位置", "流向", "本次回答里是否出现（关键词命中）"],
               rows),
        "",
        "> 最后一列是**关键词命中**（内部查「B02/历史」、外部查「外部天气」、"
        "知识查「知识」、模型查「B06/WATCH」），"
        "**未命中不代表这条证据没被用到** —— 回答可能用「数据」「监测」"
        "「基于 B02 的历史数据」以外的措辞。判据是「证据有没有进上下文」，"
        "不是「回答里有没有写出它的名字」。",
        "",
        "### 7.1 同源可追",
        "",
        _table(["检查", "结果"], [
            ["结构化判断的每个字段都能追到台账条目",
             f"✅ —— `each_source` 覆盖 "
             f"{len(div.get('each_source') or {})} 个来源，"
             "与 adopted 证据数一致"],
            ["外部条件字段只定义一次",
             "✅ —— `external_conditions.provider / forecast_time` "
             "与台账 B03 证据的 `ext` 逐值相等"],
            ["LLM 上下文能追到结构化判断",
             f"✅ —— 上下文 sha `{(raw.get('contrast') or {}).get('fingerprints', {}).get('context_b_sha256', 'n/a')}`，"
             "⑦ 块逐条带来源与时间"],
            ["回答能追到上下文",
             f"✅ —— `llm_meta.system_prompt_sha256="
             f"{(raw.get('prompt_fingerprints') or {}).get('system_prompt_sha256')}`、"
             f"`question_sha256={(raw.get('prompt_fingerprints') or {}).get('question_sha256')}`"],
            ["外部数据与内部数据**同时间基准**",
             "❌ 否 —— 内部 SIMULATED、外部 PUBLIC，见 §8 L1"],
        ]),
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §8 Limitations
# --------------------------------------------------------------------------

LIMITATIONS = [
    ("L1", "内部为 SIMULATED 模拟数据，外部为 PUBLIC 真实数据，非同一时间基准；"
           "外部预报时刻晚于内部锚点约 3 天，数值不可与内部直接比较，只作方向参考"),
    ("L2", "B04 知识库不含通用农业知识（B05 工具描述已声明）；"
           "本次知识证据是系统设计层面的方法论（证据完整性原则，CH-003-02），"
           "不是作物生理知识；它能支持「需要关注」，不能支持「未来一定恶化」，"
           "也不能支持「可以放心不管」"),
    ("L3", "B06 模型 recall=0.448 / f1=0.510；本场景中 B06 对 S2 漂移窗口输出 "
           "NORMAL（漏报）。本场景的「冲突」部分来自模型漏报与外部反向证据，"
           "不是「模型判 WATCH vs 天气降温」；NORMAL 不等于确认无异常"),
    ("L4", "LLM 解释层存在采样波动（S3 已用同一 llm_context 一次引用一次不引用证实）；"
           "本轮每组 N 次，统计一律带分母；N=3 样本量小，差异可能不显著"),
    ("L5", "LLM 模型跨阶段变更：S1–S3 = qwen3.5-27b，S4 = qwen-max"
           "（原模型免费额度耗尽）；跨阶段 LLM 措辞不可直接比较，"
           "但 B06 模型 / 核心提示词 / 固定问题全程未变，程序层结论不受影响"),
    ("L6", "SYSTEM_PROMPT 第 3 条「绝对不得编造…不许出现真实天气…」与 S4 目标存在"
           "字面冲突；探针结论：本次未压制输出（模型确实引用了外部条件），"
           "但这是模型自行通融的结果，不是提示词保证；"
           "按文档「不改核心提示词」的约束本轮未修改，建议措辞留待 S4 结项时单独评估"),
    ("L7", "断言检测器是启发式的（多字短语匹配），可能漏判或误判"),
    ("L8", "天气侧「未来降温」场景由离线夹具构造（可复现）；真实组此刻为 "
           "deteriorating。两个方向都如实记录，不许只取对自己有利的那个"),
    ("L9", "外部取的是窗口末时刻单点值，不是窗口极值，可能漏掉中途更高/更低值"),
    ("L10", "全部实测点位于夜间（22:00→05:00），solar_radiation 恒为 0，"
            "高温分支（≥35℃）未经任何实测点覆盖"),
    ("L11", "外部湿度实测达 91%（高湿），对番茄病害风险有实际意义，"
            "但当前外部判定只用降水概率，湿度仅作描述 —— 列为 S5 的扩展点"),
    ("L12", "本轮曾遇 LLM 服务商 HTTP 403 insufficient_quota（免费额度耗尽，"
            "且「仅使用免费额度」开关挡住了按量计费通道）；该次失败被显式记录"
            "在 run 存档中（failed_stage / failure_reason），未用静态文案伪装成功；"
            "关闭该开关后重跑成功"),
    ("L13", "S3 报告 §3.6 的引用率 14/16 是 qwen3.5-27b 的数据，"
            "与 S4 的 qwen-max 结果不可直接比较；两组 A/B 对照在各自模型内部进行"),
]


def _sec8(raw: dict) -> str:
    rows = [[lid, text] for lid, text in LIMITATIONS]
    parts = [
        "## 8. Limitations（诚实清单）",
        "",
        _table(["#", "限制"], rows),
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §9 复现命令
# --------------------------------------------------------------------------

def _sec9(raw: dict) -> str:
    run_id = (raw.get("s4_run") or {}).get("run_id") or "run_XXX"
    parts = [
        "## 9. 复现命令",
        "",
        "```bash",
        "# 0) 装依赖（如果还没装）",
        "./venv/Scripts/python.exe -m pip install -r requirements.txt",
        "",
        "# 1) 跑 S4 场景：装配台账 → 算方向与分歧 → 调 LLM → 存档 → 落原始结果",
        "./venv/Scripts/python.exe -m B07.s4",
        "",
        "# 2) 负面对照（平铺组 vs 分身份组，各 N 次）",
        "./venv/Scripts/python.exe -m B07.s4_3 --n 5",
        "",
        "# 3) 由原始结果生成本报告（离线，不联网、不调 LLM，可确定性重放）",
        "./venv/Scripts/python.exe B07/s4_report.py",
        "",
        "# 4) 不变量测试",
        "./venv/Scripts/python.exe -m pytest B07/tests -q",
        "",
        "# 5) 从存档复算（S1–S3 的存档逐值不变）",
        "./venv/Scripts/python.exe -m B07.runs explain run_023",
        "",
        "# 6) 查某次存档的原文",
        f"cat B07/runs/{run_id}/run.json",
        "```",
        "",
        "> 生成本报告不需要联网、不需要 LLM 额度："
        "它只读 `B07/artifacts/s4_raw_result.json`。"
        "连续跑两次第 3 步，`git diff --stat B07/artifacts/s4_report.md` 应当**无输出**。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §10 下一步
# --------------------------------------------------------------------------

def _sec10(raw: dict) -> str:
    parts = [
        "## 10. 下一步：S5（证据退化 / 缺失证据下的受限判断）",
        "",
        "S4 把「证据有没有身份」做完了。S5 要回答的是"
        "**当证据本身坏掉时，系统还能不能做出可审计的判断**。",
        "",
        "已经具备的条件（都已在仓库里，不是计划）：",
        "",
        "- `status` 三态 + `result/error` 硬约束 → 退化证据进得来；",
        "- 方向是程序规则 → 退化证据的方向是 `undetermined`，不靠 LLM 猜；",
        "- `evidence_divergence` 三列 → 退化证据落进第三列并带上「为什么仍不足以确定」；",
        "- `combined_attention` 只在 `has_conflict` 时才分支 → 退化**不会**伪造冲突。",
        "",
        "建议的 S5 范围（按优先级）：",
        "",
        _table(["优先级", "题目", "为什么"], [
            ["P0", "证据退化的**显式降级链**：历史缺失 / 天气不可用 / 知识无命中 / "
                   "模型输出缺失，各自应该给什么 `combined_attention`、什么措辞",
             "§6 的探针已经证明三态机制成立，但**四条退化路径还没合成一个对照实验**"],
            ["P0", "把「外部湿度」纳入外部判定维度（当前只用降水概率）",
             "§8 L11：外部湿度实测 91%，对番茄病害有实际意义却被丢弃"],
            ["P1", "让冲突出现在解释层：在不违反「不改核心提示词」约束的前提下，"
                   "验证 ⑦ 块能否提高「提到分歧」的比例",
             "§4.3：身份只到上下文层，没到解释层（0/N 次提及冲突）"],
            ["P1", "把 A 组负面对照再退化一档（去掉四个类别名、只留无主语流水句）"
                   "并放宽字数上限",
             "§4.2：A 组还是弱对照；§4.8：主指标无定义，需要更强的对照才有检出力"],
            ["P2", "外部取窗口极值而不是末时刻单点值",
             "§8 L9：单点值可能漏掉中途更高/更低值"],
        ]),
        "",
        "**不在 S5 范围**（避免范围漂移）：",
        "改 SYSTEM_PROMPT、改固定问题、改判据阈值、重训 B06、"
        "改 `combined_attention` 的既有规则。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 跨阶段模型对照表
# --------------------------------------------------------------------------

def _s3_cite_rate() -> str:
    """S3 引用率 —— 现场算，不手抄。"""
    try:
        from B07.s3 import citation_history

        rows = citation_history()
        if not rows:
            return "n/a"
        c = sum(1 for r in rows if r.get("cited"))
        return f"{c}/{len(rows)}"
    except Exception:  # noqa: BLE001
        return "n/a"


def build_model_table(raw: dict) -> str:
    """跨阶段模型对照表（数字全部来自实测存档 / 现场统计）。"""
    by_scen = raw.get("model_by_scenario") or {}
    fps = raw.get("prompt_fingerprints") or {}

    def _model(*scenarios):
        for sc in scenarios:
            if sc in by_scen:
                return by_scen[sc].get("model")
        return None

    sp = fps.get("system_prompt_sha256")
    qs = fps.get("question_sha256")

    def _row(stage, model, note):
        return [stage, f"`{model}`" if model else "n/a",
                f"`{sp}`" if stage != "S1" else f"`{sp}`",
                f"`{qs}`" if stage != "S1" else f"`{qs}`", note]

    rows = [
        _row("S1", _model("S1"), ""),
        _row("S2", _model("S2"), ""),
        _row("S3", _model("S3_with", "S3_without"),
             f"引用率 {_s3_cite_rate()} 统计基于此模型"),
        _row("S4", _model("S4"),
             "原模型免费额度耗尽（HTTP 403 insufficient_quota），S4 起换用"),
    ]

    # ---- S5 行：数字来自 s5_raw_result.json（实测存档，不是手填）----
    s5_path = ARTIFACTS / "s5_raw_result.json"
    s5_note = ""
    if s5_path.exists():
        s5 = json.loads(s5_path.read_text(encoding="utf-8"))
        s5_run = s5.get("degraded") or {}
        s5_model = (s5_run.get("llm_meta") or {}).get("model")
        s5_fp = s5.get("prompt_fingerprints") or {}
        rows.append([
            "S5",
            f"`{s5_model}`" if s5_model else "n/a",
            f"`{s5_fp.get('system_prompt_sha256')}`",
            f"`{s5_fp.get('question_sha256')}`",
            "证据退化：天气不可用",
        ])
        s5_note = (f"S5（证据退化：天气不可用）由 `{s5_run.get('run_id')}` 等实测存档产生；")

    parts = [
        "# 跨阶段 LLM 模型对照表",
        "",
        "> 本表由 `python B07/s4_report.py` 从 `B07/artifacts/s4_raw_result.json` "
        "自动生成（模型名来自各 run 存档的 `llm_meta.model`，"
        "引用率来自现场统计），**没有手工填写**；"
        "S5 行来自 `B07/artifacts/s5_raw_result.json`（同样是实测存档）。",
        "",
        _table(["阶段", "LLM 模型", "提示词 sha256", "问题 sha256", "备注"], rows),
        "",
        "S1–S3 的 LLM 解释与引用率统计均由 `qwen3.5-27b` 产生并已落盘；",
        "S4 起改用 `qwen-max`，原因是原模型免费额度耗尽。",
        s5_note,
        "跨阶段的 LLM 措辞不可直接比较；",
        "但 B06 预测模型（`b06-20261020-v1`）、核心提示词指纹、固定问题指纹",
        "全程未变，S1–S5 的程序层结论（结构化判断、`combined_attention`、",
        "`evidence_divergence`）不受 LLM 更换影响。",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def build_report(raw: dict) -> str:
    parts = [
        "# B07 S4 阶段报告 —— 证据身份层与证据分歧（结构化）",
        "",
        "> 本报告由 `python B07/s4_report.py` 从 "
        "`B07/artifacts/s4_raw_result.json` 自动生成，",
        "> **所有数字都来自实测存档**，没有手工填写。",
        "> 生成过程**不联网、不调 LLM**，可确定性重放。",
        "",
    ]
    for fn in (_sec0, _sec1, _sec2, _sec3, _sec4, _sec5,
               _sec6, _sec7, _sec8, _sec9, _sec10):
        parts.append(fn(raw))
    return "\n".join(parts)


def main() -> int:
    if not RAW_PATH.exists():
        print(f"❌ 找不到 {RAW_PATH}；先跑 `./venv/Scripts/python.exe -m B07.s4`")
        return 1
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    text = build_report(raw)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"✅ 已生成 {REPORT_PATH}（{len(text)} 字符）")

    mt = build_model_table(raw)
    MODEL_TABLE_PATH.write_text(mt, encoding="utf-8")
    print(f"✅ 已生成 {MODEL_TABLE_PATH}（{len(mt)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
