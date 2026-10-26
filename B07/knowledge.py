"""B07 S4-1 —— 知识证据接入层（B04 知识库 → 统一证据记录）

这一层只做一件事：**把 B05 的知识检索工具返回值包装成统一证据**。

规矩（来自 S4 文档 + B05 工具契约）：

  1. **必须走 B05.test_functions.search_tomato_knowledge**。
     不绕过 B05 直接 import B04.SPEC，也不自己重写 TF-IDF 检索逻辑 ——
     B05 的工具描述已经把"检索口径"定下来了，绕过它就等于另起一套。
  2. **知识库不含通用农业知识**。B04.SPEC.CHUNKS 共 12 条，全部是
     GreenMate 系统设计元知识（数据源定义、扰动参数、API 缓存策略等）。
     所以这里的 query 一律走**方法论语义**（"决策依据不完整有什么风险"
     "证据缺失时该怎么做"），**不假装在问农业问题**。
  3. **命中为空 ≠ 不可用**。全部 score == 0 说明"检索成功但知识库没有
     相关内容"，status 仍然是 ok，result 里明确写"无相关知识"。
     不许当成 unavailable（那是"检索失败"），更不许伪造内容。
  4. **citation 原样保留**：chunk_id / source_id / section / page / score /
     content 一律照抄工具返回，不改写、不合并、不排序重排。

默认 query 选 `过期数据导致温室调控决策基于过时信息` 的理由：

  - 它稳定命中 CH-003-02（SRC-003，"静默返回旧数据的危害"），
    实测 score 0.3627，在全部候选 query 里最高，且与第二名（0.0666）
    拉开一个量级 —— 命中是**有区分度**的，不是"随便哪个 chunk 都排第一"。
  - CH-003-02 的内容正好是本次 S4 要用的那条：
    "静默返回旧数据（phantom data）危害：…温室调控决策基于过时信息可能
     导致作物受损；问题隐蔽，潜伏数天才被发现"。
    它支持"**需要关注**"，但**不支持**"未来一定继续恶化" ——
    知识只能给方向性支撑，不能替代外部条件的实测判定。
  - top_k 在 3 / 5 / 12 三档下 CH-003-02 都排第一，命中稳定。
  - 用词是"决策依据是否过时"，属于方法论提问，不是假装在问农业知识。

硬约束：

  - 不改 B01 / B02 / B03 / B04 / B05 / B06 任何文件
  - 不改 SYSTEM_PROMPT、不改固定问题、不改判据阈值、不重训模型
  - 不接进任何既有流程（本轮只产出可独立调用的能力）
  - 不编造农业知识、不编造 citation

用法：

    from B07.knowledge import search_knowledge
    ev = search_knowledge("过期数据导致温室调控决策基于过时信息")
    print(ev["status"], ev["result"]["has_relevant"])

自检（打印候选 query 的命中表）：

    python -m B07.knowledge
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.evidence_registry import (
    EVIDENCE_STATUS_ERROR,
    EVIDENCE_STATUS_OK,
    EVIDENCE_STATUS_UNAVAILABLE,
    SOURCE_TYPE_B04_KNOWLEDGE,
    make_evidence,
    make_tool_result,
)

#: 知识来源标识（写进证据的 source 字段）
KNOWLEDGE_SOURCE = "B04/knowledge_base"

#: 默认 query —— 稳定命中 CH-003-02，理由见模块 docstring
DEFAULT_QUERY = "过期数据导致温室调控决策基于过时信息"

#: 默认 top_k（与 B05 工具默认值一致）
DEFAULT_TOP_K = 5

#: 候选 query 集（全部是方法论语义，不假装在问农业知识）
CANDIDATE_QUERIES = (
    "过期数据导致温室调控决策基于过时信息",
    "静默返回旧数据导致下游决策错误",
    "基于不完整数据的判断是否可靠",
    "决策依据不完整可能带来什么风险",
    "证据缺失时的正确做法",
)


# --------------------------------------------------------------------------
# 核心：检索 → 统一证据记录
# --------------------------------------------------------------------------

def search_knowledge(query: str = DEFAULT_QUERY,
                     top_k: int = DEFAULT_TOP_K) -> dict:
    """检索知识库并返回统一证据记录。

    Args:
        query: 检索词。方法论语义优先。
        top_k: 召回条数。

    Returns:
        证据记录（见 B07.evidence_registry.make_evidence）：
        {
          "source_type": "b04_knowledge",
          "source": "B04/knowledge_base",
          "time": <工具返回的 retrieved_at>,
          "status": "ok" | "error",
          "result": {
            "query": str,
            "top_k": int,
            "citations": [ {chunk_id, source_id, section, page, score, content}, ... ],
            "has_relevant": bool,        # 是否至少有一条 score > 0
            "note": str,                 # 命中情况说明（全 0 时写明"无相关知识"）
          },
          "error": None | str,
          "ext": {
            "citation": {...主命中 chunk...} | None,
            "retrieved": [ {chunk_id, source_id, score}, ... ],
            "question": query,
            "has_relevant": bool,
          },
        }

    说明：
      - 全部 score == 0 → status 仍为 "ok"，result["has_relevant"] = False，
        result["note"] 明确写"无相关知识"。**不改成 unavailable**
        （unavailable 意味着检索没跑成，两者不是一回事）。
      - 工具调用失败 → status = "error"，error 原样带出，不返回任何内容。
    """
    if not query or not str(query).strip():
        return make_evidence(
            make_tool_result(KNOWLEDGE_SOURCE, EVIDENCE_STATUS_ERROR, None,
                             "query 为空，无法检索"),
            SOURCE_TYPE_B04_KNOWLEDGE,
            {"citation": None, "retrieved": [], "question": query,
             "has_relevant": False},
        )

    # ---- 唯一的检索入口：B05 的工具 ----
    try:
        from B05.test_functions import search_tomato_knowledge
        raw = search_tomato_knowledge(query, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        return make_evidence(
            make_tool_result(KNOWLEDGE_SOURCE, EVIDENCE_STATUS_ERROR, None,
                             f"{type(e).__name__}: {e}"),
            SOURCE_TYPE_B04_KNOWLEDGE,
            {"citation": None, "retrieved": [], "question": query,
             "has_relevant": False},
        )

    if not isinstance(raw, dict):
        return make_evidence(
            make_tool_result(KNOWLEDGE_SOURCE, EVIDENCE_STATUS_ERROR, None,
                             f"检索返回类型异常：{type(raw).__name__}"),
            SOURCE_TYPE_B04_KNOWLEDGE,
            {"citation": None, "retrieved": [], "question": query,
             "has_relevant": False},
        )

    status = raw.get("status")
    if status != EVIDENCE_STATUS_OK:
        # 工具自己说没成功：原样带出，不编内容
        err = raw.get("error") or f"知识检索未成功（status={status}）"
        return make_evidence(
            make_tool_result(KNOWLEDGE_SOURCE, EVIDENCE_STATUS_UNAVAILABLE,
                             None, err, raw.get("retrieved_at")),
            SOURCE_TYPE_B04_KNOWLEDGE,
            {"citation": None, "retrieved": [], "question": query,
             "has_relevant": False},
        )

    # ---- citation 原样保留 ----
    citations = raw.get("citation")
    if not isinstance(citations, list):
        citations = []
    citations = [c for c in citations if isinstance(c, dict)]

    scores = []
    for c in citations:
        try:
            scores.append(float(c.get("score") or 0.0))
        except (TypeError, ValueError):
            scores.append(0.0)
    has_relevant = any(s > 0.0 for s in scores)

    if not citations:
        note = "知识检索没有召回任何切片"
    elif has_relevant:
        best = citations[0]
        note = (f"命中 {len(citations)} 条，最高分 {scores[0]:.4f}"
                f"（{best.get('chunk_id')}）；相关切片 score > 0"
                "（注意：字符级 TF-IDF 有共同字符即有非零相似度，"
                "score>0 不等于该知识库真的覆盖本主题）")
    else:
        note = (f"无相关知识：召回 {len(citations)} 条但全部 score = 0，"
                "说明该知识库没有与本 query 相关的内容"
                "（B04 知识库只含 GreenMate 系统设计元知识，不含通用农业知识）")

    top_citation = None
    if citations:
        t = citations[0]
        top_citation = {
            "chunk_id": t.get("chunk_id"),
            "source_id": t.get("source_id"),
            "section": t.get("section"),
            "page": t.get("page"),
            "score": t.get("score"),
            "content": t.get("content"),
        }

    retrieved = [
        {
            "chunk_id": c.get("chunk_id"),
            "source_id": c.get("source_id"),
            "score": c.get("score"),
        }
        for c in citations
    ]

    tr = make_tool_result(
        KNOWLEDGE_SOURCE, EVIDENCE_STATUS_OK,
        {
            "query": query,
            "top_k": top_k,
            "citations": citations,
            "has_relevant": has_relevant,
            "note": note,
        },
        None,
        raw.get("retrieved_at"),
    )

    return make_evidence(tr, SOURCE_TYPE_B04_KNOWLEDGE, {
        "citation": top_citation,
        "retrieved": retrieved,
        "question": query,
        "has_relevant": has_relevant,
    })


def search_table(queries=CANDIDATE_QUERIES, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """对一组候选 query 各跑一次检索，返回可直接打表的行。"""
    rows = []
    for q in queries:
        ev = search_knowledge(q, top_k=top_k)
        res = ev["result"] or {}
        for rank, c in enumerate(res.get("citations") or [], 1):
            rows.append({
                "query": q,
                "rank": rank,
                "chunk_id": c.get("chunk_id"),
                "source_id": c.get("source_id"),
                "section": c.get("section"),
                "page": c.get("page"),
                "score": c.get("score"),
                "content": c.get("content"),
                "status": ev["status"],
                "has_relevant": res.get("has_relevant"),
            })
        if not res.get("citations"):
            rows.append({
                "query": q, "rank": 0, "chunk_id": None, "source_id": None,
                "section": None, "page": None, "score": None, "content": None,
                "status": ev["status"], "has_relevant": res.get("has_relevant"),
            })
    return rows


# --------------------------------------------------------------------------
# 自检入口
# --------------------------------------------------------------------------

def _print_table(rows: list[dict]) -> None:
    cur_q = None
    for r in rows:
        if r["query"] != cur_q:
            cur_q = r["query"]
            print(f"\nQUERY: {cur_q}")
            print(f"  {'rank':<5}{'chunk_id':<12}{'source_id':<10}"
                  f"{'score':<10}section")
            print("  " + "-" * 72)
        if r["chunk_id"] is None:
            print("  （无召回）")
            continue
        print(f"  {r['rank']:<5}{str(r['chunk_id']):<12}{str(r['source_id']):<10}"
              f"{float(r['score']):<10.4f}{r['section']}")
        print(f"        content: {r['content']}")


def main() -> int:
    print("=" * 78)
    print("B07 S4-1 知识证据接入层 —— 自检（走 B05.search_tomato_knowledge）")
    print("=" * 78)

    print("\n[1] 候选 query 命中表")
    rows = search_table()
    _print_table(rows)

    print("\n[2] 默认 query 的统一证据记录")
    ev = search_knowledge(DEFAULT_QUERY)
    import json
    print(json.dumps(ev, ensure_ascii=False, indent=2))

    print("\n[3] 关键约束核对")
    ok = True
    if ev["status"] != "ok":
        print("  ❌ 默认 query 检索未成功")
        ok = False
    else:
        print("  ✅ 默认 query status = ok")

    top = (ev["ext"].get("citation") or {})
    if top.get("chunk_id") == "CH-003-02":
        print(f"  ✅ 命中 CH-003-02（score={top.get('score')}）")
    else:
        print(f"  ❌ 命中的不是 CH-003-02，而是 {top.get('chunk_id')}")
        ok = False

    if ev["result"].get("has_relevant"):
        print("  ✅ has_relevant = True")
    else:
        print("  ❌ has_relevant 不为 True")
        ok = False

    # 全部 score == 0 的情形：必须仍然是 ok，且明写"无相关知识"
    zero = search_knowledge("!!!???")
    if zero["status"] == "ok" and not zero["result"]["has_relevant"]:
        print(f"  ✅ 零命中（全部 score=0）→ status 仍为 ok，note="
              f"「{zero['result']['note'][:24]}…」")
    else:
        print(f"  ❌ 零命中情形处理不对：status={zero['status']}，"
              f"has_relevant={zero['result'].get('has_relevant')}")
        ok = False

    # 实测事实：农业类 query 并不是 score=0，而是拿到低分噪声命中
    # （字符级 TF-IDF 只要有共同字符就有非零相似度）。
    # 所以 score>0 **不等于**"知识库真的有相关农业知识" —— 如实记下来。
    agri = search_knowledge("番茄果实膨大期的磷肥施用剂量与灌溉配比")
    top_agri = agri["result"]["citations"][0]
    print(f"  ⚠️ 农业类 query 实测：has_relevant={agri['result']['has_relevant']}，"
          f"最高分={top_agri['score']:.4f}（{top_agri['chunk_id']}）")
    print("     —— 字符级 TF-IDF 有共同字符就有非零相似度，"
          "score>0 不等于知识库真的覆盖该主题；"
          "本知识库 12 条 chunk 全是系统设计元知识，不含通用农业知识。")

    print("\n" + "=" * 78)
    print("自检结果：✅ 全部通过" if ok else "自检结果：❌ 有未通过项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
