"""B08 第二遍 · 验证器的离线不变量测试（不调 LLM、不调天气 API）

★ 这一遍钉死的几件事：

  V1 **场景文件可校验**：五个场景文件齐全，scenario_sha256 能重算出来；
  V2 **回放日志带场景标识**：按场景文件回放出来的日志行带 scenario_id /
     scenario_file，且 base_run_id = 场景文件的 source_run_id（不是它自己）；
  V3 **判定重算是纯函数**：换 history 会让稳定性判定跟着变；
     去掉 external_conditions 会让合成关注等级跟着变（因果证明的底座）；
  V4 **LLM 文本检查用的是多字短语**，且否定 / 免责句不算命中
     （单字匹配会误伤 —— 这里钉住"多字"这个约束）；
  V5 **验证器有鉴别力**：把对的存档换成错的，判定必须翻成 FAIL。

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from B07 import b08
from B07.judgement import build_combined_attention, build_judgement
from B07.scenarios_build import scenario_digest, scenario_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = PROJECT_ROOT / "B07" / "scenarios"
SCENARIOS = ("S1", "S2", "S3", "S4", "S5")


def _load_scenario(sid: str) -> dict:
    return json.loads(scenario_path(sid).read_text(encoding="utf-8"))


def _load_run(run_id: str) -> dict:
    p = PROJECT_ROOT / "B07" / "runs" / run_id / "run.json"
    if not p.exists():
        pytest.skip(f"{run_id} 还没有存档")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# V1 场景文件
# --------------------------------------------------------------------------

def test_scenario_files_exist():
    for sid in SCENARIOS:
        assert scenario_path(sid).exists(), f"{sid} 场景文件缺失"


def test_scenario_sha256_recomputes():
    for sid in SCENARIOS:
        obj = _load_scenario(sid)
        assert obj["scenario_sha256"] == scenario_digest(obj), \
            f"{sid} 场景文件被手改过（sha256 对不上）"


def test_scenario_model_version_is_frozen():
    for sid in SCENARIOS:
        obj = _load_scenario(sid)
        assert obj["model_version"] == "b06-20261020-v1", \
            f"{sid} 的 model_version 不是冻结值"


def test_scenario_weather_identity_is_recorded():
    """天气身份三件套：要么原样来自存档，要么明确标注来源。"""
    for sid in SCENARIOS:
        snap = _load_scenario(sid).get("weather_evidence_snapshot") or {}
        for key in ("provider", "retrieved_at", "forecast_time"):
            if snap.get(key) is None:
                continue
            flag = f"{key}_recorded_in_archive"
            source = f"{key}_source"
            if not snap.get(flag, False):
                assert snap.get(source), \
                    f"{sid} 的 {key} 既不是存档原样记录的，也没写来源"


# --------------------------------------------------------------------------
# V2 回放日志
# --------------------------------------------------------------------------

def test_replay_log_has_scenario_rows():
    index = b08.replay_index()
    for sid in SCENARIOS:
        assert sid in index, f"{sid} 没有固定场景回放的日志行"


def test_replay_base_run_id_comes_from_scenario_file():
    """★ 本轮修的 bug：base_run_id 不能等于自己，得是场景文件的 source_run_id。"""
    index = b08.replay_index()
    for sid in SCENARIOS:
        row = index[sid]
        src = _load_scenario(sid)["source_run_id"]
        assert row["base_run_id"] == src, \
            f"{sid} 的 base_run_id={row['base_run_id']}，应为场景文件的 {src}"
        assert row["run_id"] != row["base_run_id"], \
            f"{sid} 的 base_run_id 指向了自己"


def test_replay_log_row_carries_scenario_file():
    for sid, row in b08.replay_index().items():
        assert row.get("scenario_file"), f"{sid} 的回放日志行没有场景文件路径"


# --------------------------------------------------------------------------
# V3 判定重算是纯函数（因果证明的底座）
# --------------------------------------------------------------------------

def test_history_swap_changes_stability():
    s1 = _load_scenario("S1")
    s2 = _load_scenario("S2")

    def stability(hist):
        j = build_judgement({
            "analysis_time": s1["analysis_time"],
            "current_state": s1["current_state"],
            "history": hist,
            "model_output": {"predicted_attention": "NORMAL",
                             "confidence": 0.9, "window_id": "w"},
            "scenario": s1["run_scenario"],
        }, external_conditions=None)
        return j["recent_trend"]["stability_verdict"]

    assert stability(s1["history"]) == "稳定"
    assert stability(s2["history"]) == "不稳定"


def test_dropping_external_conditions_changes_combined_attention():
    s3 = _load_scenario("S3")
    ec = (s3.get("weather_evidence_snapshot") or {}).get("external_conditions")
    assert ec, "S3 场景里没有外部条件"

    with_ec = build_combined_attention({
        "model_output": {"predicted_attention": "NORMAL",
                         "confidence": 0.9, "window_id": "w"},
        "external_conditions": ec,
    })
    without = build_combined_attention({
        "model_output": {"predicted_attention": "NORMAL",
                         "confidence": 0.9, "window_id": "w"},
        "external_conditions": None,
    })
    assert with_ec["value"] == "watch_external"
    assert without["value"] != "watch_external"


# --------------------------------------------------------------------------
# V4 LLM 文本检查：多字短语 + 否定/免责不算命中
# --------------------------------------------------------------------------

def test_consistency_phrases_are_multi_word():
    for p in b08.CONSISTENCY_PHRASES:
        assert len(p) >= 2, f"{p!r} 是单字短语，会误伤"


def test_consistency_phrase_detected():
    hits = b08.find_consistency_claims("各条证据都支持降温结论。")
    assert any(h["counted"] for h in hits)


def test_consistency_phrase_negated_is_not_counted():
    hits = b08.find_consistency_claims("证据之间并非都支持同一结论。")
    assert hits and not any(h["counted"] for h in hits)


def test_weather_fact_detected():
    hits = b08.find_weather_facts("外部天气显示明天将明显降温。")
    assert any(h["counted"] for h in hits)


def test_weather_disclaimer_is_not_counted():
    hits = b08.find_weather_facts("由于外部天气不可用，本次不采用天气结论。")
    assert not any(h["counted"] for h in hits)


def test_detector_markers_are_multi_word():
    for group in (b08.NEGATION_MARKERS, b08.DISCLAIMER_MARKERS,
                  b08.WEATHER_SUBJECT_PHRASES, b08.ASSERTION_MARKERS):
        for m in group:
            assert len(m) >= 2, f"{m!r} 是单字短语，会误伤"


# --------------------------------------------------------------------------
# V5 验证器有鉴别力
# --------------------------------------------------------------------------

def test_verifier_fails_on_mismatched_archive():
    """把 S1 的判定换成 S4 的存档 → 必须 FAIL（验证器不是恒真）。"""
    index = b08.replay_index()
    if "S1" not in index or "S4" not in index:
        pytest.skip("还没按场景回放过")
    s1_run = index["S1"]["run_id"]
    s4_run = index["S4"]["run_id"]
    records, ok = b08.verify(["S1"], {"S1": s4_run})
    assert not ok
    rec = records[0]
    assert rec["verdict"] == "FAIL"
    assert not all(c["passed"] for c in rec["checks"])
    assert rec["run_id"] == s4_run


def test_records_have_required_fields():
    """验证记录的结构（第二遍与第四遍回归报告共用）。"""
    required = ("scenario_id", "run_id", "app_version", "model_version",
                "analysis_time", "location", "current_state", "history_window",
                "weather_evidence", "knowledge_evidence", "model_evidence",
                "tool_run_status", "structured_judgement", "llm_explanation",
                "limitations", "checks", "counter_evidence", "verdict")
    index = b08.replay_index()
    if not index:
        pytest.skip("还没按场景回放过")
    for sid in SCENARIOS:
        if sid not in index:
            continue
        records, _ = b08.verify([sid])
        for rec in records:
            for key in required:
                assert key in rec, f"{sid} 的记录缺字段 {key}"
            assert rec["verdict"] in ("PASS", "FAIL")
            for c in rec["checks"]:
                for key in ("id", "claim", "expected", "actual", "passed",
                            "source"):
                    assert key in c, f"{sid} 的检查缺字段 {key}"
            for key in ("what", "expected", "actual", "passed", "note"):
                assert key in rec["counter_evidence"], \
                    f"{sid} 的反向对照缺字段 {key}"
