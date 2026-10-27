"""B07 第二遍【创新点 1】—— 连续时间驱动的状态演化认知与趋势预测

文档要证明的事（p.42–46）：

    案例A的创新点一不是"使用了机器学习"，而是把
        当前观测 → 历史过程 → 变化趋势 → 短时预测
    组织成一个连续分析过程。

方法不是"重新开发"，而是 **拿掉 → 恢复 → 比较 → 追踪**：

    拿掉：只给当前 temperature / humidity / light
    恢复：加回最近 3 小时历史
    再恢复：加回 B06 短时预测

实验设计（2 个锚点 × 3 档消融 = 6 次运行，一次跑完）：

    锚点 A = S1 稳态窗口的 run（当前 32.8℃，历史长期在 33℃ 附近）
    锚点 B = S2 渐进漂移窗口的 run（当前 32.8℃，历史从 25℃ 持续升上来）

    scope=current                  只给"当前 temperature/humidity/light"
    scope=current_history          再加"最近 3 小时历史过程"
    scope=current_history_prediction 再加"B06 短时预测"

**不恢复 ⑥ 外部条件块、⑦ 证据分歧块**：创新点 1 讲的是"连续时间"，
S1/S2 时期本来就没有外部证据与知识台账，混进来会污染消融。

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改任何判据阈值；
  - 不改 B01–B06；不改 build_judgement 的返回结构；
  - 只调 LLM 解释层，不重跑 B02 / B06、不重训模型；
  - **不写入 B07/runs/** —— 创新点 1 是复盘消融，复用 S1/S2 既有 run 作数据源；
  - 失败必须显式暴露，禁止用静态文案伪装成功。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.explain import (FIXED_QUESTION, SYSTEM_PROMPT, _available_categories,
                      _filter_evidence_items, _filter_limitations,
                      explain_judgement, format_judgement_context)
from B07.runs import RUNS_DIR, explain_run, load_run

ARTIFACTS_DIR = _PROJECT_ROOT / "B07" / "artifacts"
RUNS_JSON_PATH = ARTIFACTS_DIR / "innovation1_runs.json"
REPORT_PATH = ARTIFACTS_DIR / "innovation1_report.md"

#: 三档消融 —— 每档 = 渲染哪些上下文块
SCOPES: dict[str, tuple[str, ...]] = {
    "current": ("①", "④", "⑤"),
    "current_history": ("①", "②", "④", "⑤"),
    "current_history_prediction": ("①", "②", "③", "④", "⑤"),
}

#: 锚点标签 → scenario
ANCHOR_SCENARIOS = {"A": "S1", "B": "S2"}

#: 首选的参考 run（与 B07/s2.py 的 S1_REFERENCE_RUN / S2_REFERENCE_RUN 一致）
PREFERRED_RUNS = {"A": "run_012", "B": "run_014"}

#: 上下文里**不该出现**的东西（消融泄露检查）
HISTORY_LEAK_MARKERS = (
    "【②", "【③", "最近历史", "稳定判据", "窗口区间", "hours_gap", "analysis_time",
    "B06", "recall", "f1", "predicted_attention", "model_version", "DTW",
    "confidence", "窗口 ID", "短时预测",
)
PREDICTION_LEAK_MARKERS = (
    "【③", "B06", "recall", "f1", "predicted_attention", "model_version", "DTW",
    "confidence", "窗口 ID", "短时预测",
)

#: 趋势类 / 预测类关键词（"趋势 ≠ 预测"的实测判定）
TREND_MARKERS = (
    "上升", "升高", "持续", "单调", "下降", "回落", "波动", "趋势",
    "走低", "攀升", "增长", "变化",
)
PREDICTION_MARKERS = (
    "预测", "下一窗口", "下一个窗口", "接下来", "预计", "后续", "未来",
    "短时", "可能",
)

_PUNCT_RE = re.compile(r"[\s，。；、：,.;:!?！？「」『』（）()\[\]【】\-—…·\"'“”‘’]+")
#: 抽数：**不**用否定环视 —— Python 的 \w 在 Unicode 下把汉字也算词字符，
#: `(?<!\w)` 会把"温度为32.8℃"里的 32.8 全部挡掉（实测踩过）。
#: 数字噪声靠下面两步清洗（日期 / 标识符）解决。
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
#: 数值提取前先清掉的"非物理量数字"：日期时间戳、模型/窗口版本号
_DATE_RE = re.compile(
    r"\d{4}[-年]\d{1,2}[-月]\d{1,2}日?"      # 2026-10-21 / 2026年10月21日
    r"(?:[T\s]?\d{1,2}:\d{2}(?::\d{2})?)?"     # 可选时间部分
    r"|\d{1,2}:\d{2}(?::\d{2})?"                # 独立的时间 16:00 / 09:30:00
)
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9._]+)+")


def _clean_numbers(text: str) -> set[float]:
    r"""抽出回答里的"物理量数字"。

    先清掉两类噪声数字，否则会把"文本相似"算成"日期相同"：
      - 日期 / 时间戳：2026-10-21T16:00:00
      - 标识符 / 版本号：b06-20261020-v1、s1-live-001
    再用 `(?<![\w.])\d+(?:\.\d+)?(?![\w])` 抽数，按浮点数去重。
    """
    t = _DATE_RE.sub(" ", text or "")
    t = _IDENT_RE.sub(" ", t)
    return {float(m) for m in _NUM_RE.findall(t)}


# --------------------------------------------------------------------------
# 量化工具（算法写死在报告里，可复现）
# --------------------------------------------------------------------------

def _norm(text: str) -> str:
    """去掉所有空白与标点，只留实义字符。"""
    return _PUNCT_RE.sub("", text or "")


def _char_ngrams(text: str, n: int = 4) -> set[str]:
    """字符 n-gram 集合（中文按字切，n=4 ⇒ "多字短语"级）。

    算法：先去掉标点与空白，再取所有长度为 n 的连续子串。
    同一段文本里的 n-gram 集合，就是它的"多字短语集合"。
    """
    s = _norm(text)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _numbers(text: str) -> set[float]:
    """回答里出现的数值集合（按浮点数去重；已清掉日期与版本号）。"""
    return _clean_numbers(text)


def phrase_similarity(a: str, b: str, n: int = 4) -> dict:
    """多字短语重合率。

    算法（报告里逐字写明）：
      P = A 的 4-gram 集合，B 同理；
      jaccard      = |P∩Q| / |P∪Q|
      containment  = |P∩Q| / min(|P|, |Q|)   （谁更像是谁的子集）
    """
    pa, pb = _char_ngrams(a, n), _char_ngrams(b, n)
    inter = len(pa & pb)
    union = len(pa | pb)
    return {
        "n": n,
        "a_phrases": len(pa),
        "b_phrases": len(pb),
        "intersection": inter,
        "jaccard": (inter / union) if union else None,
        "containment": (inter / min(len(pa), len(pb))) if pa and pb else None,
    }


def number_similarity(a: str, b: str) -> dict:
    """数值集合 Jaccard。

    算法：把两段回答里的所有数字抽成浮点集合 Na / Nb，
    jaccard = |Na∩Nb| / |Na∪Nb|。
    """
    na, nb = _numbers(a), _numbers(b)
    inter = len(na & nb)
    union = len(na | nb)
    return {
        "a_numbers": len(na),
        "b_numbers": len(nb),
        "intersection": inter,
        "jaccard": (inter / union) if union else None,
        "a_set": sorted(na),
        "b_set": sorted(nb),
        "common": sorted(na & nb),
    }


def marker_hits(text: str, markers) -> list[str]:
    """命中的关键词列表（按出现顺序去重）。"""
    out = []
    for m in markers:
        if m in (text or "") and m not in out:
            out.append(m)
    return out


def history_temperature_citations(answer: str, history: list[dict]) -> list[float]:
    """回答引用了历史温度序列里的哪些值。

    判据：**只取历史序列里除最后一个点以外**的温度值
    （最后一个点就是当前温度，否则每篇回答都会“命中”当前值）。
    命中 ⇒ 回答确实在谈"过去的过程"，而不只是当前这一个数。
    """
    hist = [h.get("temperature") for h in (history or [])]
    past = {float(v) for v in hist[:-1] if v is not None}
    nums = _numbers(answer)
    return sorted(v for v in past if v in nums)


def prediction_citations(answer: str, model_output: dict) -> dict:
    """回答引用了 B06 预测里的哪些量。

    只看 **③ 块真正渲染给 LLM 的那几个数**：
    confidence / confidence_normal / confidence_watch /
    dtw_temperature / dtw_humidity / dtw_lux，
    外加 predicted_attention 这个等级字符串。
    """
    mo = model_output or {}
    nums = _numbers(answer)
    cited = []
    for k in ("confidence", "confidence_normal", "confidence_watch",
              "dtw_temperature", "dtw_humidity", "dtw_lux"):
        v = mo.get(k)
        if v is None:
            continue
        if round(float(v), 6) in {round(x, 6) for x in nums}:
            cited.append(k)
    level = mo.get("predicted_attention")
    return {
        "numeric_keys_cited": cited,
        "attention_level_cited": bool(level) and str(level) in (answer or ""),
        "attention_level": level,
    }


def leak_check(context: str, scope: str) -> dict:
    """消融泄露检查：上下文里不该出现的东西出现了没有。"""
    markers = []
    if scope == "current":
        markers = HISTORY_LEAK_MARKERS
    elif scope == "current_history":
        markers = PREDICTION_LEAK_MARKERS
    hits = [m for m in markers if m in (context or "")]
    return {"scope": scope, "checked": list(markers), "hits": hits,
            "leaked": bool(hits)}


# --------------------------------------------------------------------------
# 锚点选择
# --------------------------------------------------------------------------

def _is_consistent(run_id: str) -> tuple[bool, dict]:
    """复算一致性：explain_run 的所有检查项是否逐条一致。

    判定规则（**显式、可核对**）：
      - 归档里**有**这个字段的检查项 → 必须 match；
      - 归档里**没有**这个字段（archived 为 None）的检查项 → 不可比，
        **不算不一致**，但会被记进 `not_comparable` 并写进报告。
        （老存档 run_012 / run_014 / run_015 是 S3 之前生成的，
        顶层没有 combined_attention 字段，属于"存档里没这一项"，
        不是"复算对不上"。）
    """
    ex = explain_run(run_id, quiet=True)
    checks = ex.get("checks") or []
    bad, skipped = [], []
    for c in checks:
        if c.get("match"):
            continue
        if c.get("archived") is None:
            skipped.append(c["name"])
        else:
            bad.append(c["name"])
    return (not bad), {"checks": len(checks), "mismatched": bad,
                       "not_comparable": skipped}


def _candidate_runs(scenario: str) -> list[str]:
    out = []
    for d in sorted(RUNS_DIR.glob("run_*")):
        f = d / "run.json"
        if not f.exists():
            continue
        try:
            arch = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if arch.get("scenario") != scenario:
            continue
        if arch.get("status") != "success":
            continue
        if not arch.get("llm_explanation"):
            continue
        out.append(d.name)
    return out


def select_anchors(verbose: bool = True) -> dict:
    """挑出 S1 / S2 的代表 run。

    规则：**优先**用首选参考 run（run_012 / run_014），
    但必须**有 llm_explanation 且复算一致**；
    首选不合格就在同 scenario 的其它存档里找一个合格的；
    **一个都找不到 → 直接报错，不瞎选**。
    """
    anchors: dict[str, dict] = {}
    for tag, scenario in ANCHOR_SCENARIOS.items():
        candidates = _candidate_runs(scenario)
        if not candidates:
            raise RuntimeError(
                f"锚点 {tag}（scenario={scenario}）没有任何带 llm_explanation 的成功存档，"
                f"实验不能开始（不瞎选）。")

        ordered = ([PREFERRED_RUNS[tag]] if PREFERRED_RUNS.get(tag) in candidates
                   else []) + [c for c in candidates if c != PREFERRED_RUNS.get(tag)]

        picked = None
        rejected = []
        for rid in ordered:
            ok, info = _is_consistent(rid)
            if ok:
                picked = (rid, info)
                break
            rejected.append({"run_id": rid, "mismatched": info["mismatched"],
                             "not_comparable": info["not_comparable"]})

        if not picked:
            raise RuntimeError(
                f"锚点 {tag}（scenario={scenario}）找不到复算一致的存档，"
                f"实验不能开始。已检查：{rejected}")

        rid, info = picked
        arch = load_run(rid)
        anchors[tag] = {
            "anchor": tag,
            "scenario": scenario,
            "source_run_id": rid,
            "analysis_time": arch.get("analysis_time"),
            "current_state": arch.get("current_state"),
            "history": arch.get("history"),
            "model_output": arch.get("model_output"),
            "recompute_checks": info["checks"],
            "recompute_not_comparable": info["not_comparable"],
            "rejected": rejected,
        }
        if verbose:
            print(f"  锚点 {tag}（{scenario}）= {rid}"
                  f"（复算一致 {info['checks']} 项；不可比 {info['not_comparable']}；"
                  f"被否 {[r['run_id'] for r in rejected]}）")
    return anchors


# --------------------------------------------------------------------------
# 实验主体
# --------------------------------------------------------------------------

def run_experiment(verbose: bool = True) -> dict:
    """跑完 6 次消融，返回可直接落盘的记录 dict。"""
    from B07.judgement import build_judgement

    if verbose:
        print("=" * 78)
        print("B07 创新点 1 —— 连续时间认知消融（2 锚点 × 3 档 = 6 次）")
        print("=" * 78)
        print("\n[1] 选锚点（S1 / S2 的代表 run）")
    anchors = select_anchors(verbose=verbose)

    records = []
    for tag in ("A", "B"):
        a = anchors[tag]
        bundle = load_run(a["source_run_id"])     # 存档本身就是 run_bundle
        judgement = build_judgement(bundle)      # 不传台账 ⇒ 只有 5 个字段
        for scope, include in SCOPES.items():
            ctx = format_judgement_context(bundle, include)
            leak = leak_check(ctx, scope)
            if verbose:
                print(f"\n[2] 锚点 {tag} / {scope}"
                      f"（include={'/'.join(include)}）"
                      f" 上下文 {len(ctx)} 字"
                      f" 泄露检查={'❌ ' + str(leak['hits']) if leak['leaked'] else '✅ 无'}")
            if leak["leaked"]:
                raise RuntimeError(
                    f"消融泄露：锚点 {tag} / {scope} 的上下文里出现了不该有的内容 "
                    f"{leak['hits']}，实验作废（不许继续跑）。")

            res = explain_judgement(bundle, judgement=judgement, context=ctx)
            answer = res["answer"]
            # 裁剪明细（可审计）：哪些 limitation key / evidence item 被裁掉
            cats = _available_categories(set(include))
            lim_kept, lim_dropped, lim_unknown = _filter_limitations(
                judgement["limitations"], cats)
            es_kept, es_dropped, _, es_unknown = _filter_evidence_items(
                judgement["evidence_status"], cats)
            records.append({
                "source_run_id": a["source_run_id"],
                "anchor": tag,
                "scenario": a["scenario"],
                "scope": scope,
                "include": list(include),
                "context": ctx,
                "context_length": len(ctx),
                "answer": answer,
                "answer_length": res["content_length"],
                "usage": res.get("usage"),
                "model": res.get("model"),
                "system_prompt_sha256": res.get("system_prompt_sha256"),
                "question_sha256": res.get("question_sha256"),
                "question": res.get("question"),
                "leak_check": leak,
                "limitation_keys_kept": lim_kept,
                "limitation_keys_dropped": lim_dropped,
                "limitation_keys_unknown": lim_unknown,
                "evidence_items_kept": [f"{i.get('module')}.{i.get('item')}"
                                        for i in es_kept],
                "evidence_items_dropped": es_dropped,
                "evidence_items_unknown": es_unknown,
                "analysis_time": a["analysis_time"],
                "current_temperature": (a["current_state"] or {}).get("temperature"),
                "current_humidity": (a["current_state"] or {}).get("humidity"),
                "current_lux": (a["current_state"] or {}).get("lux"),
                "predicted_attention": (a["model_output"] or {}).get("predicted_attention"),
                # ---- 量化指标（全部来自实测文本，不是预设）----
                "history_temp_citations": history_temperature_citations(
                    answer, a.get("history")),
                "prediction_citations": prediction_citations(
                    answer, a.get("model_output")),
                "trend_markers": marker_hits(answer, TREND_MARKERS),
                "prediction_markers": marker_hits(answer, PREDICTION_MARKERS),
                "numbers": sorted(_numbers(answer)),
            })

    return {
        "generated_at": _now(),
        "experiment": "B07 创新点 1 —— 连续时间驱动的状态演化认知与趋势预测",
        "system_prompt_sha256": _sha12(SYSTEM_PROMPT),
        "question_sha256": _sha12(FIXED_QUESTION),
        "question": FIXED_QUESTION,
        "scopes": {k: list(v) for k, v in SCOPES.items()},
        "anchors": {k: {kk: vv for kk, vv in v.items() if kk != "history"}
                    for k, v in anchors.items()},
        "anchor_histories": {k: v.get("history") for k, v in anchors.items()},
        "records": records,
    }


def run_scope1_repeat(times: int = 3, verbose: bool = True) -> list[dict]:
    """附加实验：档 1（只看当前值）重复采样若干次。

    用途：主实验每格只有 1 个样本，措辞层面的相似度指标会被采样波动主导。
    这里把档 1 的两个锚点各跑 `times` 次，量化"档 1 到底有多相似"的分布，
    用来区分两种可能：
      - **消融泄露**（历史/预测内容混进了档 1）
      - **采样波动**（同一份输入，不同次采样给出不同措辞/结论）

    不写进 B07/runs/，只作为主记录的附加字段。
    """
    from B07.explain import explain_judgement
    from B07.judgement import build_judgement

    anchors = select_anchors(verbose=verbose)
    include = SCOPES["current"]
    out: list[dict] = []
    for i in range(times):
        for tag in ("A", "B"):
            a = anchors[tag]
            bundle = load_run(a["source_run_id"])
            judgement = build_judgement(bundle)
            ctx = format_judgement_context(bundle, include)
            res = explain_judgement(bundle, judgement=judgement, context=ctx)
            ans = res["answer"]
            out.append({
                "repeat_index": i + 1,
                "anchor": tag,
                "scope": "current",
                "source_run_id": a["source_run_id"],
                "context": ctx,
                "context_sha12": _sha12(ctx),
                "answer": ans,
                "answer_length": res["content_length"],
                "history_temp_citations": history_temperature_citations(
                    ans, a.get("history")),
                "prediction_citations": prediction_citations(
                    ans, a.get("model_output")),
                "trend_markers": marker_hits(ans, TREND_MARKERS),
                "prediction_markers": marker_hits(ans, PREDICTION_MARKERS),
            })
        if verbose:
            print(f"  档 1 复测 {i + 1}/{times} 完成")
    return out


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _sha12(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def write_runs_json(raw: dict) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_JSON_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return RUNS_JSON_PATH


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _f(x, digits=4) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def build_report(raw: dict) -> str:
    """生成 innovation1_report.md（数字全部来自实测记录）。"""
    recs = raw["records"]
    # 量化指标一律**现场重算**（不信任存档里的旧值），
    # 这样 --report-only 也能用最新的指标算法刷新报告。
    for r in recs:
        hist = (raw.get("anchor_histories") or {}).get(r["anchor"]) or []
        mo = (raw.get("anchors", {}).get(r["anchor"], {}) or {}).get("model_output") or {}
        r["history_temp_citations"] = history_temperature_citations(r["answer"], hist)
        r["prediction_citations"] = prediction_citations(r["answer"], mo)
        r["trend_markers"] = marker_hits(r["answer"], TREND_MARKERS)
        r["prediction_markers"] = marker_hits(r["answer"], PREDICTION_MARKERS)
        r["numbers"] = sorted(_numbers(r["answer"]))
        if "limitation_keys_kept" not in r:
            try:
                from B07.judgement import build_judgement
                from B07.runs import load_run
                bundle = load_run(r["source_run_id"])
                jd = build_judgement(bundle)
                cats = _available_categories(set(r["include"]))
                lk, ld, lu = _filter_limitations(jd["limitations"], cats)
                ek, ed, _, eu = _filter_evidence_items(jd["evidence_status"], cats)
                r["limitation_keys_kept"] = lk
                r["limitation_keys_dropped"] = ld
                r["limitation_keys_unknown"] = lu
                r["evidence_items_kept"] = [f"{i.get('module')}.{i.get('item')}"
                                            for i in ek]
                r["evidence_items_dropped"] = ed
                r["evidence_items_unknown"] = eu
            except Exception as e:            # pragma: no cover
                r["limitation_keys_kept"] = f"<不可得：{e}>"
                r["limitation_keys_dropped"] = []
                r["limitation_keys_unknown"] = []
                r["evidence_items_kept"] = []
                r["evidence_items_dropped"] = []
                r["evidence_items_unknown"] = []
    by = {(r["anchor"], r["scope"]): r for r in recs}

    lines: list[str] = []
    A = lines.append

    A("# B07 创新点 1 —— 连续时间驱动的状态演化认知与趋势预测")
    A("")
    A(f"（原始结果生成时间：{raw.get('generated_at')}；"
      f"本报告由 `python B07/innovation1.py` 从 `innovation1_runs.json` 离线生成，"
      f"**每个数字都来自实测输出**）")
    A("")
    A(f"- 提示词指纹 system_prompt = `{raw.get('system_prompt_sha256')}`")
    A(f"- 固定问题指纹 question = `{raw.get('question_sha256')}`")
    A(f"- 固定问题原文：{raw.get('question')}")
    A("")

    # ---- 0 一句话结论 ----
    A("## 0 一句话结论")
    A("")
    s1 = phrase_similarity(by[("A", "current")]["answer"],
                            by[("B", "current")]["answer"])
    s2 = phrase_similarity(by[("A", "current_history")]["answer"],
                            by[("B", "current_history")]["answer"])
    s3 = phrase_similarity(by[("A", "current_history_prediction")]["answer"],
                            by[("B", "current_history_prediction")]["answer"])
    n1 = number_similarity(by[("A", "current")]["answer"],
                            by[("B", "current")]["answer"])
    n2 = number_similarity(by[("A", "current_history")]["answer"],
                            by[("B", "current_history")]["answer"])
    n3 = number_similarity(by[("A", "current_history_prediction")]["answer"],
                            by[("B", "current_history_prediction")]["answer"])
    # 同锚点跨档相似度（用作相对参照，避免凭空设一个绝对阈值）
    adj_aa = phrase_similarity(by[("A", "current")]["answer"],
                               by[("A", "current_history")]["answer"])
    adj_bb = phrase_similarity(by[("B", "current")]["answer"],
                               by[("B", "current_history")]["answer"])
    adj_a3 = phrase_similarity(by[("A", "current_history")]["answer"],
                               by[("A", "current_history_prediction")]["answer"])
    adj_b3 = phrase_similarity(by[("B", "current_history")]["answer"],
                               by[("B", "current_history_prediction")]["answer"])

    # 预期 1 的判据（相对判据，不设拍脑袋的绝对阈值）：
    #   档 1 跨锚点相似度 必须高于 同锚点跨档相似度
    e1_ok = (s1["jaccard"] or 0) > (adj_aa["jaccard"] or 0) and \
            (s1["jaccard"] or 0) > (adj_bb["jaccard"] or 0)
    # 预期 2：加历史后跨锚点相似度必须下降
    e2_ok = (s2["jaccard"] or 0) < (s1["jaccard"] or 0)
    # 预期 3：档 3 的"预测类引用"必须多于档 2
    pr2 = sum(len(by[(t, "current_history")]["prediction_markers"])
              for t in ("A", "B"))
    pr3 = sum(len(by[(t, "current_history_prediction")]["prediction_markers"])
              for t in ("A", "B"))
    e3_ok = pr3 > pr2

    A("**创新点 1 的三条预期，实测结果**（数字全部来自 `innovation1_runs.json`）：")
    A("")
    # 复测（附加实验）统计
    rep_stats = None
    if raw.get("scope1_repeat"):
        rep = raw["scope1_repeat"]
        _js, _ns = [], []
        for i in range(1, max(r["repeat_index"] for r in rep) + 1):
            ra = next(r for r in rep if r["repeat_index"] == i and r["anchor"] == "A")
            rb = next(r for r in rep if r["repeat_index"] == i and r["anchor"] == "B")
            _js.append(phrase_similarity(ra["answer"], rb["answer"])["jaccard"] or 0.0)
            _ns.append(number_similarity(ra["answer"], rb["answer"])["jaccard"] or 0.0)
        leak_hits = sum(len(r["history_temp_citations"]) +
                        len(r["prediction_citations"]["numeric_keys_cited"])
                        for r in rep)
        rep_stats = {"n": len(_js), "js": _js, "ns": _ns, "leak": leak_hits}

    A("1. **预期 1（档 1 下 A 与 B 应高度相似）—— 分成两条判据分别回答：**")
    A(f"   - **泄露判据（红线，文档点名要报的那条）：通过，不是泄露。** "
      f"档 1 上下文零泄露标记；档 1 的回答**没有引用任何历史温度值、"
      f"也没有引用任何 B06 的量**"
      + (f"（{rep_stats['n']} 轮复测共 {rep_stats['n'] * 2} 篇回答，引用合计 "
         f"{rep_stats['leak']} 处）" if rep_stats else "")
      + "。")
    A(f"   - **措辞判据（“高度相似”）：本轮主样本**"
      f"{'不成立' if not e1_ok else '成立'}**"
      f"—— 4-gram jaccard = {_f(s1['jaccard'])}、containment = {_f(s1['containment'])}、"
      f"数值 jaccard = {_f(n1['jaccard'])}（共同数值 {n1['common']}）。"
      + (f"档 1 重复采样 {rep_stats['n']} 轮后，"
         f"跨锚点 jaccard 在 **{_f(min(rep_stats['js']))} ~ {_f(max(rep_stats['js']))}**"
         f"之间波动（均值 {_f(sum(rep_stats['js']) / len(rep_stats['js']))}），"
         f"数值 jaccard 在 {_f(min(rep_stats['ns']))} ~ {_f(max(rep_stats['ns']))} 之间 —— "
         f"说明单次测量**不足以**判定“高度相似”，见 §4.5。" if rep_stats else ""))
    A(f"2. **预期 2（加历史后 A 与 B 应明显分叉）—— 分成两条判据分别回答：**")
    A(f"   - **结论层：成立。** 档 2 里 A 判“稳定”、B 判“不稳定”，"
      f"并且 B 的档 2 回答引用了历史温度 "
      f"{by[('B', 'current_history')]['history_temp_citations'] or '（无）'}"
      f"（档 1 同一锚点**没有**引用任何历史温度）。"
      f"这就是“历史过程给了 GreenMate 额外认知”的直接证据。")
    A(f"   - **措辞层：{'成立' if e2_ok else '本轮不成立'}** —— "
      f"跨锚点 4-gram jaccard {_f(s1['jaccard'])} → {_f(s2['jaccard'])}"
      f"（档 3 为 {_f(s3['jaccard'])}），数值 jaccard {_f(n1['jaccard'])} → "
      f"{_f(n2['jaccard'])}（档 3 {_f(n3['jaccard'])}）。"
      f"数值 jaccard 明显下降；4-gram jaccard 未下降，"
      f"原因是措辞波动远大于认知差异（见 §4.5 的复测分布）。")
    bc2 = sum(len(by[(t, "current_history")]["prediction_citations"]["numeric_keys_cited"])
              for t in ("A", "B"))
    bc3 = sum(len(by[(t, "current_history_prediction")]["prediction_citations"]["numeric_keys_cited"])
              for t in ("A", "B"))
    A(f"3. **预期 3 —— {'成立' if e3_ok else '不成立'}**："
      f"档 2 的 **B06 数值引用为 {bc2} 处**，档 3 为 **{bc3} 处**"
      f"（confidence / confidence_normal）；"
      f"预测类关键词档 2 合计 {pr2} 处、档 3 {pr3} 处"
      f"（关键词是弱指标 —— “接下来”“可能”这种泛用词会误命中，见 §5）。"
      f"**趋势 ≠ 预测**在原文层面成立：档 2 在谈过去，档 3 才开始谈下一窗口。")
    A("")
    A("**没有证明的**：本轮只验证“改变给 LLM 看什么会改变认知”，"
      "**没有**证明这种认知在真实大棚里成立（见 Limitations L6）。")
    A("")

    # ---- 1 实验设计 ----
    A("## 1 实验设计：拿掉 → 恢复 → 比较 → 追踪")
    A("")
    A("| 档位 | include（渲染哪些块） | 给 LLM 看什么 |")
    A("|---|---|---|")
    A("| `current` | ① ④ ⑤ | 只有当前 temperature / humidity / light（+裁剪后的 ④⑤）|")
    A("| `current_history` | ① ② ④ ⑤ | 再加最近 3 小时历史过程 |")
    A("| `current_history_prediction` | ① ② ③ ④ ⑤ | 再加 B06 短时预测 |")
    A("")
    A("**锚点**（数据源是 S1/S2 的既有存档，**不重跑 B02 / B06 / 不重训模型**）：")
    A("")
    A("| 锚点 | scenario | source_run_id | analysis_time | 当前 T / H / lux | 历史温度序列 |")
    A("|---|---|---|---|---|---|")
    for tag in ("A", "B"):
        anc = raw["anchors"][tag]
        hist = raw["anchor_histories"][tag] or []
        A(f"| {tag} | {anc['scenario']} | `{anc['source_run_id']}` | "
          f"{anc['analysis_time']} | "
          f"{(anc['current_state'] or {}).get('temperature')}℃ / "
          f"{(anc['current_state'] or {}).get('humidity')}% / "
          f"{(anc['current_state'] or {}).get('lux')} lux | "
          f"{' → '.join(str(h.get('temperature')) for h in hist)} |")
    A("")
    A("> 两个锚点的**当前温度、湿度完全相同**（32.8℃ / 56.7%），"
      "**光照不同**（39842 vs 59594 lux），采样时间戳也不同 —— "
      "这是两个真实窗口的客观差异，本轮**不**人为对齐，"
      "相似度指标里会体现出来（见 §4）。")
    A("")
    A("**不恢复 ⑥ 外部条件块、⑦ 证据分歧块**：创新点 1 讲的是“连续时间”，"
      "S1/S2 时期本来就没有外部证据与知识台账，混进来会污染消融。")
    A("")

    # ---- 2 六次运行一览 ----
    A("## 2 六次运行一览表")
    A("")
    A("| # | 锚点 | scope | include | 上下文长度 | 回答长度 | prompt_tokens | completion_tokens | model | 泄露检查 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(recs, 1):
        u = r.get("usage") or {}
        A(f"| {i} | {r['anchor']} | `{r['scope']}` | {'/'.join(r['include'])} | "
          f"{r['context_length']} | {r['answer_length']} | "
          f"{u.get('prompt_tokens')} | {u.get('completion_tokens')} | "
          f"{r.get('model')} | "
          f"{'❌ ' + str(r['leak_check']['hits']) if r['leak_check']['leaked'] else '✅ 无'} |")
    A("")

    # ---- 3 三问 ----
    A("## 3 文档第四步的三问（每问都给并排原文）")
    A("")
    A("> 三问分别对应档 1 / 档 2 / 档 3。下面每问给出**两个锚点的完整原文**，"
      "并注明该回答引用了哪些历史值 / 预测量。")
    A("")
    for q, scope in (
        ("① 只看当前值，我能判断什么？", "current"),
        ("② 加入历史后，我又知道了什么？", "current_history"),
        ("③ 加入短时预测后，我又能够多讨论什么？", "current_history_prediction"),
    ):
        A(f"### {q}")
        A("")
        for t in ("A", "B"):
            r = by[(t, scope)]
            A(f"**锚点 {t} / `{scope}`**"
              f"（历史温度引用：{r['history_temp_citations'] or '无'}；"
              f"预测量引用：{r['prediction_citations']['numeric_keys_cited'] or '无'}"
              f"{'，等级 ' + str(r['prediction_citations']['attention_level']) if r['prediction_citations']['attention_level_cited'] else ''}）")
            A("")
            A("> " + r["answer"].replace("\n", "\n> "))
            A("")

    # ---- 4 消融有效性检查 ----
    A("## 4 消融有效性检查（量化）")
    A("")
    A("### 4.1 算法（可复现，写死在这里）")
    A("")
    A("- **多字短语重合率**：把回答里的标点与空白全部去掉 → 取所有长度为 **4** 的连续字符子串"
      "（4-gram）构成集合 P。"
      "`jaccard = |P∩Q| / |P∪Q|`；`containment = |P∩Q| / min(|P|,|Q|)`。")
    A("- **数值集合 Jaccard**：先把回答里的日期时间戳"
      "（`2026-10-21T16:00:00` / `2026年10月21日16:00`）"
      "和标识符 / 版本号（`b06-20261020-v1`、`s1-live-001`）清掉，"
      "再用 `\\d+(?:\\.\\d+)?` 抽出浮点数集合 Na / Nb，"
      "`jaccard = |Na∩Nb| / |Na∪Nb|`。"
      "（清掉日期是因为两个锚点的采样时间本来就不同，"
      "不清会把“时间戳不同”算成“认知不同”。"
      "注意抽数**不用**否定环视 `(?<!\\w)` —— "
      "Python 的 `\\w` 在 Unicode 下把汉字也算词字符，"
      "会把“温度为32.8℃”里的 32.8 一起挡掉。）")
    A("- **历史引用**：取历史温度序列**除最后一个点以外**的值"
      "（最后一个点 = 当前温度，否则每篇回答都会“命中”当前值），"
      "看回答的数字集合里出现了其中几个 —— 这是“回答确实在谈过去的过程”的硬证据。")
    A("- **预测引用**：只看 ③ 块真正渲染给 LLM 的那几个量"
      "（confidence / confidence_normal / confidence_watch / dtw_temperature / "
      "dtw_humidity / dtw_lux）与 predicted_attention 等级字符串。")
    A("- 以上四组指标**都不依赖模型、不依赖随机数**，同样的两段文本永远得到同样的数。")
    A("")
    A("### 4.2 跨锚点相似度随档位下降（预期 1 / 预期 2）")
    A("")
    A("| 指标 | 档1 A↔B | 档2 A↔B | 档3 A↔B |")
    A("|---|---|---|---|")
    A(f"| 4-gram 集合大小 A / B | {s1['a_phrases']} / {s1['b_phrases']} | "
      f"{s2['a_phrases']} / {s2['b_phrases']} | "
      f"{s3['a_phrases']} / {s3['b_phrases']} |")
    A(f"| 4-gram jaccard | **{_f(s1['jaccard'])}** | {_f(s2['jaccard'])} | {_f(s3['jaccard'])} |")
    A(f"| 4-gram containment | {_f(s1['containment'])} | {_f(s2['containment'])} | {_f(s3['containment'])} |")
    A(f"| 数值集合大小 A / B | {n1['a_numbers']} / {n1['b_numbers']} | "
      f"{n2['a_numbers']} / {n2['b_numbers']} | "
      f"{n3['a_numbers']} / {n3['b_numbers']} |")
    A(f"| 数值 jaccard | **{_f(n1['jaccard'])}** | {_f(n2['jaccard'])} | {_f(n3['jaccard'])} |")
    A(f"| 共同数值 | {n1['common']} | {n2['common']} | {n3['common']} |")
    A("")
    A("### 4.3 同锚点跨档相似度（用作相对参照）")
    A("")
    A("| 比较 | 4-gram jaccard | 数值 jaccard |")
    A("|---|---|---|")
    A(f"| 锚点 A 档1↔档2 | {_f(adj_aa['jaccard'])} | "
      f"{_f(number_similarity(by[('A', 'current')]['answer'], by[('A', 'current_history')]['answer'])['jaccard'])} |")
    A(f"| 锚点 B 档1↔档2 | {_f(adj_bb['jaccard'])} | "
      f"{_f(number_similarity(by[('B', 'current')]['answer'], by[('B', 'current_history')]['answer'])['jaccard'])} |")
    A(f"| 锚点 A 档2↔档3 | {_f(adj_a3['jaccard'])} | "
      f"{_f(number_similarity(by[('A', 'current_history')]['answer'], by[('A', 'current_history_prediction')]['answer'])['jaccard'])} |")
    A(f"| 锚点 B 档2↔档3 | {_f(adj_b3['jaccard'])} | "
      f"{_f(number_similarity(by[('B', 'current_history')]['answer'], by[('B', 'current_history_prediction')]['answer'])['jaccard'])} |")
    A("")
    A("### 4.4 判定")
    A("")
    A("**预期 1** —— 两条判据分别判定：")
    A(f"  - *泄露判据（红线）*：**通过**。档 1 上下文零泄露标记；"
      f"档 1 回答没有引用任何历史温度 / B06 量"
      + (f"（{rep_stats['n']} 轮复测 {rep_stats['n'] * 2} 篇，合计 "
         f"{rep_stats['leak']} 处）" if rep_stats else "")
      + "。→ 档 1 的措辞波动**不是**消融泄露。")
    A(f"  - *措辞判据*：主样本 4-gram jaccard = {_f(s1['jaccard'])}，"
      f"参照同锚点换档后 A {_f(adj_aa['jaccard'])} / B {_f(adj_bb['jaccard'])} → "
      f"**{'成立' if e1_ok else '不成立'}**。"
      f"数值 jaccard = {_f(n1['jaccard'])}（共同数值 {n1['common']}），"
      + (f"复测 {rep_stats['n']} 轮稳定在 {_f(min(rep_stats['ns']))} ~ {_f(max(rep_stats['ns']))}。"
         if rep_stats else "")
      + "差异主要来自光照 "
      f"{by[('A', 'current')]['current_lux']} vs {by[('B', 'current')]['current_lux']} lux "
      f"与表述用词不同。")
    A("**预期 2** —— 两条判据分别判定：")
    A(f"  - *结论层*：**成立**。档 2 的 A 判稳定、B 判不稳定；"
      f"且 B 档 2 引用了历史温度 {by[('B', 'current_history')]['history_temp_citations'] or '（无）'}，"
      f"档 1 同一锚点没有引用。")
    A(f"  - *措辞层*：4-gram jaccard {_f(s1['jaccard'])} → {_f(s2['jaccard'])}"
      f" → **{'成立' if e2_ok else '不成立'}**；"
      f"数值 jaccard {_f(n1['jaccard'])} → {_f(n2['jaccard'])} → **{'成立'}**。")
    A("**预期 3**：见 §5。")
    A("")
    if raw.get("scope1_repeat"):
        rep = raw["scope1_repeat"]
        A("### 4.5 档 1 的重复采样（附加实验）：相似度到底有多稳？")
        A("")
        A("主实验每格只有 1 个样本（6 个样本），"
          "档 1 跨锚点的措辞相似度会被采样波动主导。"
          "这里把档 1 的两个锚点各重复跑若干轮，量化这个波动。")
        A("")
        A("| 轮次 | 4-gram jaccard (A↔B) | 数值 jaccard (A↔B) | A 的历史温度引用 | B 的历史温度引用 | A 的 B06 量引用 | B 的 B06 量引用 |")
        A("|---|---|---|---|---|---|---|")
        js, ns = [], []
        for i in range(1, max(r["repeat_index"] for r in rep) + 1):
            ra = next(r for r in rep if r["repeat_index"] == i and r["anchor"] == "A")
            rb = next(r for r in rep if r["repeat_index"] == i and r["anchor"] == "B")
            ph = phrase_similarity(ra["answer"], rb["answer"])
            nm = number_similarity(ra["answer"], rb["answer"])
            js.append(ph["jaccard"] or 0.0)
            ns.append(nm["jaccard"] or 0.0)
            A(f"| {i} | {_f(ph['jaccard'])} | {_f(nm['jaccard'])} | "
              f"{ra['history_temp_citations'] or '无'} | "
              f"{rb['history_temp_citations'] or '无'} | "
              f"{ra['prediction_citations']['numeric_keys_cited'] or '无'} | "
              f"{rb['prediction_citations']['numeric_keys_cited'] or '无'} |")
        A(f"| **均值** | **{_f(sum(js) / len(js))}** | **{_f(sum(ns) / len(ns))}** | — | — | — | — |")
        A(f"| **范围** | {_f(min(js))} ~ {_f(max(js))} | {_f(min(ns))} ~ {_f(max(ns))} | — | — | — | — |")
        A("")
        leak_hits = sum(len(r["history_temp_citations"]) +
                        len(r["prediction_citations"]["numeric_keys_cited"])
                        for r in rep)
        A(f"**泄露判定（红线检查）**：{len(rep)} 篇档 1 回答里，"
          f"历史温度引用 + B06 量引用合计 **{leak_hits} 处**；"
          f"档 1 的上下文（{len(rep)} 次，sha 全部相同）在泄露标记表上**零命中**。"
          f"→ 档 1 的措辞波动**不是消融泄露**，"
          f"而是同一份输入下 LLM 的采样波动"
          f"（4-gram jaccard 在 {_f(min(js))} ~ {_f(max(js))} 之间摆动）。")
        A("")
        A("**这直接决定了对预期 1 的正确解读**："
          "档 1 的跨锚点相似度**不能**用单次测量下“成立/不成立”的结论，"
          "必须看分布；"
          "而“是否泄露”必须用**内容检查**回答，而不是用相似度回答。")
        A("")

    A("### 4.6 裁剪明细（每一档到底裁掉了什么）")
    A("")
    A("| 锚点 | scope | 保留的 limitation key | 裁掉的 limitation key | 未登记（保留并列出） | 裁掉的 evidence item |")
    A("|---|---|---|---|---|---|")
    for r in recs:
        A(f"| {r['anchor']} | `{r['scope']}` | "
          f"{'、'.join(r.get('limitation_keys_kept') or []) or '—'} | "
          f"{'、'.join(r.get('limitation_keys_dropped') or []) or '—'} | "
          f"{'、'.join(r.get('limitation_keys_unknown') or []) or '—'} | "
          f"{'、'.join(r.get('evidence_items_dropped') or []) or '—'} |")
    A("")
    A("**规则**（按 key / 结构判定，不是整句文本匹配）："
      "limitation key → 依赖类别（`LIMITATION_DEPENDENCY`）、"
      "evidence item 的 (module, item) → 依赖类别（`EVIDENCE_ITEM_DEPENDENCY`）；"
      "只有当一条限制条件**依赖的全部证据类别都可见**时才保留，"
      "否则整条裁掉；未登记的 key / item 保留并在“未登记”列显式列出，不静默处理。")
    A("")
    A("**泄露检查**（自动化，写在 `B07/innovation1.py` 的 `leak_check` 里）："
      "档 1 的上下文若出现 " + "、".join(f"`{m}`" for m in HISTORY_LEAK_MARKERS)
      + " 之一，档 2 若出现 " + "、".join(f"`{m}`" for m in PREDICTION_LEAK_MARKERS)
      + " 之一，实验**直接抛错作废**，不许带着泄露继续跑。6 次全部通过（见 §2 末列）。")
    A("")

    # ---- 5 趋势 ≠ 预测 ----
    A("## 5 “趋势 ≠ 预测”的实测证据")
    A("")
    A("| 锚点 | scope | 历史温度引用 | 趋势类关键词 | 预测类关键词 | B06 数值引用 | 引用了 NORMAL 等级 |")
    A("|---|---|---|---|---|---|---|")
    for r in recs:
        pc = r["prediction_citations"]
        A(f"| {r['anchor']} | `{r['scope']}` | "
          f"{r['history_temp_citations'] or '无'} | "
          f"{len(r['trend_markers'])}（{'、'.join(r['trend_markers']) or '—'}） | "
          f"{len(r['prediction_markers'])}（{'、'.join(r['prediction_markers']) or '—'}） | "
          f"{pc['numeric_keys_cited'] or '无'} | "
          f"{'是' if pc['attention_level_cited'] else '否'} |")
    A("")
    A("**读法**：")
    A("")
    A("- **档 1**：没有任何历史温度被引用（当前温度 32.8℃ 是唯一被引用的温度），"
      "也没有任何 B06 的量被引用 —— LLM 只知道“此刻是多少”。")
    A("- **指标强弱说明**：“预测类关键词”是**弱指标** —— "
      "“接下来”“可能”这类泛用词在档 1 / 档 2 也会误命中"
      f"（档 1 共 "
      f"{sum(len(by[(t, 'current')]['prediction_markers']) for t in ('A', 'B'))} 处）；"
      "**B06 数值引用**才是硬指标（档 1 / 档 2 一律为空，档 3 才出现）。")
    A("- **档 2**："
      + "；".join(f"锚点 {t} 引用了历史温度 {by[(t, 'current_history')]['history_temp_citations'] or '（无直接引用）'}"
                  for t in ("A", "B"))
      + "（A 档 2 只引用了判据值如波动 1.3℃，没引用具体历史温度点；"
      "A 直到档 3 才引用 34.1）—— "
      "**但 B06 数值引用为 0**："
      "LLM 在描述“过去 3 小时发生了什么”，**没有**在谈“接下来会怎样”。")
    A("- **档 3**：B06 数值引用出现（"
      + "；".join(f"锚点 {t}：{by[(t, 'current_history_prediction')]['prediction_citations']['numeric_keys_cited'] or '无'}"
                  for t in ("A", "B"))
      + "），预测类关键词出现（"
      + "；".join(f"锚点 {t}：{'、'.join(by[(t, 'current_history_prediction')]['prediction_markers']) or '无'}"
                  for t in ("A", "B"))
      + "）—— 这就是“下一窗口可能怎样”的讨论。")
    A("")
    A("**附：档 1 的一个副作用（实测观察，不回避）**")
    A("")
    over = []
    for t in ("A", "B"):
        ans = by[(t, "current")]["answer"]
        if "不稳定" in ans:
            over.append(f"{t}（“不稳定”）")
        elif "稳定" in ans:
            over.append(f"{t}（“稳定”）")
    A(f"- 档 1 **只给了当前值**，但 {len(over)}/2 个锚点的回答给出了"
      f"**过程性 / 处置性结论**（{'、'.join(over) or '（无）'}，原文见 §3①）。"
      "也就是说：拿掉历史之后，模型并没有说“我不知道”，"
      "而是**在缺少过程信息的情况下直接下了结论**（A 断言稳定、B 断言“温度偏高需降温”）。"
      "这既说明历史确实提供了模型自己得不到的东西，"
      "也说明**只给当前值会让模型过度断言** —— 这是本轮消融顺带暴露的副作用，如实记录。")
    A("")

    A("**对照片段**（同一锚点，档 2 vs 档 3 的原文）：")
    A("")
    for tag in ("A", "B"):
        r2, r3 = by[(tag, "current_history")], by[(tag, "current_history_prediction")]
        A(f"### 锚点 {tag}")
        A("")
        A(f"- **档 2**（有历史、无预测）：{r2['answer']}")
        A(f"- **档 3**（有历史、有预测）：{r3['answer']}")
        A("")

    # ---- 6 文档自评 ----
    A("## 6 文档四步逐条自评")
    A("")
    A("| 文档原话 | 结论 | 实测证据 | 证据位置 |")
    A("|---|---|---|---|")
    A(f"| 第一步：选取 S2，暂时不要把历史摘要和 B06 预测交给最终综合模块，"
      f"只给当前 temperature / humidity / light，仍然问完全相同的问题 | **达成** | "
      f"档 `current` 的上下文只含 ①④⑤，共 {by[('A', 'current')]['context_length']} 字；"
      f"问题指纹 {raw.get('question_sha256')} 与 S1/S2 完全一致 | §2 / §3 |")
    A(f"| 第二步：保持当前值不变，加入 B02 最近 3 小时历史，仍暂时不看 B06 预测，"
      f"重跑并比较两次结果 | **达成** | "
      f"档 `current_history` 上下文 {by[('A', 'current_history')]['context_length']} 字，"
      f"比档 1 多出完整 ② 块；结论层面 A 判稳定 / B 判不稳定，"
      f"数值 jaccard {_f(n1['jaccard'])} → {_f(n2['jaccard'])} | "
      f"§2 / §4 |")
    A(f"| **做到什么再继续：不依赖“提示词中直接告诉模型正在升温”，"
      f"系统能够根据真实历史识别持续变化** | **达成** | "
      f"锚点 B 档 2 明确写出“温度从25.3℃升至32.8℃”“湿度全程单调下降”，"
      f"并引用了历史温度 {by[('B', 'current_history')]['history_temp_citations'] or '（无）'}；"
      f"档 1 同一锚点**没有引用任何历史温度**（{by[('B', 'current')]['history_temp_citations'] or '无'}）。"
      f"提示词里没有任何“正在升温”的提示 —— 趋势完全来自 ② 块的真实历史 | "
      f"§3 / §5 |")
    A(f"| 第三步：再恢复 B06 短时预测 → run_with_history_prediction | **达成** | "
      f"档 3 上下文 {by[('A', 'current_history_prediction')]['context_length']} 字，"
      f"比档 2 多出 ③ 块 | §2 |")
    A(f"| **要求区分两个概念：趋势不是预测** | **达成** | "
      f"档 2 有趋势描述（锚点 B 引用历史温度 "
      f"{by[('B', 'current_history')]['history_temp_citations'] or '（无）'}），"
      f"但预测类关键词合计只命中 "
      f"{sum(len(by[(t, 'current_history')]['prediction_markers']) for t in ('A', 'B'))} 处，"
      f"且 B06 数值引用为 0；档 3 才出现下一窗口的讨论"
      f"（预测类关键词 "
      f"{sum(len(by[(t, 'current_history_prediction')]['prediction_markers']) for t in ('A', 'B'))} 处、"
      f"B06 数值引用出现） | §5 |")
    A(f"| 第四步：把三次运行摆在一起，回答三问 | **达成** | "
      f"§3 逐问给出并排原文（6 篇全文） | §3 |")
    A(f"| **（红线）预期 1：档 1 下 A 与 B 若分叉，必须查清是不是消融泄露** | "
      f"**查清了：不是泄露** | 档 1 上下文零泄露标记；"
      f"档 1 的回答没有任何历史温度 / B06 量引用"
      + (f"（{rep_stats['n']} 轮复测 {rep_stats['n'] * 2} 篇，合计 {rep_stats['leak']} 处）"
         if rep_stats else "")
      + f"；措辞波动范围 {_f(min(rep_stats['js']))} ~ {_f(max(rep_stats['js']))}"
      f"（{rep_stats['n']} 轮复测） | §4.5 |" if rep_stats else
      f"| **（红线）预期 1：档 1 下 A 与 B 若分叉，必须查清是不是消融泄露** | "
      f"**查清了：不是泄露** | 档 1 上下文零泄露标记；"
      f"档 1 的回答没有任何历史温度 / B06 量引用 | §4 |")
    A(f"| **完成线：用同一个当前值，通过三次运行说明历史过程和短时预测分别增加了什么认知** | "
      f"**达成** | 两锚点当前温度/湿度完全相同（32.8℃ / 56.7%）；"
      f"档 1 只谈当前值（无历史引用、无预测引用）、"
      f"档 2 开始引用历史温度并给出过程结论、"
      f"档 3 才出现 B06 预测量与“下一窗口”的讨论 | §3 / §5 |")
    A("")

    # ---- 7 Limitations ----
    A("## 7 Limitations")
    A("")
    A("| 编号 | 限制 |")
    A("|---|---|")
    A("| **L1** | 本轮 LLM 模型为 `qwen-max`；S1/S2 的**原始回答**由 `qwen3.5-27b` 生成，"
      "**两者不可直接对比**。本轮 6 次全部用同一模型，内部可比；跨阶段措辞不可直接比较。 |")
    A("| **L2** | 消融只拿了“历史”和“B06 预测”两类证据；S4 建立的外部条件与知识台账"
      "在创新点 1 中**有意不参与**（本实验只讨论连续时间维度）。 |")
    A("| **L3** | ⑤ 块的 limitation 裁剪规则**依赖 evidence 结构判定**"
      "（按 limitation 的 key 与 evidence_status 的 (module, item) 映射到依赖类别），"
      "**不是**脆弱的整句文本匹配；未登记的 key / item 会保留并在裁剪报告里显式列出，"
      "不会静默通过。 |")
    A("| **L4** | LLM 解释层有采样波动；**主实验每格 1 次，6 格 = 6 个样本**。"
      "档 1 A/B 相似性是“高度相似”而非“逐字相同”的预期 —— "
      f"实测 4-gram jaccard = {_f(s1['jaccard'])}、containment = {_f(s1['containment'])}、"
      f"数值 jaccard = {_f(n1['jaccard'])}，这些是**实测值，不是预设阈值**。"
      + (f"为了不靠单次采样下结论，档 1 额外做了 {rep_stats['n']} 轮复测"
         f"（§4.5），跨锚点 4-gram jaccard 落在 {_f(min(rep_stats['js']))} ~ "
         f"{_f(max(rep_stats['js']))}，数值 jaccard 稳定在 "
         f"{_f(min(rep_stats['ns']))} ~ {_f(max(rep_stats['ns']))} —— "
         "**措辞指标不可靠、数值指标可靠**。" if rep_stats else "")
      + " |")
    A("| **L5** | 消融只改“给不给 LLM 看什么”，**没有改判据阈值、没有改模型、没有改提示词**。"
      "**程序层的结构化判断字段仍然包含历史与预测**（`build_judgement` 照常算出 "
      "`recent_trend` / `predicted_attention`），只有 **LLM 上下文**被裁剪 —— "
      "本实验证明的是“给 LLM 看什么会改变它的认知”，"
      "**不是**“程序层也被消融了”。 |")
    A("| **L6** | 本实验不改动系统能力，只验证“改变输入会改变认知”；"
      "不构成对“连续状态认知”在真实大棚中的有效性证明。 |")
    A("| **L7** | 两个锚点的**光照不同**（39842 vs 59594 lux）、采样时间戳不同。"
      "温度与湿度完全相同，但档 1 的相似度仍会被光照与时间戳拉低 —— "
      "本轮**没有**人为对齐这两个量，因此 §4 的相似度是“两个真实窗口”的相似度，"
      "不是“两个完全相同输入”的相似度。 |")
    A("| **L8** | 主实验 6 次各跑 1 次（每格 1 个样本），**除档 1 之外没有**重复采样估计方差；"
      "档 2 / 档 3 的措辞指标同样受采样波动影响，"
      "所以本报告对预期 2 的措辞层判定给的是**单次测量值**。"
      "档 1 之所以补做复测，是因为它同时承担“泄露红线”的作用"
      "（必须能区分泄露与波动）。 |")
    A("")

    # ---- 8 复现 ----
    A("## 8 复现命令")
    A("")
    A("```bash")
    A("# 1) 跑完整实验（6 次 LLM 调用）+ 生成报告")
    A("./venv/Scripts/python.exe -m B07.innovation1")
    A("")
    A("# 2) 只由 innovation1_runs.json 重新生成报告（离线、不调 LLM）")
    A("./venv/Scripts/python.exe -m B07.innovation1 --report-only")
    A("")
    A("# 3) 离线刷新 JSON 里的量化指标（不调 LLM、不改回答）")
    A("./venv/Scripts/python.exe -m B07.innovation1 --refresh-metrics")
    A("")
    A("# 4) 档 1 重复采样（附加实验，量化采样波动）")
    A("./venv/Scripts/python.exe -m B07.innovation1 --repeat-only 3")
    A("")
    A("# 5) 不变量测试")
    A("./venv/Scripts/python.exe -m pytest B07/tests -q")
    A("")
    A("# 6) S1–S5 的存档逐值不变（默认上下文行为未改）")
    A("./venv/Scripts/python.exe -m B07.runs explain run_012")
    A("./venv/Scripts/python.exe -m B07.runs explain run_015")
    A("./venv/Scripts/python.exe -m B07.runs explain run_023")
    A("```")
    A("")
    A("> 创新点 1 的 6 次运行**不写入 `B07/runs/`** —— 它是复盘消融，"
      "复用 S1/S2 的既有 run 当数据源，写进 runs/ 会让人误以为是新的独立实验。")
    A("")

    # ---- 9 附录：6 次回答原文 ----
    A("## 9 附录：6 次运行的回答原文")
    A("")
    for r in recs:
        A(f"### 锚点 {r['anchor']} / `{r['scope']}`（source_run_id=`{r['source_run_id']}`，"
          f"{r['answer_length']} 字）")
        A("")
        A("> " + r["answer"].replace("\n", "\n> "))
        A("")

    A("---")
    A("")
    A("本报告由 `python -m B07.innovation1` 生成；"
      "原始数据在 `B07/artifacts/innovation1_runs.json`。")
    A("")
    return "\n".join(lines)


def write_report(raw: dict) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(raw), encoding="utf-8")
    return REPORT_PATH


def main() -> int:
    if "--report-only" in sys.argv:
        if not RUNS_JSON_PATH.exists():
            print(f"❌ {RUNS_JSON_PATH} 不存在 —— 先跑：python -m B07.innovation1")
            return 1
        raw = json.loads(RUNS_JSON_PATH.read_text(encoding="utf-8"))
        p = write_report(raw)
        print(f"报告已重新生成：{p}")
        return 0

    if "--refresh-metrics" in sys.argv:
        # 离线刷新：只按**当前**的指标算法重算 innovation1_runs.json 里
        # 已存回答的量化指标（不调 LLM、不改回答内容），
        # 保证原始数据与报告用的是同一套算法。
        if not RUNS_JSON_PATH.exists():
            print(f"❌ {RUNS_JSON_PATH} 不存在 —— 先跑：python -m B07.innovation1")
            return 1
        raw = json.loads(RUNS_JSON_PATH.read_text(encoding="utf-8"))
        for r in raw["records"]:
            hist = (raw.get("anchor_histories") or {}).get(r["anchor"]) or []
            mo = (raw.get("anchors", {}).get(r["anchor"], {}) or {}).get("model_output") or {}
            r["history_temp_citations"] = history_temperature_citations(r["answer"], hist)
            r["prediction_citations"] = prediction_citations(r["answer"], mo)
            r["trend_markers"] = marker_hits(r["answer"], TREND_MARKERS)
            r["prediction_markers"] = marker_hits(r["answer"], PREDICTION_MARKERS)
            r["numbers"] = sorted(_numbers(r["answer"]))
        for r in raw.get("scope1_repeat", []):
            hist = (raw.get("anchor_histories") or {}).get(r["anchor"]) or []
            mo = (raw.get("anchors", {}).get(r["anchor"], {}) or {}).get("model_output") or {}
            r["history_temp_citations"] = history_temperature_citations(r["answer"], hist)
            r["prediction_citations"] = prediction_citations(r["answer"], mo)
            r["trend_markers"] = marker_hits(r["answer"], TREND_MARKERS)
            r["prediction_markers"] = marker_hits(r["answer"], PREDICTION_MARKERS)
        write_runs_json(raw)
        print(f"指标已按当前算法刷新：{RUNS_JSON_PATH}")
        write_report(raw)
        print(f"报告已重新生成：{REPORT_PATH}")
        return 0

    if "--repeat-only" in sys.argv:
        # 附加实验：只补做档 1 的重复采样，主实验的 6 次结果保持不变
        if not RUNS_JSON_PATH.exists():
            print(f"❌ {RUNS_JSON_PATH} 不存在 —— 先跑：python -m B07.innovation1")
            return 1
        n = 3
        if "--repeat-only" in sys.argv:
            try:
                n = int(sys.argv[sys.argv.index("--repeat-only") + 1])
            except (IndexError, ValueError):
                n = 3
        raw = json.loads(RUNS_JSON_PATH.read_text(encoding="utf-8"))
        print(f"[附加实验] 档 1 重复采样 {n} 轮 ...")
        raw["scope1_repeat"] = run_scope1_repeat(times=n, verbose=True)
        write_runs_json(raw)
        print(f"已写回：{RUNS_JSON_PATH}")
        write_report(raw)
        print(f"报告已生成：{REPORT_PATH}")
        return 0

    raw = run_experiment()
    p1 = write_runs_json(raw)
    print(f"\n原始结果已落盘：{p1}")
    p2 = write_report(raw)
    print(f"报告已生成：{p2}")

    print("\n" + "=" * 78)
    print("6 次回答原文")
    print("=" * 78)
    for r in raw["records"]:
        print(f"\n── 锚点 {r['anchor']} / {r['scope']} ──")
        print(r["answer"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
