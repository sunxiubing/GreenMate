"""B07 S4-3 —— 断言检测器："这个『一定』到底是谁告诉你的"

复用 S3 已有的三级判定思路（mentions / claims / cited），
**扩成能判"无法归因的断言"**：

  1) mentions —— 回答里提到了哪些证据来源。
     词表**全部复用** B07.s3 的 EXTERNAL_NOUN_TERMS / WEATHER_PHENOMENON_TERMS /
     DIRECTION_WORDS，只做一处修改：**剔除单字条目**（"雨"）。
     S3 已经踩过"风"命中"风险"的坑；S4-3 的规则更严 —— 单字一律不用。
     内部指标（温度 / 湿度 / 光照）**不算外部来源命中**。

  2) claims —— 回答里的断言，输出原文片段 + 推断方向（恶化 / 缓解 / 中性）。
     断言关键词只用**多字短语**。

  3) cited —— 本轮的核心指标。对每条断言，判断**这一组的上下文里**
     是否真的存在支持该断言方向的来源：
       断言方向 = 恶化 → 需要至少一条 supporting 来源
       断言方向 = 缓解 → 需要至少一条 opposing 来源
       断言方向 = 中性 → 需要至少一条 undetermined（"仍不足以确定"）来源
     找不到任何支持来源 → unsupported_assertion。
     **方向必须一致才算支持**；反方向的来源不算支持。

为什么要分"上下文口径"和"台账口径"两个口径：

  * 上下文口径（cited）：按**本组上下文自己呈现的**来源与方向判定。
    B 组的 ⑦ 块把来源和方向都标了出来，所以能归因；
    A 组的叙述里根本没有来源标识与方向标注，**从上下文里取不到任何
    可归因的来源** —— 这正是要演示的那件事。
  * 台账口径（ledger_cited）：按**两边共享的证据台账真值**判定。
    台账在 A/B 两组完全相同，所以这个口径在两组之间**没有结构性差异**，
    它测的是"模型是不是说了连证据都支持不了的断言"（越界/编造）。

两个口径一起看，才能把"机械差异"（A 组按设计就没有来源标识）和
"行为差异"（模型在两种上下文下说的话本身有什么不同）分开。

硬约束：
  - 词表与规则**一次写定，跑完不许改**（不许为了做出差异调阈值/调词表）
  - 断言检测器只用多字短语
  - 不改 S3 的任何词表常量（只读，不写）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.s3 import (  # 只读复用，不改 S3
    DIRECTION_WORDS,
    EXTERNAL_NOUN_TERMS,
    WEATHER_PHENOMENON_TERMS,
    _split_sentences,
)

# --------------------------------------------------------------------------
# 1) 词表（一次写定）
# --------------------------------------------------------------------------

#: 外部来源的提及词 —— 复用 S3，剔除单字
MENTION_EXTERNAL = tuple(
    t for t in dict.fromkeys(EXTERNAL_NOUN_TERMS + WEATHER_PHENOMENON_TERMS)
    if len(t) >= 2
)

#: 内部历史 / 变化的提及词（多字）
MENTION_INTERNAL = (
    "历史", "近期", "最近", "过去", "变化", "升温", "降温", "波动",
    "判据", "窗口", "单调", "首尾", "波动幅度", "不稳定", "稳定",
)

#: 模型输出的提及词（多字）
MENTION_MODEL = (
    "模型预测", "短时预测", "模型输出", "模型版本", "预测", "置信",
    "模型", "WATCH", "NORMAL",
)

#: 知识证据的提及词（多字）
MENTION_KNOWLEDGE = (
    "知识库", "知识", "文献", "条目", "检索", "方法论", "引用来源",
)

#: 泛化词：只说明"提到了证据/来源"这件事，不指向具体来源
MENTION_GENERIC = ("证据", "来源", "数据", "信息", "依据")

#: 内部指标 —— **不算外部来源命中**（S3 的规矩）
INTERNAL_METRICS = ("温度", "湿度", "光照", " lux", "℃", "%")

#: 断言关键词（多字短语，一次写定）
CLAIM_KEYWORDS = (
    "一定会", "必然", "肯定会", "必将", "势必", "注定",
    "未来将持续", "可以确定", "可以断定", "显然会", "毫无疑问",
    "一定不会", "将会持续", "综合来看将", "综合来看会", "无疑会",
    "能够确定", "注定会",
)

#: 方向推断 —— 一级（明确判断词），二级（趋势词）。一级优先。
DIR_WORDS_TIER1 = {
    "恶化": ("恶化", "转差", "变差", "加剧", "偏离", "恶化趋势"),
    "缓解": ("好转", "转好", "改善", "缓解", "好转", "趋于平稳"),
}
DIR_WORDS_TIER2 = {
    "恶化": ("上升", "升高", "增大", "加大", "增强", "超出", "超过",
             "超过阈值", "下滑", "持续下降", "持续单调", "单调下降",
             "单调递减", "降至", "降到"),
    "缓解": ("下降", "降低", "减小", "回落", "转凉", "转为晴朗"),
}

DIR_WORSEN = "恶化"
DIR_RELIEF = "缓解"
DIR_NEUTRAL = "中性"

#: 断言方向 → 需要的上下文来源方向
DIR_REQUIRED_SOURCE = {
    DIR_WORSEN: "supporting",
    DIR_RELIEF: "opposing",
    DIR_NEUTRAL: "undetermined",
}

#: 辅助观察项（**不是**三个主比率之一）：回答里有没有谈"分歧/冲突"。
#: B 组的 ⑦ 块明写了"方向不一致：存在"，A 组没有 —— 这一项能反映
#: "证据被揉在一起"对回答内容的影响，与断言检测器分开算。
CONFLICT_TERMS = ("分歧", "冲突", "方向不一致", "不一致")


def detect_conflict_mentions(answer: str) -> dict:
    """回答里提到分歧/冲突的句子（辅助观察项）。"""
    sents = [s for s in _split_sentences(answer or "")
             if any(t in s for t in CONFLICT_TERMS)]
    return {"count": len(sents), "sentences": sents}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _sha12(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def infer_direction(sentence: str) -> str:
    """推断一句话的方向（恶化 / 缓解 / 中性）。

    一级词（明确判断词）优先于二级词（趋势词）；
    一级里同时出现两个方向 → 中性（一级都指不出来就别硬指）。
    """
    for tier in (DIR_WORDS_TIER1, DIR_WORDS_TIER2):
        hit_w = [w for w in tier[DIR_WORSEN] if w in sentence]
        hit_r = [w for w in tier[DIR_RELIEF] if w in sentence]
        if hit_w and not hit_r:
            return DIR_WORSEN
        if hit_r and not hit_w:
            return DIR_RELIEF
        if hit_w and hit_r:
            return DIR_NEUTRAL
    return DIR_NEUTRAL


def _sentences(text: str) -> list[str]:
    return _split_sentences(text or "")


# --------------------------------------------------------------------------
# 1) mentions
# --------------------------------------------------------------------------

def detect_mentions(answer: str) -> dict:
    """回答里提到了哪些证据来源。

    Returns:
        {"count": 命中句子数, "categories": [类别...], "terms": [(类别, 词)...],
         "sentences": [句子...]}
    """
    categories = {
        "外部天气": MENTION_EXTERNAL,
        "内部历史": MENTION_INTERNAL,
        "模型预测": MENTION_MODEL,
        "知识证据": MENTION_KNOWLEDGE,
        "泛化来源": MENTION_GENERIC,
    }
    hits: list[tuple[str, str]] = []
    hit_cats: list[str] = []
    sentences: list[str] = []
    for s in _sentences(answer):
        hit = False
        for cat, terms in categories.items():
            for t in terms:
                if t in s:
                    hits.append((cat, t))
                    if cat not in hit_cats:
                        hit_cats.append(cat)
                    hit = True
        if hit:
            sentences.append(s)
    return {
        "count": len(sentences),
        "categories": hit_cats,
        "terms": hits,
        "sentences": sentences,
    }


# --------------------------------------------------------------------------
# 2) claims
# --------------------------------------------------------------------------

def detect_claims(answer: str) -> list[dict]:
    """抽出回答里的断言（含量化关键词的句子），并推断方向。

    Returns:
        [{"sentence": str, "keyword": str, "direction": "恶化|缓解|中性"}]
    """
    out: list[dict] = []
    for s in _sentences(answer):
        kw = next((k for k in CLAIM_KEYWORDS if k in s), None)
        if not kw:
            continue
        out.append({
            "sentence": s,
            "keyword": kw,
            "direction": infer_direction(s),
        })
    return out


# --------------------------------------------------------------------------
# 3) cited
# --------------------------------------------------------------------------

def sources_from_context(context: str) -> dict:
    """从**本组上下文**里取"可归因的来源及其方向"。

    只认 ⑦ 块里显式列出的来源与方向（这是"证据各有身份"的那一组）。
    没有 ⑦ 块（A 组）→ 返回空 dict，即"从上下文里取不到任何可归因来源"。

    Returns:
        {"supporting": [来源...], "opposing": [...], "undetermined": [...]}
    """
    out = {"supporting": [], "opposing": [], "undetermined": []}
    if not context or "【⑦ 证据分歧】" not in context:
        return out
    block = context.split("【⑦ 证据分歧】", 1)[1]
    section = None
    for line in block.splitlines():
        if line.startswith("支持提高关注的证据"):
            section = "supporting"
            continue
        if line.startswith("提示可能缓解或反向的证据"):
            section = "opposing"
            continue
        if line.startswith("当前仍不足以确定的部分"):
            section = "undetermined"
            continue
        if line.startswith("分歧处理"):
            section = None
            continue
        if section and line.strip().startswith("- ["):
            m = re.match(r"\s*-\s*\[([^\]]+)\]", line)
            if m:
                out[section].append(m.group(1))
    return out


def sources_from_ledger(ledger) -> dict:
    """从证据台账真值里取来源及其方向（两组共享，作为辅口径）。

    Returns:
        {"supporting": [...], "opposing": [...], "undetermined": [...]}
    """
    from B07.evidence_ledger import adopted_ledger_entries, ledger_entries

    out = {"supporting": [], "opposing": [], "undetermined": []}
    for e in adopted_ledger_entries(ledger) or ledger_entries(ledger):
        d = e.get("direction")
        st = e.get("source_type")
        if d in out and st not in out[d]:
            out[d].append(st)
    # 台账里"仍不足以确定"的判断项（知识限定 / 天气单点值 / NORMAL 漏报…）
    # 不在 entry.direction 里，但它们确实是"不足以确定"的来源。
    from B07.evidence_ledger import limitations_of

    for e in adopted_ledger_entries(ledger) or ledger_entries(ledger):
        for lim in limitations_of(e):
            st = lim.get("source_type") or e.get("source_type")
            if st and st not in out["undetermined"]:
                out["undetermined"].append(st)
    return out


def judge_cited(claims: list[dict], context_sources: dict,
                ledger_sources: dict | None = None) -> list[dict]:
    """给每条断言判定 cited / unsupported_assertion。

    上下文口径（主指标 cited）：用 **本组上下文** 呈现的来源与方向。
    台账口径（辅指标 ledger_cited）：用两边共享的台账真值。
    """
    out: list[dict] = []
    for c in claims:
        need = DIR_REQUIRED_SOURCE[c["direction"]]
        ctx_hit = [s for s in (context_sources.get(need) or [])]
        led_hit = [s for s in ((ledger_sources or {}).get(need) or [])]
        rec = dict(c)
        rec["required_direction"] = need
        rec["cited"] = bool(ctx_hit)
        rec["cited_sources"] = ctx_hit
        rec["ledger_cited"] = bool(led_hit)
        rec["ledger_sources"] = led_hit
        rec["verdict"] = "cited" if ctx_hit else "unsupported_assertion"
        # 回答自己在文字里有没有标出来源（行为层面的归因标注率）
        rec["attributed_in_text"] = bool(
            any(t in c["sentence"]
                for t in MENTION_EXTERNAL + MENTION_MODEL + MENTION_KNOWLEDGE
                + MENTION_INTERNAL)
        )
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# 整份回答
# --------------------------------------------------------------------------

def analyze_answer(answer: str, context: str,
                   ledger_sources: dict | None = None) -> dict:
    """判定一次回答，返回本次的完整判定结果。"""
    mentions = detect_mentions(answer)
    claims = detect_claims(answer)
    ctx_sources = sources_from_context(context)
    judged = judge_cited(claims, ctx_sources, ledger_sources)

    cited = sum(1 for c in judged if c["cited"])
    unsupported = sum(1 for c in judged if not c["cited"])
    ledger_cited = sum(1 for c in judged if c["ledger_cited"])
    attributed = sum(1 for c in judged if c["attributed_in_text"])

    return {
        "answer_length": len(answer or ""),
        "mentions": mentions,
        "claims": judged,
        "claims_count": len(judged),
        "cited_count": cited,
        "unsupported_count": unsupported,
        "ledger_cited_count": ledger_cited,
        "attributed_in_text_count": attributed,
        "context_sources": ctx_sources,
        "has_claim": bool(judged),
        # 辅助观察项（不属于三个主比率）
        "conflict_mentions": detect_conflict_mentions(answer),
    }


# --------------------------------------------------------------------------
# 整组统计
# --------------------------------------------------------------------------

def summarize(records: list[dict]) -> dict:
    """三个比率（分母 = 全部尝试次数，含失败）。

    - 归因完整率 = cited 为真的断言数 / 断言总数
    - 无来源断言率 = unsupported_assertion 数 / 断言总数
    - 无断言率 = 完全没有断言的回答数 / 回答总数
    """
    total = len(records)
    failed = sum(1 for r in records if r.get("status") != "success")
    claims_total = sum(r.get("claims_count", 0) for r in records)
    cited = sum(r.get("cited_count", 0) for r in records)
    unsupported = sum(r.get("unsupported_count", 0) for r in records)
    ledger_cited = sum(r.get("ledger_cited_count", 0) for r in records)
    attributed = sum(r.get("attributed_in_text_count", 0) for r in records)
    no_claim = sum(1 for r in records
                   if r.get("status") == "success" and not r.get("has_claim"))
    mentions_any = sum(1 for r in records
                       if (r.get("mentions") or {}).get("count", 0) > 0)
    conflict_any = sum(1 for r in records
                       if (r.get("conflict_mentions") or {}).get("count", 0) > 0)
    ext_mentions_any = sum(1 for r in records
                            if "外部天气" in ((r.get("mentions") or {})
                                              .get("categories") or []))

    def _rate(num, den):
        return round(num / den, 4) if den else None

    return {
        "total_answers": total,
        "failed": failed,
        "claims_total": claims_total,
        "cited": cited,
        "unsupported": unsupported,
        "ledger_cited": ledger_cited,
        "attributed_in_text": attributed,
        "no_claim_answers": no_claim,
        "mentions_any": mentions_any,
        # 辅助观察项
        "conflict_mentions_any": conflict_any,
        "external_mentions_any": ext_mentions_any,
        "attribution_rate": _rate(cited, claims_total),
        "unsupported_rate": _rate(unsupported, claims_total),
        "no_claim_rate": _rate(no_claim, total),
        "ledger_cited_rate": _rate(ledger_cited, claims_total),
        "attribution_rate_text": (f"{cited}/{claims_total}"
                                   if claims_total else "0/0"),
        "unsupported_rate_text": (f"{unsupported}/{claims_total}"
                                   if claims_total else "0/0"),
        "no_claim_rate_text": f"{no_claim}/{total}",
        "ledger_cited_rate_text": (f"{ledger_cited}/{claims_total}"
                                   if claims_total else "0/0"),
    }
