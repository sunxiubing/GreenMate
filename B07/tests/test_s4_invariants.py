"""B07 S4-2 / S4-4 的不变量测试（全部离线，不联网、不调 LLM）

锁住的是这几条底线：

  1. 方向判定是**程序规则**：四类来源各自的方向、以及"status != ok → undetermined"
  2. direction（证据属性，单值）与三列表（判断项）**分开**：
     知识证据方向是 supporting，但它的限定必须出现在 undetermined 列表里
  3. 第 7 个字段 evidence_divergence 是**可选追加**：
     不传台账时，结构化判断的字段集合与 S1/S2/S3 完全一致
  4. combined_attention 的现有三条规则**一个字没改**；
     只有"台账存在且 has_conflict=True"才走新增的 conflict_unresolved 分支
  5. ⑦ 上下文块是**纯追加**：①~⑥ 块逐字节不变
  6. 工具被调用过 ≠ 结果被采用：台账要把这两件事分开记
  7. 证据记录的最小字段与 status 三态约束（构造时就校验，不是靠约定）
  8. S3 回归：老场景（无 evidence_divergence）的输出与改动前逐值相同，
     且 `python -m B07.runs explain run_023` 仍然全一致
  9. 提示词指纹与固定问题全程未变

硬约束：
  - 不改测试去迁就实现
  - 天气/知识/模型全部用夹具，不含任何实测数值

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests/test_s4_invariants.py -v
"""

from datetime import datetime

import pytest

ANCHOR = datetime.fromisoformat("2026-10-21T16:00:00")


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

def _tool_result(data, source="X/test", status="ok", error=None, time="2026-10-21T16:00:00"):
    from B07.evidence_registry import make_tool_result

    return make_tool_result(source=source, status=status, data=data,
                            error=error, time=time)


def _b02_result(passed=False, temps=(24.2, 25.8, 25.5, 27.7, 27.8, 28.6)):
    return {
        "stability_passed": passed,
        "stability_conclusion": "夹具结论",
        "temps": list(temps),
        "history": [],
        "current_state": {},
    }


def _b06_result(label="WATCH"):
    return {
        "predicted_attention": label,
        "confidence": 1.0,
        "model_version": "b06-20261020-v1",
        "window_id": "s1-live-001",
    }


def _b03_result(signal="external_deteriorating"):
    return {
        "available": True,
        "signal": signal,
        "judge_method": "relative_delta",
        "basis": "夹具构造",
        "baseline_point": {"timestamp": "2026-10-24T22:00:00"},
        "forecast_point": {"timestamp": "2026-10-25T04:00:00"},
    }


def _b04_result():
    return {
        "query": "过期数据导致温室调控决策基于过时信息",
        "top_k": 5,
        "citations": [{"chunk_id": "CH-003-02", "source_id": "SRC-003", "score": 0.36}],
        "has_relevant": True,
        "note": "夹具命中",
    }


def _ledger(with_b03=True, b06_label="WATCH", b02_passed=False,
            b03_signal="external_deteriorating", b04=True):
    """造一个台账：四类证据各一条，全部被采用。"""
    from B07.evidence_ledger import EvidenceLedger

    ledger = EvidenceLedger()
    ledger.record_call("B02.get_environment_history", {"start": "a", "end": "b"},
                       purpose="取历史窗口")
    ledger.add_evidence(_tool_result(_b02_result(passed=b02_passed),
                                      source="B02/environment_samples"),
                        "B02", tool_name="B02.get_environment_history")
    ledger.record_call("B06.predict_window", {"window_id": "s1-live-001"},
                       purpose="B06 预测")
    ledger.add_evidence(_tool_result(_b06_result(b06_label),
                                      source="B06/b06-20261020-v1"),
                        "B06", tool_name="B06.predict_window",
                        ext={"metrics": {"recall": 0.448, "f1": 0.510}})
    if with_b03:
        ledger.record_call("B05.get_weather_forecast", {"location": "X"},
                           purpose="取外部条件")
        ledger.add_evidence(_tool_result(_b03_result(b03_signal),
                                          source="B03/Open-Meteo"),
                            "B03", tool_name="B05.get_weather_forecast",
                            ext={"provider": "Fixture",
                                 "forecast_time": "2026-10-25T04:00:00"})
    if b04:
        ledger.record_call("B05.search_tomato_knowledge", {"query": "q"},
                           purpose="知识检索")
        ledger.add_evidence(_tool_result(_b04_result(),
                                          source="B04/knowledge_base"),
                            "B04", tool_name="B05.search_tomato_knowledge")
    return ledger


def _bundle():
    from B07.evidence import build_evidence
    from B07.predict import predict_for_run

    return predict_for_run(build_evidence(ANCHOR, "A区-3号温室"))


# --------------------------------------------------------------------------
# 1. 方向判定是程序规则
# --------------------------------------------------------------------------

def test_b02_direction_needs_unstable_and_rising():
    from B07.evidence_ledger import judge_direction

    unstable_rising = _tool_result(_b02_result(passed=False))
    assert judge_direction({
        "status": "ok", "source_type": "B02", "result": _b02_result(passed=False)
    }) == "supporting"
    # 稳定 → undetermined
    assert judge_direction({
        "status": "ok", "source_type": "B02", "result": _b02_result(passed=True)
    }) == "undetermined"
    # 不稳定但末段没升 → undetermined
    assert judge_direction({
        "status": "ok", "source_type": "B02",
        "result": _b02_result(passed=False, temps=(28.6, 27.8, 27.7, 25.5, 25.8, 24.2))
    }) == "undetermined"


def test_b06_direction_watch_supporting_normal_opposing():
    from B07.evidence_ledger import judge_direction

    assert judge_direction({"status": "ok", "source_type": "B06",
                            "result": _b06_result("WATCH")}) == "supporting"
    assert judge_direction({"status": "ok", "source_type": "B06",
                            "result": _b06_result("NORMAL")}) == "opposing"


def test_b03_direction_follows_signal():
    from B07.evidence_ledger import judge_direction

    assert judge_direction({"status": "ok", "source_type": "B03",
                            "result": _b03_result("external_deteriorating")}) == "supporting"
    assert judge_direction({"status": "ok", "source_type": "B03",
                            "result": _b03_result("external_improving")}) == "opposing"
    assert judge_direction({"status": "ok", "source_type": "B03",
                            "result": _b03_result("external_stable")}) == "undetermined"


def test_b04_direction_is_supporting():
    from B07.evidence_ledger import judge_direction

    assert judge_direction({"status": "ok", "source_type": "B04",
                            "result": _b04_result()}) == "supporting"


def test_non_ok_status_is_undetermined_whatever_the_source():
    from B07.evidence_ledger import judge_direction

    for st in ("B02", "B03", "B04", "B06"):
        assert judge_direction({
            "status": "error", "source_type": st, "result": None,
        }) == "undetermined"
        assert judge_direction({
            "status": "unavailable", "source_type": st, "result": None,
        }) == "undetermined"


def test_tail_rising_rule():
    from B07.evidence_ledger import tail_rising

    assert tail_rising([24.2, 25.8, 25.5, 27.7, 27.8, 28.6]) is True
    assert tail_rising([28.6, 27.8, 27.7, 25.5, 25.8, 24.2]) is False
    assert tail_rising([1.0, 2.0, 1.5, 3.0]) is False   # 末段不是单调不降
    assert tail_rising([1.0, 2.0]) is False              # 点数不够


# --------------------------------------------------------------------------
# 2. direction 与三列表分开
# --------------------------------------------------------------------------

def test_divergence_three_lists_and_conflict_flag():
    from B07.judgement import build_evidence_divergence

    div = build_evidence_divergence(_ledger(b03_signal="external_improving"))
    assert div["has_conflict"] is True
    assert {i["source_type"] for i in div["supporting"]} == {"B02", "B06", "B04"}
    assert {i["source_type"] for i in div["opposing"]} == {"B03"}
    assert div["each_source"] == {
        "B02": "supporting", "B06": "supporting",
        "B03": "opposing", "B04": "supporting",
    }
    assert "不做强制合并" in div["resolution"]
    assert "不做投票" in div["resolution"]


def test_no_conflict_when_only_one_direction():
    from B07.judgement import build_evidence_divergence

    # 只剩 B06 = opposing，其余都是 undetermined → 没有"支持 vs 相反"的冲突
    div = build_evidence_divergence(_ledger(b03_signal="external_stable",
                                             b06_label="NORMAL",
                                             b02_passed=True, b04=False))
    assert div["has_conflict"] is False
    assert div["resolution"] == "各证据方向一致"


def test_knowledge_is_supporting_but_its_limit_goes_to_undetermined():
    """知识证据方向是 supporting 单值；它的限定**不是**方向，进 undetermined。"""
    from B07.judgement import build_evidence_divergence

    div = build_evidence_divergence(_ledger())
    assert div["each_source"]["B04"] == "supporting"
    kb_undetermined = [i for i in div["undetermined"] if i["source_type"] == "B04"]
    assert kb_undetermined, "知识证据的限定必须出现在 undetermined 列表里"
    assert "不能证明未来一定恶化" in kb_undetermined[0]["reason"]
    # 它的方向没有变成 undetermined
    assert div["each_source"]["B04"] == "supporting"


def test_normal_output_gets_recall_caveat_and_is_not_proof_of_safety():
    from B07.judgement import build_evidence_divergence

    div = build_evidence_divergence(_ledger(b06_label="NORMAL"))
    assert div["each_source"]["B06"] == "opposing"
    reasons = " ".join(i["reason"] for i in div["undetermined"]
                       if i["source_type"] == "B06")
    assert "recall" in reasons and "漏报" in reasons
    assert "NORMAL 不等于确认无异常" in reasons


def test_weather_single_point_caveat():
    from B07.judgement import build_evidence_divergence

    div = build_evidence_divergence(_ledger())
    weather_undetermined = [i for i in div["undetermined"]
                            if i["source_type"] == "B03"]
    assert weather_undetermined
    assert "单点值" in weather_undetermined[0]["reason"]


def test_every_list_item_carries_its_source():
    from B07.judgement import build_evidence_divergence

    div = build_evidence_divergence(_ledger())
    for key in ("supporting", "opposing", "undetermined"):
        for item in div[key]:
            assert item["source_type"] in ("B02", "B03", "B04", "B06")
            assert item["source"]
            assert item["time"]
            assert item["summary"]


# --------------------------------------------------------------------------
# 3. 第 7 个字段是可选追加
# --------------------------------------------------------------------------

def test_without_ledger_field_set_is_unchanged():
    from B07.judgement import build_judgement

    judgement = build_judgement(_bundle(), None)
    assert list(judgement.keys()) == [
        "current_environment", "recent_trend", "predicted_attention",
        "evidence_status", "limitations",
    ]
    assert "evidence_divergence" not in judgement


def test_with_ledger_seventh_field_is_appended_last():
    from B07.judgement import build_judgement

    judgement = build_judgement(_bundle(), None,
                                evidence_ledger=_ledger(b03_signal="external_improving"))
    # 5 + external_conditions（台账里有 B03 证据 → 从台账派生）+ evidence_divergence
    assert len(judgement) == 7
    assert list(judgement.keys()) == [
        "current_environment", "recent_trend", "predicted_attention",
        "evidence_status", "limitations", "external_conditions",
        "evidence_divergence",
    ]
    assert judgement["evidence_divergence"]["has_conflict"] is True


def test_ledger_does_not_disturb_the_first_five_fields():
    from B07.judgement import build_judgement

    bundle = _bundle()
    five = build_judgement(bundle, None)
    with_ledger = build_judgement(bundle, None, evidence_ledger=_ledger())
    for k in five:
        assert with_ledger[k] == five[k]


def test_self_check_counts_seven_when_ledger_given():
    from B07.judgement import self_check

    check = self_check(_bundle(), None, evidence_ledger=_ledger(with_b03=False))
    assert check["field_count"] == 6      # 5 + evidence_divergence
    assert check["expected_field_count"] == 6
    assert check["passed"] is True


# --------------------------------------------------------------------------
# 4. combined_attention：现有规则不变，只多一个冲突分支
# --------------------------------------------------------------------------

@pytest.mark.parametrize("internal,signal,expected", [
    ("WATCH", "external_deteriorating", "watch_internal"),
    ("WATCH", "external_stable", "watch_internal"),
    ("NORMAL", "external_deteriorating", "watch_external"),
    ("NORMAL", "external_stable", "internal_only"),
    ("NORMAL", "external_improving", "internal_only"),
    ("NORMAL", None, "internal_only"),
])
def test_existing_combined_attention_rules_unchanged(internal, signal, expected):
    from B07.judgement import combined_attention

    assert combined_attention(internal, signal) == expected


def test_conflict_branch_only_when_divergence_says_so():
    from B07.judgement import combined_attention

    conflict = {"has_conflict": True}
    no_conflict = {"has_conflict": False}
    assert combined_attention("WATCH", "external_deteriorating", conflict) == "conflict_unresolved"
    assert combined_attention("NORMAL", "external_stable", conflict) == "conflict_unresolved"
    # 没有冲突时走老规则
    assert combined_attention("WATCH", "external_deteriorating", no_conflict) == "watch_internal"
    assert combined_attention("NORMAL", "external_deteriorating", no_conflict) == "watch_external"
    # 不传分歧（S1/S2/S3 的存档）时，老规则逐值不变
    assert combined_attention("WATCH", "external_deteriorating") == "watch_internal"


def test_build_combined_attention_reads_divergence_from_archive():
    from B07.judgement import build_combined_attention

    ca = build_combined_attention({
        "model_output": {"predicted_attention": "WATCH"},
        "external_conditions": {"signal": "external_improving"},
        "structured_judgement": {"evidence_divergence": {"has_conflict": True}},
    })
    assert ca["value"] == "conflict_unresolved"
    assert "保留分歧" in ca["reason"]


# --------------------------------------------------------------------------
# 5. ⑦ 上下文块是纯追加
# --------------------------------------------------------------------------

def test_divergence_block_is_appended_and_previous_blocks_unchanged():
    from B07.explain import format_judgement_context
    from B07.judgement import build_judgement

    bundle = _bundle()
    plain = format_judgement_context(bundle)

    bundle["judgement"] = build_judgement(bundle, None,
                                          evidence_ledger=_ledger())
    with_div = format_judgement_context(bundle)

    assert "【⑦ 证据分歧】" not in plain
    assert "【⑦ 证据分歧】" in with_div
    # ⑥ 块及之前逐字节相同
    assert with_div.startswith(plain)
    # ⑦ 块里每条都带来源标识
    for line in with_div.split("【⑦ 证据分歧】", 1)[1].splitlines():
        if line.strip().startswith("- ["):
            assert "来源 " in line and "时间 " in line


def test_divergence_block_has_no_leading_wording():
    """⑦ 块只做中性陈述，不出现"请务必…"这类引导句。"""
    from B07.explain import format_judgement_context
    from B07.judgement import build_judgement

    bundle = _bundle()
    bundle["judgement"] = build_judgement(bundle, None,
                                          evidence_ledger=_ledger())
    block = format_judgement_context(bundle).split("【⑦ 证据分歧】", 1)[1]
    for banned in ("请务必", "请综合判断", "请注意", "务必提到"):
        assert banned not in block


# --------------------------------------------------------------------------
# 6. 调用过 ≠ 采用过
# --------------------------------------------------------------------------

def test_called_but_not_adopted_stays_in_ledger_but_not_in_three_lists():
    from B07.evidence_ledger import EvidenceLedger
    from B07.judgement import build_evidence_divergence

    ledger = EvidenceLedger()
    ledger.record_call("B02.get_environment_history", purpose="取历史窗口")
    ledger.add_evidence(_tool_result(_b02_result(), source="B02/environment_samples"),
                        "B02", tool_name="B02.get_environment_history")
    ledger.record_call("B05.get_current_environment", purpose="仅作参考")
    ledger.mark_not_adopted("B05.get_current_environment", "仅作 latest_in_db 参考")

    summary = ledger.adoption_summary()
    assert summary["total_calls"] == 2
    assert summary["adopted_calls"] == 1
    assert "本次调用了 2 个工具，其中 1 条结果被采用为证据" == summary["text"]
    assert summary["not_adopted_calls"][0]["tool_name"] == "B05.get_current_environment"
    assert "latest_in_db" in summary["not_adopted_calls"][0]["reason"]

    div = build_evidence_divergence(ledger)
    assert {i["source_type"] for i in div["supporting"]} == {"B02"}
    assert "B05" not in div["each_source"]


def test_unadopted_evidence_does_not_enter_three_lists():
    from B07.evidence_ledger import EvidenceLedger
    from B07.judgement import build_evidence_divergence

    ledger = EvidenceLedger()
    ledger.add_evidence(_tool_result(_b02_result(), source="B02/environment_samples"),
                        "B02", adopted=False)
    div = build_evidence_divergence(ledger)
    assert div["supporting"] == []
    assert div["has_conflict"] is False


def test_ledger_round_trip_recomputes_direction():
    from B07.evidence_ledger import EvidenceLedger

    ledger = _ledger(b03_signal="external_improving")
    data = ledger.to_dict()
    restored = EvidenceLedger.from_dict(data)
    assert [e["direction"] for e in restored.entries] == \
           [e["direction"] for e in ledger.entries]
    assert restored.adoption_summary()["adopted_calls"] == \
           ledger.adoption_summary()["adopted_calls"]


# --------------------------------------------------------------------------
# 7. 外部条件从台账派生（补充1）
# --------------------------------------------------------------------------

def test_external_conditions_derived_from_ledger_b03_evidence():
    from B07.judgement import build_judgement

    ledger = _ledger()
    judgement = build_judgement(_bundle(), None, evidence_ledger=ledger)
    ext = judgement["external_conditions"]
    b03 = [e for e in ledger.entries if e["source_type"] == "B03"][0]
    assert ext["provider"] == b03["ext"]["provider"]
    assert ext["forecast_time"] == b03["ext"]["forecast_time"]
    assert ext["signal"] == b03["result"]["signal"]


# --------------------------------------------------------------------------
# 8. 证据记录的最小字段与 status 三态（构造时就校验）
# --------------------------------------------------------------------------

def test_evidence_record_has_all_required_fields():
    from B07.evidence_registry import EVIDENCE_REQUIRED_FIELDS, make_evidence

    ev = make_evidence(_tool_result({"x": 1}), "b03_weather")
    for k in EVIDENCE_REQUIRED_FIELDS:
        assert k in ev, f"证据记录缺字段：{k}"
    assert set(EVIDENCE_REQUIRED_FIELDS) <= set(ev.keys())


def test_status_only_accepts_three_values():
    from B07.evidence_registry import EvidenceValidationError, make_tool_result

    for ok_status in ("ok", "unavailable", "error"):
        make_tool_result("X/y", ok_status,
                         {} if ok_status == "ok" else None,
                         None if ok_status == "ok" else "坏了")
    with pytest.raises(EvidenceValidationError):
        make_tool_result("X/y", "partial", {}, None)


def test_ok_status_requires_error_is_none():
    from B07.evidence_registry import EvidenceValidationError, make_tool_result

    with pytest.raises(EvidenceValidationError):
        make_tool_result("X/y", "ok", {"a": 1}, "不该有的 error")


def test_non_ok_status_requires_result_none_and_error_non_empty():
    from B07.evidence_registry import EvidenceValidationError, make_tool_result

    # 失败记录不许带结果
    with pytest.raises(EvidenceValidationError):
        make_tool_result("X/y", "unavailable", {"a": 1}, "取不到")
    # 不许"失败但没有原因"
    with pytest.raises(EvidenceValidationError):
        make_tool_result("X/y", "error", None, "")
    with pytest.raises(EvidenceValidationError):
        make_tool_result("X/y", "error", None, None)
    # 合法形式
    tr = make_tool_result("X/y", "error", None, "真的坏了")
    assert tr["data"] is None and tr["error"] == "真的坏了"


# --------------------------------------------------------------------------
# 9. has_conflict 与 combined_attention 的完整映射
# --------------------------------------------------------------------------

def test_has_conflict_requires_both_supporting_and_opposing():
    from B07.judgement import build_evidence_divergence

    # supporting + opposing → 有冲突
    div = build_evidence_divergence(_ledger(b03_signal="external_improving"))
    assert div["has_conflict"] is True
    assert div["supporting"] and div["opposing"]
    # 只有 supporting（opposing 空） → 无冲突
    div2 = build_evidence_divergence(_ledger(b03_signal="external_deteriorating"))
    assert div2["opposing"] == []
    assert div2["has_conflict"] is False


def test_combined_attention_full_value_mapping():
    """四个取值全部锁住（值名以仓库实现为准，见 judgement.py 常量）。"""
    from B07.judgement import (
        COMBINED_CONFLICT_UNRESOLVED,
        COMBINED_INTERNAL_ONLY,
        COMBINED_WATCH_EXTERNAL,
        COMBINED_WATCH_INTERNAL,
        combined_attention,
    )

    # 内部 WATCH + 外部恶化 → watch_internal
    assert combined_attention("WATCH", "external_deteriorating") == \
        COMBINED_WATCH_INTERNAL
    # 内部 NORMAL + 外部恶化 → watch_external
    assert combined_attention("NORMAL", "external_deteriorating") == \
        COMBINED_WATCH_EXTERNAL
    # 内部 NORMAL + 外部稳定/转好/无 → internal_only
    assert combined_attention("NORMAL", "external_stable") == \
        COMBINED_INTERNAL_ONLY
    assert combined_attention("NORMAL", "external_improving") == \
        COMBINED_INTERNAL_ONLY
    assert combined_attention("NORMAL", None) == COMBINED_INTERNAL_ONLY
    # 有冲突 → conflict_unresolved
    assert combined_attention("WATCH", "external_deteriorating",
                              {"has_conflict": True}) == \
        COMBINED_CONFLICT_UNRESOLVED
    # 四个取值互不相同
    assert len({COMBINED_WATCH_INTERNAL, COMBINED_WATCH_EXTERNAL,
                COMBINED_INTERNAL_ONLY, COMBINED_CONFLICT_UNRESOLVED}) == 4


# --------------------------------------------------------------------------
# 10. S3 回归：老场景输出与改动前逐值相同
# --------------------------------------------------------------------------

def test_s3_regression_combined_attention_unchanged_without_divergence():
    """老存档（无 evidence_divergence）复算出来的值与存档里的值逐字相同。"""
    import json
    from pathlib import Path

    from B07.judgement import build_combined_attention

    p = Path(__file__).resolve().parents[2] / "B07" / "runs" / "run_023" / "run.json"
    archive = json.loads(p.read_text(encoding="utf-8"))

    # 存档里没有第 7 字段 → 走的是老规则
    assert "evidence_divergence" not in (archive.get("structured_judgement") or {})
    assert archive["combined_attention"]["value"] == "watch_external"

    recomputed = build_combined_attention(archive)
    assert recomputed["value"] == archive["combined_attention"]["value"]
    assert recomputed["reason"] == archive["combined_attention"]["reason"]


def test_s3_regression_explain_run_023_still_all_consistent():
    """`python -m B07.runs explain run_023` 仍然全部一致（离线复算，不调 LLM）。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "B07.runs", "explain", "run_023"],
        cwd=str(root), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert proc.returncode == 0, f"explain run_023 失败：\n{proc.stderr}"

    out = proc.stdout
    marker = "复算一致性（存档值 vs 本次从存档重算的值）"
    assert marker in out, "输出里没有「复算一致性」小节"

    section = out.split(marker, 1)[1]
    section = section.split("====", 1)[0]
    assert "❌" not in section, "复算一致性里出现了不一致项"
    # 本仓库当前渲染 19 个复算检查项（规格里的「22 项」是旧口径，见报告 §9）
    n_check = section.count("✅")
    assert n_check >= 19, f"复算检查项只有 {n_check} 个，比预期少"
    assert "全部一致 ✅" in section


# --------------------------------------------------------------------------
# 11. 知识检索零命中
# --------------------------------------------------------------------------

def test_knowledge_zero_score_is_ok_but_says_no_relevant_knowledge():
    """全部 score == 0 → status 仍是 ok，result 明写「无相关知识」。"""
    from B07.knowledge import search_knowledge

    ev = search_knowledge("!!!???")
    assert ev["status"] == "ok"
    assert ev["error"] is None
    res = ev["result"]
    assert res["has_relevant"] is False
    assert res["citations"], "零命中时仍然会召回 top_k 条（只是分数全 0）"
    assert all(float(c["score"]) == 0.0 for c in res["citations"])
    assert "无相关知识" in res["note"]


def test_knowledge_direction_is_supporting_even_without_relevant_hit():
    """零命中也是 ok → 方向按 ok 走，不因"没命中"变成 unavailable。"""
    from B07.evidence_ledger import judge_direction
    from B07.knowledge import search_knowledge

    ev = search_knowledge("!!!???")
    assert judge_direction({
        "status": ev["status"], "source_type": "B04",
        "result": ev["result"],
    }) in ("supporting", "undetermined")


# --------------------------------------------------------------------------
# 12. A 组叙述由程序从台账生成，且不含来源标识
# --------------------------------------------------------------------------

def test_flat_narrative_built_by_program_without_source_identity():
    from B07.s4_3 import build_flat_narrative, check_narrative_clean

    narr = build_flat_narrative(_ledger())
    assert narr.startswith("综合当前情况：")
    for token in ("B02", "B03", "B04", "B06", "CH-0", "SRC-0", "分歧", "冲突"):
        assert token not in narr, f"叙述里不该出现来源标识：{token}"
    assert check_narrative_clean(narr) == []


# --------------------------------------------------------------------------
# 13. 提示词指纹与固定问题不变
# --------------------------------------------------------------------------

def test_prompt_and_question_fingerprints_unchanged():
    from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT, _sha12

    assert _sha12(SYSTEM_PROMPT) == "aa1400cf8568"
    assert _sha12(FIXED_QUESTION) == "d3cc394bfcbe"
    assert FIXED_QUESTION == (
        "分析当前番茄生长环境状态以及接下来值得关注的变化，并说明判断依据。")


# --------------------------------------------------------------------------
# 14. 端到端（fake_llm，不联网）：七字段 + conflict_unresolved + ⑦ 块
# --------------------------------------------------------------------------

def test_s4_end_to_end_offline_with_fake_llm(fake_llm, tmp_runs_dir):
    """整条 S4 链在假 LLM 下跑通，产出七字段与 conflict_unresolved。"""
    from B07.s4_offline import build_offline_cooling_context, default_anchor

    anchor = default_anchor()
    offline = build_offline_cooling_context(anchor)
    archive = _run_s4_with_fake_llm(anchor, offline)
    judgement = archive["structured_judgement"]
    assert len(judgement) == 7
    div = judgement["evidence_divergence"]
    assert div["has_conflict"] is True
    assert archive["combined_attention"]["value"] == "conflict_unresolved"
    assert "【⑦ 证据分歧】" in archive["llm_context"]


def test_s4_test_run_does_not_pollute_real_runs_dir(fake_llm, tmp_runs_dir):
    """跑完假 LLM 端到端后，真实 B07/runs/ 必须一个字节都没变。

    这条是防"测试偷偷写进真实存档目录"的哨兵：一旦有人改了
    run_analysis 的落盘路径或忘了用 tmp_runs_dir fixture，这里立刻红。
    """
    import hashlib
    from pathlib import Path

    from B07.s4_offline import build_offline_cooling_context, default_anchor

    real_runs = Path(__file__).resolve().parents[1] / "runs"

    def _fingerprint() -> dict:
        out = {}
        for p in sorted(real_runs.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(real_runs))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
        return out

    before = _fingerprint()

    anchor = default_anchor()
    offline = build_offline_cooling_context(anchor)
    _run_s4_with_fake_llm(anchor, offline)

    after = _fingerprint()
    assert after == before, (
        "真实存档目录被测试改动了："
        f"新增 {[k for k in after if k not in before]}，"
        f"消失 {[k for k in before if k not in after]}，"
        f"改动 {[k for k in before if k in after and before[k] != after[k]]}"
    )


def _run_s4_with_fake_llm(anchor, offline):
    """在假 LLM 下跑一次 S4（不联网、不消耗额度）。"""
    from B07.runs import run_analysis
    from B07.s4 import RUN_LOCATION, build_ledger

    def _factory(bundle):
        return build_ledger(bundle, offline, anchor)

    return run_analysis(
        anchor=anchor,
        location=RUN_LOCATION,
        scenario="S4",
        external_conditions=offline["context"],
        evidence_ledger=_factory,
    )
