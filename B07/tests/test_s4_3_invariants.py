"""B07 S4-3 的不变量测试（全部离线，不联网、不调 LLM）

锁住的是这几条底线：

  1. A 组的"综合叙述"是**程序从台账拼出来的**纯函数结果，
     且不出现来源标识 / 时间戳 / 状态词 / 分歧字样
  2. A 组上下文 = ①②③ + ④A + ⑤，**没有 ⑥、没有 ⑦**
  3. 两组的 ①②③⑤ 块**逐字节相同**（唯一变量 = 证据组织方式）
  4. 断言检测器：只认多字短语；方向推断一级优先于二级；
     cited 判定要求方向一致
  5. 三个比率的分母口径：回答数含失败；断言数含全部断言
  6. explain.py 的 usage 会被存进 llm_meta

硬约束：
  - 不改测试去迁就实现
  - 天气/知识/模型全部用夹具，不含任何实测数值

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests/test_s4_3_invariants.py -v
"""

from datetime import datetime

import pytest

ANCHOR = datetime.fromisoformat("2026-10-21T16:00:00")


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

def _tool_result(data, source="X/test", status="ok", error=None,
                 time="2026-10-21T16:00:00"):
    from B07.evidence_registry import make_tool_result

    return make_tool_result(source=source, status=status, data=data,
                            error=error, time=time)


def _b02_result():
    return {
        "stability_passed": False,
        "stability_conclusion": "近 3 小时温度 24.2~28.6℃，温度波动 4.4℃ 超过阈值 2.0℃",
        "temps": [24.2, 25.8, 25.5, 27.7, 27.8, 28.6],
        "history": [],
        "current_state": {},
    }


def _b06_result():
    return {
        "predicted_attention": "WATCH",
        "confidence": 1.0,
        "model_version": "b06-20261020-v1",
    }


def _b03_result():
    return {
        "available": True,
        "signal": "external_improving",
        "judge_method": "relative_delta",
        "basis": "相对基线差分：降水概率 50.0% → 10.0%（Δ=-40.0 个百分点）",
        "baseline_point": {"timestamp": "2026-10-24T22:00"},
        "forecast_point": {"timestamp": "2026-10-25T04:00"},
    }


def _b04_result():
    return {
        "query": "过期数据导致温室调控决策基于过时信息",
        "top_k": 5,
        "citations": [{"chunk_id": "CH-003-02", "source_id": "SRC-003",
                       "score": 0.3627,
                       "content": "静默返回旧数据危害：温室调控决策基于过时信息；问题隐蔽"}],
        "has_relevant": True,
        "note": "命中 5 条",
    }


def _ledger():
    from B07.evidence_ledger import EvidenceLedger

    ledger = EvidenceLedger()
    ledger.add_evidence(_tool_result(_b02_result(), source="B02/environment_samples"), "B02")
    ledger.add_evidence(_tool_result(_b06_result(), source="B06/b06-20261020-v1"), "B06")
    ledger.add_evidence(_tool_result(_b03_result(), source="B03/Open-Meteo"), "B03",
                        ext={"provider": "Fixture", "forecast_time": "2026-10-25T04:00"})
    ledger.add_evidence(_tool_result(_b04_result(), source="B04/knowledge_base"), "B04")
    return ledger


# --------------------------------------------------------------------------
# 1. A 组叙述是程序拼出来的
# --------------------------------------------------------------------------

def test_flat_narrative_is_built_from_ledger():
    from B07.s4_3 import build_flat_narrative

    narr = build_flat_narrative(_ledger())
    assert narr.startswith("综合当前情况：")
    assert narr.endswith("。")
    # 四类证据的结论都在
    assert "内部历史显示" in narr
    assert "短时预测给出 WATCH" in narr
    assert "外部天气显示" in narr
    assert "相关知识提示" in narr


def test_flat_narrative_has_no_source_identity():
    """不得出现来源标识 / 时间戳 / 状态词 / 分歧字样。"""
    from B07.s4_3 import build_flat_narrative, check_narrative_clean

    narr = build_flat_narrative(_ledger())
    assert check_narrative_clean(narr) == []
    for token in ("B02", "B03", "B04", "B06", "CH-0", "SRC-0", "分歧", "冲突"):
        assert token not in narr


def test_flat_narrative_is_pure_function():
    """同一个台账 → 同一段叙述（不引入随机、不改台账）。"""
    from B07.s4_3 import build_flat_narrative

    led = _ledger()
    before = led.to_dict()
    a = build_flat_narrative(led)
    b = build_flat_narrative(led)
    assert a == b
    assert led.to_dict() == before


# --------------------------------------------------------------------------
# 2 & 3. 两组上下文的结构
# --------------------------------------------------------------------------

def _contexts():
    from B07.explain import format_judgement_context
    from B07.judgement import build_judgement
    from B07.s4_3 import build_context_a, build_flat_narrative

    from B07.evidence import build_evidence
    from B07.predict import predict_for_run

    evidence = build_evidence(ANCHOR, "A区-3号温室")
    bundle = predict_for_run(evidence)
    ledger = _ledger()
    judgement = build_judgement(bundle, evidence_ledger=ledger)
    bundle["judgement"] = judgement
    ctx_b = format_judgement_context(bundle)
    ctx_a = build_context_a(ctx_b, build_flat_narrative(ledger))
    return ctx_a, ctx_b


def test_group_a_has_no_block_6_and_7():
    from B07.s4_3 import split_blocks

    ctx_a, ctx_b = _contexts()
    a_heads = [b[:2] for b in split_blocks(ctx_a)]
    b_heads = [b[:2] for b in split_blocks(ctx_b)]
    assert "【⑥" not in a_heads
    assert "【⑦" not in a_heads
    assert "【⑥" in b_heads
    assert "【⑦" in b_heads


def test_shared_blocks_are_byte_identical():
    """①②③⑤ 两组逐字节相同 —— 唯一变量只能是 ④ 与 ⑥⑦。"""
    from B07.s4_3 import split_blocks

    ctx_a, ctx_b = _contexts()
    a = {b[:2]: b.rstrip("\n") for b in split_blocks(ctx_a)}
    b = {b[:2]: b.rstrip("\n") for b in split_blocks(ctx_b)}
    for key in ("【①", "【②", "【③", "【⑤"):
        assert a[key] == b[key], f"{key} 不相同"
    # ④ 必须不同
    assert a["【④"] != b["【④"]


# --------------------------------------------------------------------------
# 4. 断言检测器
# --------------------------------------------------------------------------

def test_claim_keywords_are_multi_char_only():
    from B07.s4_3_detector import CLAIM_KEYWORDS

    assert CLAIM_KEYWORDS
    for k in CLAIM_KEYWORDS:
        assert len(k) >= 2, f"单字短语：{k}"


def test_direction_inference_tier1_wins():
    from B07.s4_3_detector import infer_direction

    # 一级（明确判断词）优先
    assert infer_direction("未来一定会持续恶化") == "恶化"
    assert infer_direction("外部条件好转，必然会缓解") == "缓解"
    # 二级（趋势词）
    assert infer_direction("温度升高了") == "恶化"
    assert infer_direction("降水概率下降") == "缓解"
    # 两个方向都有 → 中性
    assert infer_direction("既会恶化也会好转") == "中性"
    assert infer_direction("状态平稳") == "中性"


def test_detect_claims_finds_assertion_sentences():
    from B07.s4_3_detector import detect_claims

    ans = "当前温度 28.6℃。未来一定会持续恶化，请立即干预。外部条件必然会好转。"
    claims = detect_claims(ans)
    assert len(claims) == 2
    assert claims[0]["direction"] == "恶化"
    assert claims[1]["direction"] == "缓解"


def test_cited_requires_matching_direction():
    """方向不一致的来源不算支持。"""
    from B07.s4_3_detector import judge_cited

    claims = [{"sentence": "未来一定会恶化", "keyword": "一定会", "direction": "恶化"}]
    # 上下文里只有 opposing（B03）→ 恶化断言拿不到 supporting → unsupported
    r = judge_cited(claims, {"supporting": [], "opposing": ["B03"],
                              "undetermined": []})
    assert r[0]["cited"] is False
    assert r[0]["verdict"] == "unsupported_assertion"
    # 有 supporting → cited
    r2 = judge_cited(claims, {"supporting": ["B02"], "opposing": ["B03"],
                               "undetermined": []})
    assert r2[0]["cited"] is True
    # 缓解断言需要 opposing
    relief = [{"sentence": "必然会好转", "keyword": "必然", "direction": "缓解"}]
    r3 = judge_cited(relief, {"supporting": ["B02"], "opposing": ["B03"],
                              "undetermined": []})
    assert r3[0]["cited"] is True


def test_sources_from_context_reads_block_7():
    from B07.s4_3_detector import sources_from_context

    ctx = ("【⑦ 证据分歧】\n"
           "各来源方向：B02=supporting、B03=opposing\n"
           "方向不一致：存在（支持 1 条 / 相反 1 条）\n"
           "支持提高关注的证据：\n"
           "  - [B02] 来源 B02/environment_samples ｜ 时间 2026-10-21T16:00:00\n"
           "    摘要：x\n"
           "提示可能缓解或反向的证据：\n"
           "  - [B03] 来源 B03/Open-Meteo ｜ 时间 2026-10-21T16:00:00\n"
           "    摘要：y\n"
           "当前仍不足以确定的部分：\n"
           "  - [B04] 来源 B04/knowledge_base ｜ 时间 2026-10-21T16:00:00\n"
           "    摘要：z\n"
           "分歧处理：xxx\n")
    src = sources_from_context(ctx)
    assert src == {"supporting": ["B02"], "opposing": ["B03"],
                    "undetermined": ["B04"]}
    # 没有 ⑦ 块 → 取不到任何可归因来源
    assert sources_from_context("【① 当前环境】\n温度 28.6℃") == {
        "supporting": [], "opposing": [], "undetermined": []}


def test_mentions_exclude_single_char_and_internal_metrics():
    from B07.s4_3_detector import detect_mentions

    # 内部指标不算外部来源命中
    m = detect_mentions("当前温度 28.6℃，湿度 66.9%，光照 42492 lux。")
    assert "外部天气" not in m["categories"]
    # 外部现象算
    m2 = detect_mentions("外部天气显示降水概率由 50% 降到 10%。")
    assert "外部天气" in m2["categories"]


# --------------------------------------------------------------------------
# 5. 比率口径
# --------------------------------------------------------------------------

def test_summary_denominators_include_failures():
    from B07.s4_3_detector import summarize

    recs = [
        {"status": "success", "claims_count": 2, "cited_count": 1,
         "unsupported_count": 1, "ledger_cited_count": 2,
         "attributed_in_text_count": 1, "has_claim": True,
         "mentions": {"count": 1, "categories": []},
         "conflict_mentions": {"count": 0}},
        {"status": "success", "claims_count": 0, "cited_count": 0,
         "unsupported_count": 0, "ledger_cited_count": 0,
         "attributed_in_text_count": 0, "has_claim": False,
         "mentions": {"count": 0, "categories": []},
         "conflict_mentions": {"count": 0}},
        {"status": "failed", "claims_count": 0, "cited_count": 0,
         "unsupported_count": 0, "ledger_cited_count": 0,
         "attributed_in_text_count": 0, "has_claim": False,
         "mentions": {"count": 0, "categories": []},
         "conflict_mentions": {"count": 0}},
    ]
    s = summarize(recs)
    assert s["total_answers"] == 3          # 含失败
    assert s["failed"] == 1
    assert s["claims_total"] == 2
    assert s["attribution_rate"] == 0.5
    assert s["attribution_rate_text"] == "1/2"
    assert s["unsupported_rate_text"] == "1/2"
    # 无断言率 = 成功且无断言的回答数 / 全部尝试数（失败也进分母）
    assert s["no_claim_answers"] == 1
    assert s["no_claim_rate_text"] == "1/3"


def test_summary_with_zero_claims_is_none_not_zero():
    from B07.s4_3_detector import summarize

    recs = [{"status": "success", "claims_count": 0, "cited_count": 0,
             "unsupported_count": 0, "ledger_cited_count": 0,
             "attributed_in_text_count": 0, "has_claim": False,
             "mentions": {"count": 0, "categories": []},
             "conflict_mentions": {"count": 0}}]
    s = summarize(recs)
    assert s["attribution_rate"] is None
    assert s["attribution_rate_text"] == "0/0"
    assert s["no_claim_rate"] == 1.0


# --------------------------------------------------------------------------
# 6. usage 进 llm_meta
# --------------------------------------------------------------------------

def test_usage_is_stored_in_llm_meta():
    from B07.explain import _usage_of

    class _Det:
        cached_tokens = 1234

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150
        prompt_tokens_details = _Det()

    class _Resp:
        usage = _Usage()

    u = _usage_of(_Resp())
    assert u["prompt_tokens"] == 100
    assert u["completion_tokens"] == 50
    assert u["total_tokens"] == 150
    assert u["cached_tokens"] == 1234


def test_usage_missing_fields_stay_none():
    from B07.explain import _usage_of

    class _Resp:
        usage = None

    u = _usage_of(_Resp())
    assert u == {"prompt_tokens": None, "completion_tokens": None,
                 "total_tokens": None, "cached_tokens": None}
