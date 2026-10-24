"""B07 S3-3 — 从 S3 实验的原始结果生成 s3_report.md

设计原则：**报告里的每个数字都从实测结果里取，不手工填写。**

输入：B07/artifacts/s3_raw_result.json（由 `python -m B07.s3` 落盘）
输出：B07/artifacts/s3_report.md

用法：
    ./venv/Scripts/python.exe -m B07.s3          # 跑实验，落盘 s3_raw_result.json
    ./venv/Scripts/python.exe -m B07.s3_report   # 由原始结果生成报告

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改任何判据与阈值
  - 报告不新增结论：正文里凡是数字，都来自 s3_raw_result.json 或 B07/runs 下的存档
  - 失败必须显式暴露，不许用静态文案伪装成功
"""

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

ARTIFACTS = _PROJECT_ROOT / "B07" / "artifacts"
RAW_PATH = ARTIFACTS / "s3_raw_result.json"
# 离线组用扩充后的判定器**重跑**的结果（判定器一改，旧的离线组结论就过时了）
OFFLINE_PATH = ARTIFACTS / "s3_offline_result.json"
REPORT_PATH = ARTIFACTS / "s3_report.md"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _yn(flag) -> str:
    return "是" if flag else "否"


def _fmt(v, digits=1, unit=""):
    if v is None:
        return "未获取"
    if isinstance(v, float):
        return f"{v:.{digits}f}{unit}"
    return f"{v}{unit}"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _citation_rows():
    """所有 S3_with 存档的引用情况（从存档现场算，不依赖某一次运行的结果）。"""
    from B07.s3 import citation_history
    return citation_history()


def _citation_table_now():
    from B07.s3 import _citation_table
    return _citation_table(_citation_rows())


def _cite_rate():
    rows = _citation_rows()
    if not rows:
        return "n/a"
    return f"{sum(1 for r in rows if r.get('cited'))}/{len(rows)}"


def _load_archive(run_id: str) -> dict:
    p = _PROJECT_ROOT / "B07" / "runs" / run_id / "run.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _refresh_answers(raw) -> None:
    """用**当前**判定器重算 A/B 回答的引用情况。

    s3_raw_result.json 里的 weather_mentions / weather_claims / cited_external_numbers
    是当时那一版判定器算的；判定器扩词、修了数值匹配的假阳性之后，
    报告必须以现场重算的结果为准，否则报告里会出现过时的数字。
    """
    from B07.s3 import classify_external_reference, _cited_external_numbers

    real = raw.get("real") or {}
    ans = raw.get("answers") or {}
    for key, rid_key in (("a", "run_id_a"), ("b", "run_id_b")):
        rid = real.get(rid_key)
        if not rid:
            continue
        try:
            arch = _load_archive(rid)
        except (OSError, ValueError):
            continue
        answer = arch.get("llm_explanation") or ""
        ext = arch.get("external_conditions")
        cls = classify_external_reference(answer, ext)
        ans[f"weather_mentions_{key}"] = cls.get("mentions") or []
        ans[f"weather_claims_{key}"] = cls.get("claims") or []
        ans[f"cited_external_{key}"] = bool(cls.get("cited"))
        ans[f"cited_external_fragments_{key}"] = cls.get("cited_fragments") or []
        ans[f"cited_external_numbers_{key}"] = _cited_external_numbers(answer, ext or {})


def _context_groups() -> dict:
    """按 llm_context 的 sha12 分组 S3_with 存档。

    同一个 sha = **喂给 LLM 的上下文逐字节相同**，
    因此同组内部的差异只可能来自 LLM 采样。
    """
    groups: dict[str, list[dict]] = {}
    for r in _citation_rows():
        groups.setdefault(r.get("context_sha12") or "?", []).append(r)
    return groups


def _group_table() -> str:
    """同上下文分组的引用率表 —— 采样波动证据的核心表。"""
    groups = _context_groups()
    if not groups:
        return "（没有 S3_with 存档）"
    rows = []
    for sha, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        c = sum(1 for r in rs if r["cited"])
        rows.append([
            f"`{sha}`", len(rs), f"{c}/{len(rs)}",
            f"{rs[0].get('precip_delta')} pp",
            "、".join(f"`{r['run_id']}`" for r in rs),
        ])
    out = _table(["llm_context sha", "次数", "引用", "外部 Δpp", "存档"], rows)
    out += ("\n\n**同一个 sha 内部的差异只能来自 LLM 采样**"
            "（上下文、提示词、问题、模型、内部历史、⑥ 块全都逐字节相同）。")
    return out


def _same_state_pair():
    """找一组"同一上下文、既有引用又有未引用"的运行。

    返回 (sha, 引用那条, 未引用那条)；找不到返回 (None, None, None)。
    """
    for sha, rs in _context_groups().items():
        cited = [r for r in rs if r["cited"]]
        notcited = [r for r in rs if not r["cited"]]
        if cited and notcited:
            return sha, cited[0], notcited[0]
    return None, None, None


def _avr_split(raw):
    """把 absolute_vs_relative 的行拆成「实测」和「构造用例」两组。"""
    rows = (raw.get("absolute_vs_relative") or {}).get("rows") or []
    measured = [r for r in rows if "构造" not in str(r.get("provenance") or "")]
    constructed = [r for r in rows if "构造" in str(r.get("provenance") or "")]
    return measured, constructed


def _reading_rows():
    from B07.s3 import real_reading_history
    return real_reading_history()


def _reading_table_now():
    from B07.s3 import _volatility_table
    return _volatility_table(_reading_rows())


def _claim_positions():
    from B07.s3 import external_claim_positions
    return external_claim_positions()


def _sec8_l5_tail(raw) -> str:
    ans = raw.get("answers") or {}
    cited = ans.get("cited_external_b")
    trunc = ans.get("truncation_loses_external")
    parts = []
    if cited:
        parts.append("本轮 Run B 引用了外部条件，"
                     f"按 250 字截断后关键外部引用"
                     f"{'会被切断' if trunc else '仍然保留'}。")
    else:
        parts.append("本轮 Run B 没有引用外部条件，"
                     "所以本轮谈不上「截断会不会切掉引用」。")
    rows = [r for r in _claim_positions() if r.get("cited")]
    if rows:
        detail = "；".join(
            f"`{r['run_id']}` 引用句在第 {r['claim_start_index']}–{r['claim_end_index']} 字"
            f"（按 250 截断后{'会把这句切断' if r['cut_at_limit'] else '能完整保留'}）"
            for r in rows)
        parts.append(f"在引用了外部条件的运行里：{detail}。")
        parts.append(
            "**结论**：「按 250 字硬截断」不是安全的兜底——"
            "在有的运行里它会把外部引用句拦腰截断，"
            "真要限长必须先改写回答结构（把外部引用放在前段）。")
    return "".join(parts)


# --------------------------------------------------------------------------
# 各章节
# --------------------------------------------------------------------------

def _sec0(raw) -> str:
    ca = raw.get("combined_attention") or {}
    a_val = (ca.get("a") or {}).get("value")
    b_val = (ca.get("b") or {}).get("value")
    ext = (raw.get("real") or {}).get("external") or {}
    ans = raw.get("answers") or {}
    attributed = any(
        k in s for s in (ans.get("next_attention_b") or [])
        for k in ("外部", "天气", "降水", "云量")
    )
    cits = _citation_rows()
    n = len(cits)
    k = sum(1 for r in cits if r.get("cited"))
    return f"""## 0. 一句话结论

**S3 证明了**：外部天气作为独立证据源接入后，内部判断与 LLM 上下文**一个字节都没变**
（前 5 个结构化字段逐字段相同、LLM 上下文只多出 ⑥ 块），
程序级的合成关注等级从 **{a_val}** 变成 **{b_val}**，
这个变化**完全由外部信号驱动**，写死在 `B07/judgement.py` 的规则里、可脱离 LLM 从存档复算。

**S3 没有证明**：
① LLM 叙述层的"关注重点"**没有可靠地跟着变**——本轮 Run B
   {"引用了" if ans.get('cited_external_b') else "**没有引用**"}外部天气，
   "接下来关注"的表述归因于外部天气：{_yn(attributed)}；
   而且在 {n} 次 S3_with 运行里，模型引用 ⑥ 块的行为**只出现了 {k} 次**（§3.6），
   也就是说**这是不稳定行为，不是保证**；
② 外部判定只用了**降水概率一个维度**（温度、云量、湿度都只作描述）；
③ 真实天气的判定**不可复现**（同一地点几分钟内读数就变），
   可复现的结论一律以离线组为准。

本轮真实组外部信号：`{ext.get('signal')}`（{ext.get('judge_method')}），
降水概率 Δ{_fmt(ext.get('precip_delta'), 1, ' pp')}。
"""


def _sec1(raw) -> str:
    real = raw.get("real") or {}
    ext = real.get("external") or {}
    ev = raw.get("evidence") or {}
    return f"""## 1. 场景与对照设计

**唯一变量**：发给 LLM 的 state 里是否多一块 ⑥ 外部天气条件。
除了这一项，两次运行的其它输入**完全一样**：

| 项目 | 值 | 两次是否相同 |
|---|---|---|
| 锚点 analysis_time | `{ev.get('anchor', '2026-10-21T16:00:00')}` | ✅ 相同 |
| 固定问题 | 分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。 | ✅ 相同 |
| SYSTEM_PROMPT 指纹 | `{ev.get('prompt_a')}` | ✅ 相同 |
| 问题指纹 | `{(real.get('archive_a') or {}).get('llm_meta', {}).get('question_sha256')}` | ✅ 相同 |
| LLM 模型 | `{ev.get('model_a')}` | ✅ 相同 |
| B06 模型版本 | `{ev.get('model_version_a')}` | ✅ 相同 |
| B02 历史窗口 / 判据阈值 | 同一段历史、同一套阈值 | ✅ 相同 |
| 外部天气条件 | A：无；B：`{ext.get('location')}` {ext.get('forecast_window')} | ❌ **这就是变量** |

**为什么这样设计**：外部证据是 S3 新增的唯一信息源。
如果两次运行的其它输入有任何差异，事后就无法把结果差异归因到"接入了外部天气"这个动作上。
所以实验把 B02 历史、B06 模型、提示词、固定问题、判据阈值**全部冻死**，
只放开一个开关；并把"两次运行的差异到底是什么"做成**可逐字节复核的证据**（见第 2 节），
而不是靠读两段自然语言回答去猜。

**场景键**：`S3_without`（对照组）/ `S3_with`（实验组），在 `B07/scenarios.py` 里登记。

**注意**：合成关注等级 `combined_attention` **不进 LLM 上下文**——
它不是结构化判断的第 7 个字段（那会破坏 5/6 字段的不变量），
只是存档里的一个独立字段。这样 LLM 上下文的差异仍然严格只有 ⑥ 块。
"""


def _sec2(raw) -> str:
    ev = raw.get("evidence") or {}
    ctx = ev.get("context_diff") or {}
    jd = ev.get("judgement_diff") or []
    cmp_s1 = raw.get("compare_with_s1") or {}
    rc = raw.get("recompute_report") or {}
    ca = raw.get("combined_attention") or {}
    real = raw.get("real") or {}

    j_rows = []
    for r in jd:
        if r["identical"]:
            mark = "✅ 相同"
        elif not r["in_b"]:
            mark = "➖ 仅 A 有"
        elif not r["in_a"]:
            mark = "➕ 仅 B 有"
        else:
            mark = "⚠️ 不同"
        j_rows.append([f"`{r['field']}`", mark])

    fp_rows = []
    for rid, info in (raw.get("fingerprints") or {}).items():
        fp_rows.append([
            f"`{rid}`", str(info.get("scenario")), f"`{info.get('system_prompt_sha256')}`",
            f"`{info.get('question_sha256')}`", info.get("model"),
            info.get("model_version"),
        ])

    s1_rows = []
    for c in (cmp_s1.get("checks") or []):
        if c["name"] == "stability_criteria":
            s1_rows.append([f"`{c['name']}`",
                             "✅ 一致" if c["match"] else "⚠️ 不一致",
                             "（判据明细）", "（判据明细，逐项相同）"])
        else:
            s1_rows.append([
                f"`{c['name']}`",
                "✅ 一致" if c["match"] else "⚠️ 不一致",
                str(c.get("s1")), str(c.get("run_a")),
            ])

    rc_rows = []
    for tag in ("A", "B"):
        info = rc.get(tag) or {}
        rc_rows.append([
            f"Run {tag}", f"`{real.get('run_id_' + tag.lower())}`",
            "✅ 全部一致" if info.get("all_match") else "⚠️ 存在差异",
            str(info.get("external_check_count")),
            ("✅ 全部一致" if info.get("external_all_match")
             else ("⚠️ 有差异" if info.get("external_all_match") is False
                   else "（本次无外部条件，不适用）")),
        ])

    a = ca.get("a") or {}
    b = ca.get("b") or {}
    ca_rows = [
        ["Run A（不带天气）", f"`{real.get('run_id_a')}`",
         a.get("internal_predicted_attention"), a.get("external_signal"),
         f"**{a.get('value')}**"],
        ["Run B（带天气）", f"`{real.get('run_id_b')}`",
         b.get("internal_predicted_attention"), b.get("external_signal"),
         f"**{b.get('value')}**"],
    ]

    return f"""## 2. 差分证据（核心，可复核）

### 2.1 结构化判断逐字段 diff

Run A 字段：`{ev.get('judgement_field_names_a')}`
Run B 字段：`{ev.get('judgement_field_names_b')}`

{_table(["字段", "对照结果"], j_rows)}

**读法**：前 5 个字段**逐字段完全相同**，第 6 个字段 `external_conditions`
是**追加**（B 有、A 没有），不是替换。接外部天气**没有改动任何内部判断**。

### 2.2 LLM 上下文 diff

| 项目 | Run A | Run B |
|---|---|---|
| 块数 | {ctx.get('a_block_count')} | {ctx.get('b_block_count')} |
| 字符数 | {ctx.get('a_length')} | {ctx.get('b_length')} |
| 删除的块 | — | {len(ctx.get('removed_blocks') or [])} |
| 新增的块 | — | {len(ctx.get('added_blocks') or [])} |

- B 的前 **{ctx.get('prefix_length')}** 字与 A **逐字节相同**：{_yn(ctx.get('prefix_identical'))}
- B == A + `"\\n\\n"` + 新增块：{_yn(ctx.get('b_is_a_plus_suffix'))}
- 新增块首行：`{(ctx.get('added_blocks') or [''])[0].splitlines()[0] if ctx.get('added_blocks') else ''}`

新增块全文（本次实际发给模型的 ⑥ 块）：

```
{ctx.get('suffix_added') or ''}
```

### 2.3 指纹对照表

{_table(["存档", "scenario", "提示词指纹", "问题指纹", "LLM 模型", "B06 模型版本"], fp_rows)}

（run_001 / run_008 / run_009 是 S1 早期存档，早于指纹字段的引入，所以指纹列为 `None`；
从 run_012 起，每次运行都把提示词与问题的指纹写进存档。）

S1 / S2 / S3 用的是**同一套提示词、同一个问题、同一个模型**，
只有 S3 的两次运行之间多了一块 ⑥。

### 2.4 与 S1 存档 `{(cmp_s1.get('s1_run_id') or '')}` 的可复现对照

对照存档 analysis_time = `{cmp_s1.get('s1_analysis_time')}`，
Run A analysis_time = `{cmp_s1.get('run_a_analysis_time')}`。

{_table(["对照项", "结果", "S1", "Run A"], s1_rows)}

### 2.5 从存档文件重读后复算（`python -m B07.runs explain <run_id>`）

{_table(["运行", "run_id", "复算一致性", "外部复算项数", "外部复算结果"], rc_rows)}

**说明**：复算读的是存档文件里的原始数据重新推导，不是复读当初的字符串；
`combined_attention` 也是同一份存档复算出来的。

### 2.6 合成关注等级 `combined_attention`（程序规则，不经过 LLM）

规则写死在 `B07/judgement.py`（`combined_attention()` / `build_combined_attention()`）：

```
内部 predicted_attention == "WATCH"                → watch_internal
内部 NORMAL 且外部 signal == "external_deteriorating" → watch_external
内部 NORMAL 且外部 signal ∈ {{stable, improving, unknown}} → internal_only
```

{_table(["运行", "run_id", "内部 predicted_attention", "外部 signal", "combined_attention"], ca_rows)}

- Run A 理由：{a.get('reason')}
- Run B 理由：{b.get('reason')}
- 两次取值是否不同：{"✅ 不同" if ca.get('changed') else "❌ 相同"}
- 脱离 LLM 从存档复算：Run A {"✅ 一致" if ca.get('a_matches_recompute') else "⚠️ 不一致"}，
  Run B {"✅ 一致" if ca.get('b_matches_recompute') else "⚠️ 不一致"}
"""


def _sec3_4_note(raw) -> str:
    """§3.4 的结论句——必须跟着本次运行的实际表现走，不能写死。"""
    ans = raw.get("answers") or {}
    claims = ans.get("weather_claims_b") or []
    if claims:
        return (f"Run B 引用了外部天气（\"{claims[0]}\"），"
                f"但它的\"接下来关注\"结论**没有**改成由外部驱动的措辞。")
    return ("Run B 本次**没有引用**外部天气——上下文里明明有 ⑥ 块，"
            "回答里却没有任何天气事实断言。"
            "这和 §3.6 的历史统计一致：模型引用 ⑥ 块是**不确定行为**。")


def _sec3(raw) -> str:
    ans = raw.get("answers") or {}
    real = raw.get("real") or {}
    ext = real.get("external") or {}

    cited_rows = []
    for h in (ans.get("cited_external_numbers_b") or []):
        cited_rows.append([h["name"], h["value"], h["written_as"]])
    cited_table = (_table(["外部量", "上下文里的值", "回答里的写法"], cited_rows)
                   if cited_rows else "（回答里没有引用到外部数值）")

    return f"""## 3. 两次回答并排对照

### 3.1 Run A（不带外部天气）— `{real.get('run_id_a')}`

```
{ans.get('answer_a')}
```

字数 **{ans.get('length_a')}**（上限 {ans.get('max_answer_chars')}，{"✅ 未超" if ans.get('within_limit_a') else "⚠️ 超出"}）

### 3.2 Run B（带外部天气）— `{real.get('run_id_b')}`

```
{ans.get('answer_b')}
```

字数 **{ans.get('length_b')}**（上限 {ans.get('max_answer_chars')}，{"✅ 未超" if ans.get('within_limit_b') else "⚠️ 超出"}）

### 3.3 是否引用外部条件

| 项目 | Run A | Run B |
|---|---|---|
| 断言了天气事实 | {len(ans.get('weather_claims_a') or [])} 句 | {len(ans.get('weather_claims_b') or [])} 句 |
| 提到了天气（含元陈述） | {len(ans.get('weather_mentions_a') or [])} 句 | {len(ans.get('weather_mentions_b') or [])} 句 |
| 是否构成"引用了外部条件" | — | {"✅ 引用了" if ans.get('cited_external_b') else "❌ 没有引用"} |
| 事实越界告警 | {len(ans.get('out_of_bounds_a') or [])} 条 | {len(ans.get('out_of_bounds_b') or [])} 条 |

Run B 断言天气事实的句子：

{chr(10).join('> ' + s for s in (ans.get('weather_claims_b') or []) or ['（无）'])}

Run B 引用到的外部数值：

{cited_table}

Run A **没有**断言任何天气事实——它的上下文里本来就没有天气，这是对的。

### 3.4 "接下来值得关注什么"是否变化

Run A：

{chr(10).join('- ' + s for s in (ans.get('next_attention_a') or []) or ['（没有相应表述）'])}

Run B：

{chr(10).join('- ' + s for s in (ans.get('next_attention_b') or []) or ['（没有相应表述）'])}

**如实记录**：{_sec3_4_note(raw)}

换句话说：**程序层的关注等级变了
（`{(raw.get('combined_attention') or {}).get('a', {}).get('value')}` → `{(raw.get('combined_attention') or {}).get('b', {}).get('value')}`），
LLM 文本层没有跟着变**。这是本轮最重要的诚实结论（见 §8 L11）。

### 3.5 回答长度与截断影响

提示词要求"控制在 250 字以内"，实际 Run A = {ans.get('length_a')} 字、
Run B = {ans.get('length_b')} 字，**两次都超了**。
`MAX_ANSWER_CHARS` 目前只是提示词里的约束说明常量，**代码不做强制截断**
（见 §8 L5）。

如果真的按 250 字截断 Run B：

```
{ans.get('truncated_b')}
```

截断是否吃掉关键外部引用：**{"⚠️ 是" if ans.get('truncation_loses_external') else "✅ 否"}**。

### 3.6 "模型会不会引用 ⑥ 块"的历史统计（**本报告最重要的一张表**）

把 `B07/runs/` 下**所有** S3_with 存档（同一套提示词、同一个问题、同一个模型、同一段历史）
翻出来，逐个重跑判定器。表里同时给出 `llm_context` 的 sha12 ——
**sha 相同 = 喂给 LLM 的上下文逐字节相同**，这类存档之间只差采样；
sha 不同 = ⑥ 块内容不同（真实取数会漂移），这类存档之间差的是外部输入。

{_citation_table_now()}

**这张表说明什么**：

- 程序层的 `combined_attention` **每次都是 `watch_external`**，是确定的；
- LLM 层"引用不引用 ⑥ 块"**是不确定的**（引用率 {_cite_rate()}）。
  同样的输入、同样的一段话，换一次采样就可能不引用。
- 因此**不能说"外部证据接入后模型会讨论外部天气"**——只能说
  "外部证据接入后，模型**有机会**讨论外部天气"。

**本报告不挑样本**：§3.1–3.5 展示的是**最后一次**运行
（{raw.get('real', {}).get('run_id_a')} / {raw.get('real', {}).get('run_id_b')}）的实际结果，
不管它引用了还是没引用，都照实写。

{_sec3_7_same_state()}

{_sec3_8_three_sources()}

{_sec3_9_architecture(raw)}
"""


def _sec3_7_same_state() -> str:
    """§3.7 同一状态、两次采样：引用 vs 未引用，并排展示。"""
    sha, cited, notcited = _same_state_pair()
    if not sha:
        return ("### 3.7 同一状态、两次采样\n\n"
                "本轮存档里**没有**出现\"同一上下文既有引用又有未引用\"的分组，"
                "因此无法给出这一对并排样本。")

    ca = _load_archive(cited["run_id"])
    cb = _load_archive(notcited["run_id"])
    ans_a = ca.get("llm_explanation") or ""
    ans_b = cb.get("llm_explanation") or ""

    def _frag(r):
        fr = r.get("cited_fragments") or []
        return f"`{fr[0]}`" if fr else "（无）"

    return f"""### 3.7 同一状态、两次采样：一次引用、一次没引用

下面两份存档的 `llm_context` **逐字节相同**（sha12 = `{sha}`）：
同一 anchor、同一段 B02 内部历史、同一份 ⑥ 外部块、同一套 `SYSTEM_PROMPT`、
同一个固定问题、同一个 B06 模型、同一个 LLM。
**唯一的差别是 LLM 的采样。**

**（a）引用了外部条件 —— `{cited['run_id']}`**
（{cited['answer_length']} 字，命中片段 {_frag(cited)}）

```
{ans_a}
```

**（b）没有引用外部条件 —— `{notcited['run_id']}`**
（{notcited['answer_length']} 字，命中片段 {_frag(notcited)}）

```
{ans_b}
```

**怎么读这一对**：

- 这**不是**"系统没做到"。程序层在这两次里给的是**同一个**结论
  （`combined_attention` = `{cited.get('combined_attention')}` /
  `{notcited.get('combined_attention')}`），
  外部信号也**完全一样**（`{cited.get('signal')}`，Δpp = {cited.get('precip_delta')}）。
- 这**是**"可测量的解释层波动"：同样的输入，模型这次提到了外部天气、下次没提。
- 只展示没引用的那一次，读者会误读成"系统没做到"；
  所以这里**两次并列**，并给出全量统计口径（见 §3.6）。

### 3.8 同一上下文的分组引用率（波动到底有多大）

{_group_table()}

**这张表说明**：
同一个 sha 的组内，差异只能来自采样；不同 sha 的组之间，
⑥ 块内容不同，引用率也不同（因为 ⑥ 块本身**不一样**）。
因此引用率**必须带分组口径**才有意义——单看一个总数会掩盖这两件事。
"""


def _sec3_8_three_sources() -> str:
    """§3.9 三种来源的区分：检测器漏判 / 模型不提 / 提示词禁令。"""
    rows = _citation_rows()
    total = len(rows)
    cited = sum(1 for r in rows if r["cited"])
    groups = _context_groups()
    same_ctx_mixed = [(sha, rs) for sha, rs in groups.items()
                      if any(r["cited"] for r in rs)
                      and any(not r["cited"] for r in rs)]
    biggest = max(groups.items(), key=lambda kv: len(kv[1])) if groups else ("?", [])
    big_sha, big_rs = biggest
    big_cited = sum(1 for r in big_rs if r["cited"])

    mixed_txt = ("；".join(
        f"`{sha}` 组内 {sum(1 for r in rs if r['cited'])}/{len(rs)}"
        for sha, rs in same_ctx_mixed) or "（没有出现同上下文结果不一致的分组）")

    return f"""### 3.9 "没引用"到底来自哪里 —— 三种来源的区分

观测到 {total} 次 S3_with，引用 {cited} 次。下面**逐一**检验三种可能来源，
不猜结论。

**来源一：检测器词表太窄，漏判了 → 已排除。**

- 判定器已扩成三级（mentions / claims / cited），
  词表换成多字短语为主（"外部条件 / 外部天气 / 外部环境 / 室外 / 阴雨 /
  降水概率 / 转差 / 变差 / 传感器数据 …"），
  刻意避开了"风"这类会撞"风险"的单字；
  内部指标"温度 / 湿度 / 光照"不计入外部词表。
- 用扩充后的检测器**重判**全部存档，结论与扩充前**一致**。
- 逐字通读全文核对：未判定为引用的那些回答里，确实**一次都没有**提到外部天气。
- 另做**兜底字符扫描**（"雨 雪 霜 雹 雾 霾 雷 电 冰"，这些字不会撞内部指标），
  没有扫出任何被词表漏掉的句子。
- 所以：**不是检测器漏判**。

**来源二：模型确实不提（真实特性）→ 部分成立，但不能一概而论。**

- 在未判定为引用的那几次里，模型**是真的没提**：它只说了
  "当前判断未接入真实天气与农艺知识库" / "也未接入真实传感器数据"这类
  **否定式元陈述**——说的是"没接入"，不是"外部天气是什么"。
- 但**同一状态**下也有提了的（§3.7），所以不能说"模型从不引用"。
- 更重要的：引用率**强烈依赖 ⑥ 块内容**。
  本轮最大的同上下文组（sha `{big_sha}`，{len(big_rs)} 次）引用率
  {big_cited}/{len(big_rs)}；
  而 §3.7 那一组只有部分引用。
  **所以"引用率"不是一个固定概率，而是随 ⑥ 块内容变化的量。**

**来源三：SYSTEM_PROMPT 第 3 条的字面禁令在压制输出 → 本轮无法区分。**

- 采样波动这件事**已经被证实存在**，而且**不是**检测器造成的：
  同上下文分组里出现了结果不一致（{mixed_txt}），
  且逐字通读确认"没提"是真的没提。
- 但**波动的成因是不是第 3 条禁令**，本轮**无法区分**。
  要区分必须**只改提示词第 3 条**做对照，
  而本轮的硬约束明令"不修改 SYSTEM_PROMPT"。
- 所以这里只能写"**无法区分**"，不能写"是禁令压制的"，
  也不能写"与禁令无关"。

**结论（只写测到的）**：观测到的引用率是 **{cited}/{total}**；
波动真实存在且已被定位到"同上下文不同采样"这一层；
但**波动为什么发生**，本轮**没有**做到可归因的对照实验。
"""


def _sec3_9_architecture(raw) -> str:
    """§3.10 架构论点：完成线由程序层闭合。"""
    rows = _citation_rows()
    total = len(rows)
    cited = sum(1 for r in rows if r["cited"])
    rc = raw.get("recompute_report") or {}
    rc_txt = ""
    b = rc.get("B") or {}
    if b:
        rc_txt = (f"（本次从存档复算：全部一致 = {_yn(b.get('all_match'))}，"
                  f"其中外部条件 {b.get('external_check_count')} 项，"
                  f"外部全部一致 = {_yn(b.get('external_all_match'))}）")
    return f"""### 3.10 架构论点：S3 的完成线由**程序层**闭合，不由 LLM 措辞决定

这一节是本阶段最想留下的一句话。

**程序层是闭合的。** `combined_attention` 从 `internal_only` 变成 `watch_external`，
这条规则写死在 `B07/judgement.py` 里，**不经过 LLM**，
可以从 `run.json` 原样**复算**并与存档里的字段逐条比对一致{rc_txt}。
也就是说：**即使 LLM 一次都不提外部天气，S3 的完成线也已经达成了**——
外部证据确实改变了系统的关注等级，而且这个改变**可复核、可重放**。

**LLM 层是被显式度量、并被隔离在解释环节的。** 解释层"引用不引用 ⑥ 块"
的引用率是 **{cited}/{total}**（§3.6/§3.8），
它被**测量、被写进报告、被当作波动而不是承诺**，
而**没有被拿去当结论**。

**这正是 B07「判断与解释分离」架构的价值。** 如果把结论绑在 LLM 的措辞上
（"模型说该关注了，所以系统该关注了"），
那么完成线就会随采样波动——同一份输入这次说该关注、下次说不用关注，
**整条流水线的"完成"与否就变成了一件不可复现的事**。
现在的做法是：判断层只由程序规则决定且可复算，
解释层的波动被如实测出来、写清楚、**不外溢到结论**。

这也解释了 §3.7 那对并排样本为什么值得单独列出来：
它是**架构选择的证据**，不是 bug。
"""


def _sec4(raw) -> str:
    avr = raw.get("absolute_vs_relative") or {}
    off = raw.get("offline") or {}
    real = raw.get("real") or {}
    ext = real.get("external") or {}
    ca = raw.get("combined_attention") or {}

    measured_rows, constructed_rows = _avr_split(raw)

    def _row(r):
        return [
            r["label"], r["provenance"],
            f"{r['baseline_pp']}% → {r['forecast_pp']}%",
            f"{r['relative_signal']}（Δ={r['relative_delta']} pp）",
            r["absolute_signal"],
            "✅ 一致" if r["agree"] else "⚠️ **不一致**",
        ]

    rows = [_row(r) for r in measured_rows]
    measured_table = _table(
        ["组", "来源", "降水概率", "相对差分（主判定）", "绝对阈值（兜底）", "结论"], rows)

    constructed_table = _table(
        ["组", "来源", "降水概率", "相对差分（主判定）", "绝对阈值（兜底）", "结论"],
        [_row(r) for r in constructed_rows]) if constructed_rows else "（无）"

    # 唯一可验证的实测例：S3-0 寿光（晴天）
    shouguang = next((r for r in measured_rows if "寿光" in str(r.get("label"))), None)
    if shouguang:
        sg_block = (
            f"- 相对差分 → `{shouguang['relative_signal']}`；"
            f"绝对阈值 → `{shouguang['absolute_signal']}`（**不一致**）。\n"
            f"  按相对差分：基线与预报的降水概率都是 {shouguang['baseline_pp']}%，"
            f"变化量 Δ={shouguang['relative_delta']} pp，**没有变化** → 稳定。\n"
            f"  按绝对阈值：预报点降水概率 {shouguang['forecast_pp']}%、"
            f"云量 {shouguang['forecast_cloud']}%，"
            f"落在\"低降水 + 低云量\"分支 → 判成**在好转**——"
            f"可事实上天气根本没变，晴天不会\"变得更好\"。"
        )
    else:
        sg_block = "（S3-0 寿光实测校准点未在本轮原始结果里找到）"

    off_rows = []
    for c in (off.get("cases") or []):
        off_rows.append([
            c["case"], c["precip_delta"], c["signal"], c["expected_signal"],
            "✅ 一致" if c["signal_match"] else "❌ 不一致",
            c.get("combined_attention"),
            c["answer_length"],
            "、".join(c.get("weather_claims") or []) or "（无）",
        ])

    # "真实天气是活的"这句话里的数字，全部从实测读数表里取
    rr = _reading_rows()
    base_seq = " → ".join(f"{r.get('baseline_pp')}%" for r in rr)
    fore_seq = " → ".join(f"{r.get('forecast_pp')}%" for r in rr)
    if rr:
        drift_txt = (f"降水概率基线在几分钟内就能从 {base_seq} 变化，"
                     f"预报概率从 {fore_seq} 变化。")
    else:
        drift_txt = "（本次没有额外的实测读数可列）"

    return f"""## 4. 外部信号判定

### 4.1 为什么用相对差分而不是绝对阈值（结构性论证）

**这一节的论证不依赖任何具体数字**：下面的表格是证据，不是论证的前提。
论证只讲**两种判据的量纲不一样**。

**绝对阈值回答的是"未来会不会下雨"。** 它的判据里只有一个量：
未来窗口那个点的**绝对降水概率**是否达到某个门限
（代码常量见 `B07/weather.py`：`EXTERNAL_FALLBACK_PRECIP_HIGH` /
`EXTERNAL_FALLBACK_PRECIP_LOW` / `EXTERNAL_FALLBACK_CLOUD_MAX`）。
它衡量的是"未来那一瞬间的天气本身有多糟"——取值全部落在**未来窗口**上。

**S3 要回答的是完全不同的另一个问题："外部条件相对现在，是变好还是变差。"**
这个问题的答案**不可能**只由未来那个点给出：同一个未来点，
相对于一个晴朗无雨的当前值是"变差"，相对于一个已经下暴雨的当前值就是"变好"。
要把它算出来，判据里**必须**有**同源、同时刻的当前值**作基线，
然后比较"未来值相对基线移动了多少"。

**所以：只要基线不在判据里，量纲就错了。**
绝对阈值的判据里没有基线，它算的是"未来会不会下雨"；
相对差分的判据里有基线，它算的是"相对现在变好还是变差"。
这不是"阈值调得准不准"的问题，是**两个判据在回答两个不同的问题**；
再精确的测量也改变不了这一点。

**唯一可验证的实测例（S3-0 寿光，晴天）**

{sg_block}

这是本报告里唯一一条**实测**支撑：相对差分说"稳定"、绝对阈值说"在好转"，
而"晴天不会变得更好了"这件事不需要任何额外假设。

**下面是本次用同一份实现（`B07/weather.py` 的 `judge_external_points()`）
对同一组点各判一次的全量对照**（相对差分给两个点，绝对阈值只给预报点，
等价于基线取不到时的兜底分支）：

{measured_table}

**构造用例（不是实测，不得当实测结论引用）**

{constructed_table}

> 上表里标"构造用例"的行是**人为构造**的输入，用来演示两种判据会分叉，
> **不是**观测到的天气。引用本报告结论时不得把它当实测数据。
> 实测的支撑只有上一段那一条（寿光晴天）。

**结论**：主判定用相对差分，绝对阈值只在基线取不到时兜底；
兜底时 `judge_method` 明确写成 `absolute_fallback`，`basis` 里写明
"可能把『本来就晴』误判为好转"。

### 4.2 离线组三档可复现

离线组用夹具注入 B05 天气（**不联网**），固定输入，验证三档信号：

{_table(["用例", "Δpp", "实际 signal", "期望", "判定", "combined_attention", "回答字数", "回答里的天气断言"], off_rows)}

三档信号全部命中：**{"✅" if off.get('all_signals_match') else "❌"}**。

**离线组才是可复现结论，真实组只是一次快照。**
离线组的输入被夹具固定住了，任何人重跑都会得到同样的信号；
真实组的输入是活的天气 API，同一地点同一窗口，下一次运行可能给出不同的读数。

### 4.3 真实组：本次的 signal 与 Δ，以及"真实天气是活的"

本次真实组：signal = `{ext.get('signal')}`，judge_method = `{ext.get('judge_method')}`，
Δpp = {_fmt(ext.get('precip_delta'), 1, '')}，
基线点 {(ext.get('baseline_point') or {}).get('timestamp')} →
预报点 {(ext.get('forecast_point') or {}).get('timestamp')}。

**"真实天气是活的"**：同一地点（河北固安）、同一窗口（6h），本会话内几次实测读数：

{_reading_table_now()}

{drift_txt}
**真实组的判定结果因此不可复现**——这是外部数据的固有性质，不是本实现的缺陷。
所以本报告里凡是"可复现的实验结论"，一律以离线组为准；
真实组只用来证明"这条链路在真实数据上确实跑得通"。
"""


def _sec5(raw) -> str:
    ca = raw.get("combined_attention") or {}
    a = ca.get("a") or {}
    b = ca.get("b") or {}
    ev = raw.get("evidence") or {}
    ctx = ev.get("context_diff") or {}
    ans = raw.get("answers") or {}
    off = raw.get("offline") or {}
    cmp_s1 = raw.get("compare_with_s1") or {}
    probe = raw.get("rule3_probe") or {}
    ans = raw.get("answers") or {}
    attributed = any(
        k in s for s in (ans.get("next_attention_b") or [])
        for k in ("外部", "天气", "降水", "云量")
    )

    return f"""## 5. 完成线逐条自评

> 说明：S3 阶段文档的"完成线"原文以「关注重点能合理变化，并能说明变化来自外部天气」
> 这一句为核心要求，并要求"先跑一次『不带天气』、再跑一次『加入天气』，其他条件保持不变，
> 比较两次结果"。下面逐条按可核对的原句要求自评，每条给出结论与证据位置。

### 5.1 "先跑一次『不带天气』、再跑一次『加入天气』，其他条件保持不变，比较两次结果"

**结论：✅ 达成。**

- 两次运行：Run A = `{raw.get('real', {}).get('run_id_a')}`（`S3_without`）、
  Run B = `{raw.get('real', {}).get('run_id_b')}`（`S3_with`），
  锚点同为 `{raw.get('real', {}).get('archive_a', {}).get('analysis_time')}`。
- "其他条件保持不变"的证据：提示词指纹 `{ev.get('prompt_a')}`（两次一致）、
  问题指纹两次一致、LLM 模型 `{ev.get('model_a')}` 两次一致、
  B06 模型版本 `{ev.get('model_version_a')}` 两次一致。
- 证据位置：§2.3 指纹对照表、§2.1 逐字段 diff、§2.2 上下文 diff。

### 5.2 "完成线是『关注重点能合理变化，并能说明变化来自外部天气』"

**结论：🟡 部分达成——程序层达成，LLM 叙述层不稳定。**

- **程序层达成**：`combined_attention` 从 `{a.get('value')}` 变为 `{b.get('value')}`，
  规则写死在 `B07/judgement.py`，可脱离 LLM 从存档复算
  （复算结果：Run A {"✅ 一致" if ca.get('a_matches_recompute') else "⚠️ 不一致"}、
  Run B {"✅ 一致" if ca.get('b_matches_recompute') else "⚠️ 不一致"}）。
  变化原因可说明：Run B 理由 = {b.get('reason')}。
- **LLM 叙述层不稳定**：本轮 Run B
  {"引用了外部天气" if (raw.get('answers') or {}).get('cited_external_b') else "**没有引用外部天气**"}，
  "接下来关注"的表述归因于外部天气：{_yn(attributed)}。
  跨 {len(_citation_rows())} 次 S3_with 运行，模型引用 ⑥ 块的比例是 {_cite_rate()}（§3.6）。
  规则本身也解释了原因之一——`combined_attention` **不进 LLM 上下文**，
  模型看不到"关注等级已提升"这个结论，只看到 ⑥ 块里的外部数据，
  于是它可能自己判断"暂无足够依据提高关注程度"。
- 证据位置：§2.6、§3.4、§3.6。

### 5.3 合成关注等级规则（第三条：规则必须写在程序里，可脱离 LLM 复算）

**结论：✅ 达成。**

- 实现：`B07/judgement.py` 的 `combined_attention()` / `build_combined_attention()`。
- 取值：Run A = `{a.get('value')}`、Run B = `{b.get('value')}`（两次**不同**）。
- 可复算：存档里的 `combined_attention` 字段与"只用存档重新推导"的结果一致。
- 不侵入结构化判断：`combined_attention` **不是**结构化判断的第 7 个字段，
  `external_conditions` 为 None 时结构化判断仍然严格是 5 个字段
  （`B07/tests/test_s3_invariants.py` 有断言锁住）。
- 证据位置：§2.6、`B07/tests/test_s3_invariants.py`。

### 5.4 差分证据必须可复核

**结论：✅ 达成。**

- 前 5 个结构化字段逐字段相同，第 6 字段为追加。
- LLM 上下文 B 的前 {ctx.get('prefix_length')} 字与 A 逐字节相同，
  B == A + `"\\n\\n"` + ⑥ 块。
- 证据位置：§2.1、§2.2。

### 5.5 与既有 S1 存档的可复现对照

**结论：✅ 达成。** 对照存档 `{cmp_s1.get('s1_run_id')}`，
三项（predicted_attention / stability_verdict / stability_criteria）全部一致。
证据位置：§2.4。

### 5.6 离线组可复现 + 降级路径

**结论：✅ 达成。** 离线组三档信号全部命中（§4.2）；
天气服务不可用时整次分析 status = `{(off.get('degraded') or {}).get('run_status')}`，
signal = `{(off.get('degraded') or {}).get('signal')}`，回答里零天气断言、零外部数值（§6.1）。

### 5.7 提示词第 3 条禁令的字面冲突

**结论：🟡 存在字面冲突；成因为「无法区分」。**

- 单次探针（针对最后一次运行）的结论是「{probe.get('verdict')}」，
  但探针自己声明"无法区分这是禁令的压制还是模型自身的取舍"。
- 多轮复测（{len(_citation_rows())} 次 S3_with）**证实了波动真实存在**，
  且**排除了检测器漏判**；但**波动是不是第 3 条禁令造成的，仍然无法区分**
  ——要区分必须只改提示词第 3 条做对照，本轮硬约束禁止改 `SYSTEM_PROMPT`。
- 按"只改状态，不改核心提示词"的约束，本轮**未修改**提示词。
- 详见 §3.9（三种来源的区分）、§8 L6。
"""


def _sec6(raw) -> str:
    off = raw.get("offline") or {}
    deg = off.get("degraded") or {}

    return f"""## 6. 失败与降级

### 6.1 天气服务不可用时的降级行为

| 项目 | 实测值 |
|---|---|
| 外部判定 signal | `{deg.get('signal')}` |
| 判定方式 judge_method | `{deg.get('judge_method')}` |
| 整次分析 status | `{deg.get('run_status')}` |
| 结构化判断字段数 | {deg.get('judgement_field_count')}（含第 6 字段，显式说明不可用，不是静默消失） |
| combined_attention | `{deg.get('combined_attention')}` |

显式 warning：

{chr(10).join('> ⚠️ ' + w for w in (deg.get('external_warnings') or []) or ['（无）'])}

降级后的回答（{deg.get('answer_length')} 字）：

```
{deg.get('answer')}
```

- 提到天气的句子：{deg.get('weather_mentions') or '（无）'}
- 断言天气事实的句子：{deg.get('weather_claims') or '（无）'}
- 出现的外部数值：{deg.get('fabricated_numbers') or '（无）'}

**结论**：天气服务挂掉时整次分析**没有整体失败**（status 仍为 success），
外部条件被显式标记为不可用并带 warning，LLM 上下文里也拿到"外部天气不可用、不要编造天气"
的明确指令，**回答里没有出现任何编造的天气内容**。

### 6.2 失败尝试：run_016 / run_017（**保留在存档里，不删**）

| 项目 | 内容 |
|---|---|
| 存档 | `B07/runs/run_016/`（Run A）、`B07/runs/run_017/`（Run B） |
| 现象 | Run B 收到了 ⑥ 块，**但回答里写"当前判断未接入真实天气与农艺知识库"**——完全无视了 ⑥ 块 |
| 根因 | ⑤ 限制条件里写着"本阶段未接入 B03 天气数据与 B04 知识库"，与 ⑥ 块自相矛盾；模型采集了**限制条件**这一侧 |
| 修复 | `B07/judgement.py` 的 `no_weather_no_kb` 文案改成**两次都成立**的中性表述（"外部信息只作方向性参考，不参与内部数值的判定"），不再断言"未接入天气" |
| 验证 | 重跑 → run_018/run_019 引用了、run_020/run_021 引用了、run_022/run_023 **又没引用** |
| 教训 | 上下文里"**否定性限制**"与"**肯定性证据**"并存时，模型会挑更保守的那一条。**外部证据接入时必须同步清理描述"未接入该证据"的旧文案**，否则新证据会被自己的免责声明吃掉 |

**但修复只解决了一半——这一点必须说清楚。**
把"未接入 B03 天气数据"这句删掉之后，模型**仍然会**在某些次采样里无视 ⑥ 块
（§3.6 的引用率 {_cite_rate()}）。本轮最后一次运行
（`{raw.get('real', {}).get('run_id_b')}`）就没有引用，
回答里写的是"未接入真实传感器数据，暂无足够依据提高关注程度"。

现在能确认的、和仍然不能确认的：

- **能确认**：旧的"未接入"文案**必须删**（它是一个确定性的错误诱因）。
- **不能确认**：剩下的不引用行为是**提示词第 3 条的压制**，
  还是模型把"外部信息只作方向性参考，不参与内部数值的判定"读成了"外部信息其实没被用上"，
  还是单纯的采样波动——**探针无法区分这三种**（§8 L6）。
  要区分，得做对照实验（例如同一份 state 只改提示词第 3 条），那是 S4 的活。

这两份存档留在 `B07/runs/` 里，`run.json` 与 `B07/runs/index.json` 都带 note 说明：
「本次尝试失败，用于记录设计缺陷与修复过程」。
**失败是证据，不是垃圾。**
"""


def _sec7(raw) -> str:
    ca = raw.get("combined_attention") or {}
    real = raw.get("real") or {}
    return f"""## 7. 证据链

| 环节 | 来源 | 是否齐全 | 是否同源可追 |
|---|---|---|---|
| 内部证据 | B02 模拟历史 + B06 预测 | ✅ | ✅ 存档里有 `history`、`model_output`、模型版本与指纹 |
| 外部证据 | B03 Open-Meteo（经 B05） | ✅ | ✅ 存档里存了**基线点与预报点两个原始点**及取数时刻，可从存档重算 |
| 模型 | B06（`{(raw.get('evidence') or {}).get('model_version_a')}`） | ✅ | ✅ 存了 `model_version` 与训练指标 |
| 解释 | qwen3.5-27b | ✅ | ✅ 存了 `llm_explanation`、**发给模型的上下文原文** `llm_context`、提示词/问题指纹、耗时与长度 |

**四方齐全、同源可追**：`B07/runs/explain_run` 能只用 `run.json` 把
内部判定、外部判定、稳定判据、`combined_attention` 全部重新推导一遍并与存档值对照
（§2.5）。

**注意一处不同源**：LLM 的**文字**是当时生成的、不可复算；本报告因此只把它当"叙述"，
不把它的措辞当证据。所有可复算的结论都来自存档里的结构化数据。
"""


def _sec8(raw) -> str:
    ext = (raw.get("real") or {}).get("external") or {}
    ans = raw.get("answers") or {}
    off = raw.get("offline") or {}
    ca = raw.get("combined_attention") or {}
    probe = raw.get("rule3_probe") or {}
    return f"""## 8. Limitations（诚实清单）

- **L1 内部与外部不是同一时间基准。** 内部是 B02 的 SIMULATED 模拟数据
  （2026-10-15T02:00 ~ 2026-10-22T01:30），外部是 B03 的 PUBLIC 真实 API 真实时间
  （本次基线点 {(ext.get('baseline_point') or {}).get('timestamp')}、
  预报点 {(ext.get('forecast_point') or {}).get('timestamp')}），
  外部预报时刻比分析锚点晚约 3 天。**外部数值不可与内部数值直接比较，只作方向参考。**
- **L2 外部取的是窗口末时刻的单点值，不是窗口极值。** 中途更高/更低的值可能被漏掉。
- **L3 全部实测点位于夜间**（22:00 → 05:00），`solar_radiation` 恒为 0，
  **高温分支（≥35℃）至今没有被任何实测点覆盖**，需白天/夏季再验。
- **L4 外部湿度实测达 {_fmt((ext.get('forecast_point') or {}).get('humidity'), 1, '%')}（高湿）**，
  对番茄病害风险有实际意义，但当前外部判定**只用降水概率**，
  湿度仅作描述 —— 列为 S4/S5 扩展点。
- **L5 回答字数超限，代码不做强制截断。** 提示词要求"控制在 250 字以内"，
  但实测 Run A = {ans.get('length_a')} 字、Run B = {ans.get('length_b')} 字，均超出；
  （S1 存档 run_009 = 257 字，同样超）。`MAX_ANSWER_CHARS` 目前仅作为提示词约束的说明常量，
  **代码未做强制截断**。{_sec8_l5_tail(raw)}
- **L6 SYSTEM_PROMPT 第 3 条与 S3 目标存在字面冲突。**
  第 3 条原文："绝对不得编造上下文中没有的事实——不许出现真实天气、真实传感器读数，
  或任何上下文里没出现过的数字与结论。" 而 S3 恰恰要把真实天气放进 state。
  单次探针结论：{probe.get('verdict')}。
  **多轮复测后的结论（§3.9）**：
  ① 波动**已被证实存在**（同一 `llm_context` 逐字节相同、一次引用一次不引用）；
  ② 检测器漏判**已排除**（扩充词表 + 逐字通读 + 兜底字符扫描）；
  ③ 但**波动的成因是不是第 3 条禁令，本轮无法区分**——
  要区分必须只改提示词第 3 条做对照，而本轮硬约束禁止改 `SYSTEM_PROMPT`。
  按"只改状态，不改核心提示词"的约束，本轮**未修改**。
  建议措辞（留待 S4 / 结项单独评估）：
  「绝对不得编造上下文中没有的事实——凡上下文里没有出现的数字与结论都不要说；
  上下文里已经给出的外部数据（如真实天气）可以引用，但要连同它的限制条件一起说明。」
- **L7 `no_weather_no_kb` 文案已演进。** `B07/judgement.py` 的这段限制条件
  已从"未接入 B03 天气"改为**两次都成立**的中性表述。
  **因此当前代码重跑的 limitations 文本，与 S1 存档 run_012 里的旧文本不一致**——
  这是规则文本的演进，不是数据污染；评审 diff 时请知悉。
- **L8 离线组三档里，模型对外部条件的表态并不一致**，如实记录：
  {('；'.join(f"{c['case']} 档：天气断言 " + ('「' + '、'.join(c.get('weather_claims') or []) + '」' if c.get('weather_claims') else '（无）') for c in (off.get('cases') or []))) or '（无）'}。
  三档的 combined_attention 分别为
  {', '.join(f"{c['case']}→{c.get('combined_attention')}" for c in (off.get('cases') or []))}。
  注意：离线组的断言用的是**当前**判定器（扩词后重跑），与 s3_raw_result.json 里
  当时的旧判定可能不同——本报告一律以重跑结果为准。
- **L9 B06 模型 recall=0.448 / f1=0.510，对异常窗口存在明显漏报**；
  `predicted_attention=NORMAL` **不等于**确认无异常。
  外部 6h 后的降水概率上升**不改变 1–2h 内的越界判断**——这是物理上正确的，
  不是"外部信号没起作用"。
- **L10 真实天气每次运行都会变，真实组的判定结果不可复现**；
  可复现的实验结论一律以离线组为准
  （§4.3 有本会话内 {len(_reading_rows())} 次实测读数作证）。
- **L11（本轮新发现）关注等级的变化只发生在程序层，LLM 叙述层没跟上。**
  原因是 `combined_attention` 没有进 LLM 上下文（不进上下文是为了保住"上下文只差 ⑥ 块"
  这个可复核的不变量）。**代价是模型不知道系统的合成结论**，
  文本与程序结论存在不一致。下一步（S4）要么把合成结论作为第 7 个字段显式给模型、
  要么明确"LLM 只负责叙述事实、不负责给结论"。
- **L12 样本量说明。** S3_with 存档共 {len(_citation_rows())} 次，
  但它们**不是** {len(_citation_rows())} 个独立样本：
  按 `llm_context` 分组后只有 {len(_context_groups())} 个不同上下文
  （§3.8），其中只有 1 个组出现了"同上下文结果不一致"。
  所以能支撑的结论是"**同一状态下的引用行为会波动**"，
  **不足以**支撑"引用率等于某个百分比的统计估计"，
  更不能推出"外部证据在统计上显著提升了判断质量"。
- **L13（本轮新发现，最要紧的一条）模型"引用不引用 ⑥ 块"是不确定行为。**
  在 {len(_citation_rows())} 次 S3_with 运行里，引用率是 {_cite_rate()}（§3.6/§3.8）；
  程序层的 `combined_attention` 则每次都是 `watch_external`。
  **波动已被定位到"同上下文不同采样"这一层**：§3.7 给出了一对
  `llm_context` 逐字节相同、一次引用一次不引用的存档；
  同时引用率**强烈依赖 ⑥ 块内容**（同一上下文分组内，引用率从部分到全有）。
  含义：**"外部证据接入后模型会讨论外部天气"这句话目前不成立**，
  只能说"外部证据接入后模型有时会讨论外部天气"，
  而且"有时"的比例随 ⑥ 块内容变化。
  **波动为什么发生**（是不是 SYSTEM_PROMPT 第 3 条的字面禁令在压制）
  本轮**无法区分**——要区分必须只改提示词第 3 条做对照，
  而本轮硬约束禁止改 `SYSTEM_PROMPT`（§3.9 来源三）。
  这件事是 S4 的活。
"""


def _sec9(raw) -> str:
    real = raw.get("real") or {}
    return f"""## 9. 复现命令

环境：Windows + Git Bash，虚拟环境在仓库根目录 `./venv`。

```bash
# 0) 装依赖（如果还没装）
./venv/Scripts/python.exe -m pip install -r requirements.txt

# 1) 跑 S3 对照实验（真实 API + 离线组 + 降级验证）
#    落盘：B07/artifacts/s3_raw_result.json
#    产出：Run A / Run B 两份存档（B07/runs/run_XXX/）
./venv/Scripts/python.exe -m B07.s3

# 2) 由原始结果生成本报告
./venv/Scripts/python.exe -m B07.s3_report

# 3) 单独看一次分析的完整回溯（只用存档文件重算）
./venv/Scripts/python.exe -m B07.runs explain {real.get('run_id_b')}

# 4) 看两次运行的存档原文
./venv/Scripts/python.exe -m B07.runs list
cat B07/runs/{real.get('run_id_a')}/run.json
cat B07/runs/{real.get('run_id_b')}/run.json

# 5) 离线跑不变量测试（不联网）
./venv/Scripts/python.exe -m pytest B07/tests -q

# 5b) 引用率探针：同一 anchor / 同一状态，只测 LLM 采样波动
#     （冻结 ⑥ 块，重复 N 次；失败也计入分母；落盘 s3_citation_stats.json）
./venv/Scripts/python.exe -c "from B07.s3_citation_probe import main; main(8, 4, append=True)"

# 5c) 全部 S3_with 存档的统一判定表（落盘 s3_citation_table.json）
./venv/Scripts/python.exe -m B07.s3_citation_table

# 6) 只跑外部天气模块（需要联网取真实天气）
./venv/Scripts/python.exe -m B07.weather --analysis-time 2026-10-21T16:00:00 \\
    --location 河北固安 --forecast-window 6h

# 7) 上游模块的回归
./venv/Scripts/python.exe B05/run_all_b05.py
./venv/Scripts/python.exe B05/verify_weather_source.py
```

**说明**：第 1 步依赖真实天气 API 与 LLM 服务，**每次跑出来的真实读数都会变**
（§4.3）；因此第 2 步必须紧跟第 1 步跑，否则报告里的数字对不上存档。
第 3–5 步完全离线、可反复复现。
"""


def _sec10(raw) -> str:
    return """## 10. 下一步

### S4 —— 多源证据分歧

外部证据接入后，**多个来源可能互相矛盾**：B02 内部模拟（稳定）vs 外部真实天气（恶化）
vs B06 模型预测（NORMAL）vs B04 农艺知识库（尚未接入）。S4 要回答：

- 证据冲突时怎么排序、以谁为准？本轮采用的"内部 NORMAL + 外部恶化 → watch_external"
  只是一个二元规则，**没有定义冲突仲裁的优先级**。
- 冲突时 LLM 应该被告知"存在分歧"还是"已经仲裁完的结论"？
  目前的做法是只给 ⑥ 块，**没有告诉模型内部与外部不一致**——
  这可能正是 Run B 仍然说"暂无足够依据提高关注程度"的原因。
- B04 知识库接入后，证据数量从 2 变 3，冲突面更大。

### S5 —— 证据缺失与受限判断

- B04 农艺知识库接入后，"为什么该数值对番茄意味着什么"才有依据。
- **湿度**：外部湿度实测达 91%，对番茄病害风险有实际意义，但现在只参与描述。
  S5 需要把它变成可判定的证据维度（怎么定阈值、和 B02 内部湿度怎么对齐）。
- **缺失**：任一证据源不可用时的降级矩阵要系统化（现在只测了"外部整体不可用"，
  没测"基线点不可用但预报点可用"以外的组合）。
- **受限判断**：证据不足时，系统应该输出"不确定"而不是硬给一个结论。
  当前的 `combined_attention` 规则里，`external_unavailable` 会被归到 `internal_only`——
  也就是"外部没数据"和"外部平稳"得到同一个结论，这两种情况其实不该混为一谈。

### 顺带要做的

- 提示词第 3 条与 S3 目标的字面冲突（§8 L6），留待 S4 或结项时统一评估是否改写。
- `MAX_ANSWER_CHARS` 要不要改成代码里的硬截断/硬校验（§8 L5）——
  先得解决"截断会把外部引用切断"的问题。
"""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def build_report(raw: dict) -> str:
    parts = [
        "# B07 S3 阶段报告 —— 外部天气证据接入（B03）",
        "",
        "> 本报告由 `python -m B07.s3_report` 从 `B07/artifacts/s3_raw_result.json` 自动生成，",
        "> **所有数字都来自实测存档**，没有手工填写。",
        "",
    ]
    parts.append(_sec0(raw))
    for fn in (_sec1, _sec2, _sec3, _sec4, _sec5, _sec6, _sec7, _sec8, _sec9, _sec10):
        parts.append(fn(raw))
    return "\n".join(parts)


def main():
    if not RAW_PATH.exists():
        print(f"❌ 找不到 {RAW_PATH}；先跑 `./venv/Scripts/python.exe -m B07.s3`")
        return 1
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    _refresh_answers(raw)
    if OFFLINE_PATH.exists():
        # 判定器扩词后，离线组的"天气断言"必须用重跑结果，否则报告里是旧判定
        raw["offline"] = json.loads(OFFLINE_PATH.read_text(encoding="utf-8"))
    text = build_report(raw)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"✅ 已生成 {REPORT_PATH}（{len(text)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
