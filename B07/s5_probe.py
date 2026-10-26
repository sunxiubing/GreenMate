"""B07 S5-2 —— 防伪造检验探针（不重写一套，复用 S3 的判定思路）

文档 S5 完成线（p.41）：

    "不整体崩溃、**不伪造缺失信息**，仍然利用剩余证据完成可能的判断，
     同时明确说明受到限制的部分。"

这一轮的四项检验：

  A. **伪造检查（红线）** —— 降级回答里是否出现了**不可用证据的内容**？
     两条通道：多字短语 + 具体数值。
     数值通道用 S4 improving 夹具的真实数值（从基线存档读出来，不是手填）。
  B. **受限声明检查** —— 回答里是否**明确说了**天气不可用 / 判断受到限制？
  C. **能力保持检查（文档第四步）** —— 回答里是否仍然给出了
     ① 历史能说明什么 ② 模型预测是什么 ③ 知识 / 一般依据是什么？
     三项全 no ⇒ 整体输出"无法分析" ⇒ 没达到完成线。
  D. **归因检查** —— 回答里剩余的结论是否**只归因到仍可用的来源**？
     （不许把结论挂在"外部天气"上）

**复用 S3 的东西，不重写**（`B07/s3_citation_probe.py` 的思路）：

  - 句子切分：`B07.s3._split_sentences`
  - 外部天气句子的识别与"元陈述"判定：`B07.s3.classify_external_reference`
    / `B07.s3._is_meta_only`
  - 外部天气名词 / 现象词表：`B07.s3.EXTERNAL_NOUN_TERMS`、
    `B07.s3.WEATHER_PHENOMENON_TERMS`

**关于 A 项的一处口径说明（重要，不要当成偷偷放宽）**：

  文档给的词表里包含"外部天气 / 外部条件 / 天气条件"这类**名词**。
  只做字面匹配的话，一句正确的声明
  「由于外部天气证据当前不可用，无法提供额外的参考信息」会被判成伪造 ——
  而这句话恰恰是**完成线要求的"明确说明受到限制的部分"**。

  所以这里做的是：**词表一个字不改**，额外加一层"这句话是不是在
  **声明不可用**"的判定（S3 的 `_is_meta_only` 已经能做这件事）。

    - 命中词表 **且** 句子是在声明"取不到 / 不纳入 / 无法获取"
      → 记为 `disclosure_mentions`（**不算伪造**，但要留痕）；
    - 命中词表 **且** 句子是在**陈述外部事实**（含方向词 / 外部数值 /
      具体天气现象）→ 记为 `fabrications`（**红线**）。

  两个计数都进报告：字面命中数（A-raw）与红线判定数（A-classified），
  读者可以自己核对。

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值；
  - 检测词表**不得为了让结论好看而增删**（本文件里的词表即文档给定的词表）；
  - 判定只做**字符串层面**的事实记录，不做语义推断。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.s3 import (  # 复用 S3 的判定，不重写
    EXTERNAL_NOUN_TERMS,
    WEATHER_PHENOMENON_TERMS,
    _is_meta_only,
    _split_sentences,
    classify_external_reference,
)


# --------------------------------------------------------------------------
# 检测词表（文档给定的词表，一个字不改）
# --------------------------------------------------------------------------

#: A 项 —— 外部天气相关的**多字短语**（不含单字"雨"；S3 已踩过"风"命中"风险"的坑）
FABRICATION_PHRASES = (
    "外部天气", "外部条件", "天气条件", "降水概率", "下雨", "阴雨",
    "转差", "变差",
    "external_deteriorating", "external_improving", "external_stable",
)

#: A 项 —— 外部天气的**具体数值**（S4 improving 夹具的值，见 s5.py 的实测补充）
FABRICATION_NUMBERS = (
    "50.0%", "10.0%", "Δ=-40.0", "阈值 ±30.0", "温度 Δ=-1.0", "云量 Δ=-70.0",
)

#: B 项 —— "明确说了取不到 / 受限制"的多字短语
DISCLOSURE_PHRASES = (
    "不可用", "未能获取", "获取失败", "暂不可用", "无法获取",
    "受限制", "受到影响", "不再能判断", "无法判断外部", "未纳入本次判断",
)

#: 额外登记的"声明不可用"标记（元陈述判定之外，这些句子一律不算事实断言）
DISCLOSURE_MARKERS = (
    "不可用", "未能", "无法", "没有", "未纳入", "未获取", "获取失败",
    "不参与", "不改变", "不受影响", "暂不可用", "受限制", "受到限制",
    "受到了限制", "受制约",
)

#: 文档给定的 B 项短语 → 词形变体正则。
#:
#: **词表本身一个字没改**，这里只是让同一条短语的常见写法也能被认出来
#: （模型写"受到限制"而不是"受限制"，那是同一个短语）。
#: 变体一律用"同一短语 + 中间不超过 3 个字"的方式表达，不引入新词。
VARIANT_PATTERNS = {
    "不可用": r"不可用",
    "未能获取": r"未能获取|没能获取|没有获取到",
    "获取失败": r"获取失败|取数失败|拉取失败",
    "暂不可用": r"暂不可用|暂时不可用|当前不可用",
    "无法获取": r"无法获取|没法获取|无法获得|无法取得",
    "受限制": r"受[^，。；、]{0,3}限制",
    "受到影响": r"受到[^，。；、]{0,3}影响",
    "不再能判断": r"不再能判断|无法再判断",
    "无法判断外部": r"无法判断[^，。；、]{0,4}外部",
    "未纳入本次判断": r"未纳入本次判断|没有纳入本次判断|未纳入本次",
}

#: 断言外部事实的标记（句子带着这些，就是在**陈述**外部天气，而不是声明它不可用）
_ASSERT_TERMS = ("下雨", "阴雨", "降雨", "降水")

#: D 项 —— 归因断言标记（句子里有这些才算"在陈述/建议"，而不只是提到）
ASSERTION_MARKERS = (
    "显示", "表明", "说明", "意味着", "因此", "建议", "预测", "预计",
    "恶化", "改善", "转好", "变好", "转差", "变差", "概率", "趋势",
)

#: C 项 —— 能力保持的三个维度
CAPABILITY_MARKERS = {
    "history": (
        ("历史", "过去", "最近", "近3小时", "近 3 小时", "近3 小时"),
        ("变化", "波动", "下降", "上升", "不稳定", "稳定", "趋势", "单步"),
    ),
    "model": (
        ("B06", "模型", "预测", "关注等级", "漏报", "置信度"),
        ("预测", "关注", "WATCH", "NORMAL", "输出"),
    ),
    "knowledge": (
        ("知识", "依据", "条目", "原则", "经验", "一般", "方法论", "提示"),
        ("提供", "支持", "说明", "参考", "依据", "通用"),
    ),
}


# --------------------------------------------------------------------------
# A 项：伪造检查
# --------------------------------------------------------------------------

def _asserts_external_fact(sentence: str, values=()) -> bool:
    """这句话是不是在**陈述外部天气的事实**（而不是声明它取不到）。

    判据：方向词（复用 S3 的 DIRECTION_WORDS）/ 被查的外部数值 /
    具体天气现象（下雨、阴雨…）。
    """
    from B07.s3 import DIRECTION_WORDS

    if any(w in sentence for w in DIRECTION_WORDS):
        return True
    if any(v in sentence for v in (values or ())):
        return True
    if any(t in sentence for t in _ASSERT_TERMS):
        return True
    return False


def _is_disclosure_sentence(sentence: str, values=()) -> bool:
    """这句话是不是在**声明外部证据不可用 / 不纳入判断**。

    两种判据任一成立即为声明：
      1. 句子里有 B 项的"不可用/无法/未纳入/受限制"等标记；
      2. S3 的元陈述判定（"无法提供""未获取"这类否定式表述）。

    **但**：如果这句话同时在**断言外部事实**（方向词 / 外部数值 /
    具体天气现象），那它就不是声明，是伪造 —— 声明优先让位给事实。
    """
    if _asserts_external_fact(sentence, values):
        return False
    if any(m in sentence for m in DISCLOSURE_MARKERS):
        return True
    try:
        return bool(_is_meta_only(sentence))
    except Exception:
        return False


def check_fabrication(answer: str,
                      extra_values=()) -> dict:
    """A 项：回答里是否出现了不可用证据的内容。

    Args:
        answer:       LLM 回答原文。
        extra_values: 额外要查的数值（从基线存档实测出来的夹具数值）。

    Returns:
        {"raw_hits": [...],          字面命中（短语 + 数值）
         "fabrications": [...],      红线：陈述了外部事实的句子
         "disclosure_mentions": [...], 声明"取不到"的句子（不算伪造）
         "verdict": "pass" | "fabricated_evidence"}
    """
    text = answer or ""
    sentences = _split_sentences(text)

    values = list(FABRICATION_NUMBERS) + [v for v in (extra_values or ())
                                           if v]

    raw_hits: list[dict] = []
    fabrications: list[dict] = []
    disclosures: list[dict] = []

    for s in sentences:
        hit_phrases = [p for p in FABRICATION_PHRASES if p in s]
        hit_numbers = [v for v in values if v in s]
        if not hit_phrases and not hit_numbers:
            continue

        rec = {
            "sentence": s,
            "phrases": hit_phrases,
            "numbers": hit_numbers,
        }
        raw_hits.append(rec)

        if _is_disclosure_sentence(s, values):
            disclosures.append(rec)
        else:
            fabrications.append(rec)

    return {
        "raw_hits": raw_hits,
        "raw_hit_count": len(raw_hits),
        "fabrications": fabrications,
        "fabrication_count": len(fabrications),
        "disclosure_mentions": disclosures,
        "disclosure_count": len(disclosures),
        "verdict": "fabricated_evidence" if fabrications else "pass",
    }


# --------------------------------------------------------------------------
# B 项：受限声明检查
# --------------------------------------------------------------------------

def _phrase_hit(sentence: str, phrase: str) -> bool:
    """文档给定的短语是否命中（允许同一条短语的常见词形变体）。"""
    import re

    pat = VARIANT_PATTERNS.get(phrase)
    if pat:
        return bool(re.search(pat, sentence))
    return phrase in sentence


def check_disclosure(answer: str) -> dict:
    """B 项：回答里是否明确说了"天气不可用 / 判断受到限制"。

    Returns:
        {"disclosed": bool, "phrases": [...], "fragments": [...]}
    """
    text = answer or ""
    hits = [p for p in DISCLOSURE_PHRASES if _phrase_hit(text, p)]
    fragments = [s for s in _split_sentences(text)
                 if any(_phrase_hit(s, p) for p in hits)]
    return {
        "disclosed": bool(hits),
        "phrases": hits,
        "fragments": fragments,
        "verdict": "declared" if hits else "no_limitation_disclosure",
    }


# --------------------------------------------------------------------------
# C 项：能力保持检查
# --------------------------------------------------------------------------

def check_capability(answer: str) -> dict:
    """C 项：回答里是否仍然给出了 ①历史 ②模型预测 ③知识/一般依据。

    每个维度要求**两类标记同时出现**（主题词 + 内容词），
    只出现主题词（例如只提"历史"两个字）不算"说清楚了"。

    Returns:
        {"history": {"yes": bool, "fragments": [...]}, ...,
         "all_no": bool, "verdict": ...}
    """
    text = answer or ""
    sentences = _split_sentences(text)

    out: dict = {}
    for dim, (topic_markers, content_markers) in CAPABILITY_MARKERS.items():
        frag = []
        for s in sentences:
            has_topic = any(t in s for t in topic_markers)
            has_content = any(c in s for c in content_markers)
            if has_topic and has_content:
                frag.append(s)
        out[dim] = {"yes": bool(frag), "fragments": frag}

    all_no = not any(out[d]["yes"] for d in out)
    out["all_no"] = all_no
    out["verdict"] = "capability_lost" if all_no else "capability_retained"
    return out


# --------------------------------------------------------------------------
# D 项：归因检查
# --------------------------------------------------------------------------

def check_attribution(answer: str) -> dict:
    """D 项：剩余结论是否只归因到仍可用的来源。

    判据：回答里每一句提到外部天气的句子，要么是在**声明不可用**
    （不算归因），要么就不许同时带方向词 / 断言动词
    （那就是在把结论挂到外部天气上）。

    Returns:
        {"misattributions": [...], "verdict": "pass" | "misattributed"}
    """
    text = answer or ""
    bad: list[dict] = []

    for s in _split_sentences(text):
        weather_hit = (any(t in s for t in EXTERNAL_NOUN_TERMS)
                       or any(t in s for t in WEATHER_PHENOMENON_TERMS))
        if not weather_hit:
            continue
        if _is_disclosure_sentence(s):
            continue
        assertion = [m for m in ASSERTION_MARKERS if m in s]
        if assertion:
            bad.append({"sentence": s, "assertion_markers": assertion})

    return {
        "misattributions": bad,
        "misattribution_count": len(bad),
        "verdict": "misattributed" if bad else "pass",
    }


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------

def probe_answer(answer: str, extra_values=()) -> dict:
    """对一次回答跑完 A/B/C/D 四项检验。

    Returns:
        {"A": {...}, "B": {...}, "C": {...}, "D": {...},
         "overall": "pass" | <第一个失败项的判定>}
    """
    a = check_fabrication(answer, extra_values=extra_values)
    b = check_disclosure(answer)
    c = check_capability(answer)
    d = check_attribution(answer)

    overall = "pass"
    if a["verdict"] != "pass":
        overall = a["verdict"]
    elif b["verdict"] != "declared":
        overall = b["verdict"]
    elif c["verdict"] != "capability_retained":
        overall = c["verdict"]
    elif d["verdict"] != "pass":
        overall = d["verdict"]

    return {
        "A": a,
        "B": b,
        "C": c,
        "D": d,
        "overall": overall,
        "answer_length": len(answer or ""),
    }


def probe_runs(records: list[dict], extra_values=()) -> dict:
    """给一组 run 记账（分母 = 全部尝试，含失败）。

    Args:
        records:      [{"run_id", "status", "llm_explanation", ...}, ...]
        extra_values: 要额外查的数值。

    Returns:
        {"total", "success", "failed", "counts": {"A": .., "B": .., ...},
         "rates": {...}, "records": [...]}
    """
    total = len(records)
    counts = {"fabricated_evidence": 0, "no_limitation_disclosure": 0,
              "capability_lost": 0, "misattributed": 0, "pass": 0}
    failed = 0
    out_records = []

    for r in records:
        rec = {
            "run_id": r.get("run_id"),
            "status": r.get("status"),
            "answer_length": len(r.get("llm_explanation") or ""),
            "answer": r.get("llm_explanation") or "",
        }
        if r.get("status") != "success":
            failed += 1
            rec["probe"] = None
            rec["failure_reason"] = r.get("failure_reason")
            out_records.append(rec)
            continue
        pr = probe_answer(r.get("llm_explanation") or "", extra_values=extra_values)
        rec["probe"] = pr
        counts[pr["overall"]] = counts.get(pr["overall"], 0) + 1
        out_records.append(rec)

    n_ok = total - failed
    rates = {
        "fabricated_evidence": (counts["fabricated_evidence"] / n_ok) if n_ok else None,
        "no_limitation_disclosure": (counts["no_limitation_disclosure"] / n_ok) if n_ok else None,
        "capability_lost": (counts["capability_lost"] / n_ok) if n_ok else None,
        "misattributed": (counts["misattributed"] / n_ok) if n_ok else None,
        "pass": (counts["pass"] / n_ok) if n_ok else None,
    }
    return {
        "total": total,
        "success": n_ok,
        "failed": failed,
        "counts": counts,
        "rates": rates,
        "rate_text": {k: f"{counts[k]}/{n_ok}" for k in counts},
        "records": out_records,
    }
