"""B07 创新点 1 的不变量测试（离线，不联网、不需要真实密钥）

覆盖的东西：
  1. `format_judgement_context(run_bundle)` 默认参数 ⇒ 与改动前**逐字节一致**
  2. `include` 的三档裁剪：块在不在、④ 块内部有没有跟着裁
  3. limitation 按 key 判定依赖（不是脆弱的整句文本匹配）
  4. evidence_status 的 items 与 summary 同步裁剪
  5. 消融泄露检查真的会报警
  6. 相似度指标（4-gram / 数值集合 Jaccard）的基本性质
  7. 6 次消融跑通（fake_llm），且**不写入 B07/runs/**
  8. 报告里必须有要求的章节

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from B07.explain import (
    BLOCK_MARKERS,
    EVIDENCE_ITEM_DEPENDENCY,
    LIMITATION_DEPENDENCY,
    format_judgement_context,
)
from B07.innovation1 import (
    ARTIFACTS_DIR,
    HISTORY_LEAK_MARKERS,
    PREDICTION_LEAK_MARKERS,
    RUNS_JSON_PATH,
    SCOPES,
    build_report,
    leak_check,
    number_similarity,
    phrase_similarity,
    run_experiment,
)
from B07.runs import RUNS_DIR, load_run

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ARTIFACTS_DIR / "innovation1_baseline_contexts.json"

ALL_BLOCKS = tuple(BLOCK_MARKERS)


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def s1_bundle():
    try:
        return load_run("run_012")
    except Exception:            # pragma: no cover - 存档不在就跳过
        pytest.skip("run_012 存档不可用")


@pytest.fixture(scope="module")
def s2_bundle():
    try:
        return load_run("run_014")
    except Exception:            # pragma: no cover
        pytest.skip("run_014 存档不可用")


# --------------------------------------------------------------------------
# 1. 默认行为逐字节不变
# --------------------------------------------------------------------------

def test_default_context_matches_pre_change_baseline(s1_bundle, s2_bundle):
    """默认参数下的上下文必须与**改动前**存下来的基线逐字节一致。

    基线是加 include 参数之前抓的（artifacts/innovation1_baseline_contexts.json）。
    """
    if not BASELINE_PATH.exists():
        pytest.skip("缺少改动前的基线上下文")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    for run_id in ("run_012", "run_015", "run_023"):
        if run_id not in baseline:
            continue
        bundle = load_run(run_id)
        now = format_judgement_context(bundle)
        assert now == baseline[run_id], f"{run_id} 的默认上下文被改动了"


def test_explicit_all_blocks_equals_default(s1_bundle, s2_bundle):
    """显式给出全部块标记，必须与默认输出逐字节一致。"""
    for bundle in (s1_bundle, s2_bundle):
        assert format_judgement_context(bundle) == \
            format_judgement_context(bundle, include=ALL_BLOCKS)


def test_default_include_is_none(s1_bundle):
    """默认参数就是 include=None —— S1–S5 的调用点一个字都没改。"""
    import inspect

    sig = inspect.signature(format_judgement_context)
    assert sig.parameters["include"].default is None


# --------------------------------------------------------------------------
# 2. 三档裁剪：块与块内部
# --------------------------------------------------------------------------

def test_scope_current_has_no_history_and_no_prediction(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        ctx = format_judgement_context(bundle, include=SCOPES["current"])
        assert "【②" not in ctx
        assert "【③" not in ctx
        assert "【①" in ctx and "【④" in ctx and "【⑤" in ctx
        # ④ 块里依赖 ②③ 的行必须一起消失
        assert "最近历史主要变化" not in ctx
        assert "predicted_attention" not in ctx
        assert "稳定判据" not in ctx


def test_scope_current_history_has_no_prediction(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        ctx = format_judgement_context(bundle, include=SCOPES["current_history"])
        assert "【②" in ctx
        assert "【③" not in ctx
        assert "predicted_attention" not in ctx
        assert "model_version" not in ctx
        assert "DTW" not in ctx


def test_scope_with_prediction_equals_default(s1_bundle, s2_bundle):
    """第三档恢复了 ①②③④⑤，对这两个没有 ⑥⑦ 数据的 run 就等于默认输出。"""
    for bundle in (s1_bundle, s2_bundle):
        ctx = format_judgement_context(
            bundle, include=SCOPES["current_history_prediction"])
        assert ctx == format_judgement_context(bundle)
        assert "【③" in ctx


def test_context_grows_with_scope(s1_bundle):
    """档位越高，上下文越长。"""
    lens = [len(format_judgement_context(s1_bundle, include=SCOPES[s]))
            for s in ("current", "current_history", "current_history_prediction")]
    assert lens[0] < lens[1] < lens[2]


# --------------------------------------------------------------------------
# 3. limitation 按 key 判定依赖
# --------------------------------------------------------------------------

def test_limitation_dependency_table_is_key_based():
    """依赖关系必须挂在 key 上，不能靠整句文本。"""
    assert LIMITATION_DEPENDENCY["simulated_data"] == frozenset()
    assert "prediction" in LIMITATION_DEPENDENCY["model_boundary"]
    assert "history" in LIMITATION_DEPENDENCY["time_anchor"]
    assert LIMITATION_DEPENDENCY["no_weather_no_kb"] == frozenset(
        {"history", "prediction"})


def test_simulated_data_limitation_always_kept(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        for scope in SCOPES:
            ctx = format_judgement_context(bundle, include=SCOPES[scope])
            assert "source_type='simulated'" in ctx


def test_model_boundary_only_with_prediction(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        ctx2 = format_judgement_context(bundle, include=SCOPES["current_history"])
        ctx3 = format_judgement_context(
            bundle, include=SCOPES["current_history_prediction"])
        assert "recall" not in ctx2 and "f1=" not in ctx2
        assert "recall" in ctx3


def test_time_anchor_only_with_history(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        ctx1 = format_judgement_context(bundle, include=SCOPES["current"])
        ctx2 = format_judgement_context(bundle, include=SCOPES["current_history"])
        assert "hours_gap" not in ctx1
        assert "hours_gap" in ctx2


def test_unknown_limitation_key_is_kept_and_reported(s1_bundle, monkeypatch):
    """未登记的 key：保留 + 显式列出，不静默处理。"""
    from B07 import explain as explain_mod

    orig = explain_mod._filter_limitations

    def spy(lim, cats):
        kept, dropped, unknown = orig(lim, cats)
        assert "mystery_key" in kept
        assert "mystery_key" in unknown
        assert "mystery_key" not in dropped
        return kept, dropped, unknown

    monkeypatch.setattr(explain_mod, "_filter_limitations", spy)

    lim = {"all_keys": ["simulated_data", "mystery_key"], "simulated_data": {},
           "mystery_key": {}}
    kept, dropped, unknown = explain_mod._filter_limitations(lim, set())
    assert "mystery_key" in kept and "mystery_key" in unknown
    assert "mystery_key" not in dropped


# --------------------------------------------------------------------------
# 4. evidence_status 的 items 与 summary 同步裁剪
# --------------------------------------------------------------------------

def test_evidence_items_filtered_by_module(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        ctx = format_judgement_context(bundle, include=SCOPES["current"])
        assert "[B02]current_state" in ctx
        assert "[B06]" not in ctx
        assert "B02.history" not in ctx


def test_evidence_summary_recomputed_not_reused(s1_bundle):
    """裁掉 B06 之后，summary 里的 "B06 2/2" 必须一起消失。"""
    full = format_judgement_context(s1_bundle)
    cropped = format_judgement_context(s1_bundle, include=SCOPES["current"])
    assert "B06" in full
    assert "B06" not in cropped


def test_evidence_item_dependency_table():
    assert EVIDENCE_ITEM_DEPENDENCY[("B06", "model_output")] == frozenset(
        {"prediction"})
    assert EVIDENCE_ITEM_DEPENDENCY[("B02", "history")] == frozenset({"history"})


# --------------------------------------------------------------------------
# 5. 消融泄露检查
# --------------------------------------------------------------------------

def test_leak_check_flags_history_leak(s1_bundle):
    ctx = format_judgement_context(s1_bundle, include=SCOPES["current_history"])
    res = leak_check(ctx, "current")
    assert res["leaked"] is True
    assert res["hits"]


def test_leak_check_clean_on_real_scopes(s1_bundle, s2_bundle):
    for bundle in (s1_bundle, s2_bundle):
        for scope, include in SCOPES.items():
            ctx = format_judgement_context(bundle, include=include)
            res = leak_check(ctx, scope)
            assert not res["leaked"], f"{scope} 泄露：{res['hits']}"


def test_leak_marker_lists_are_not_empty():
    assert HISTORY_LEAK_MARKERS and PREDICTION_LEAK_MARKERS
    assert "【②" in HISTORY_LEAK_MARKERS
    assert "【③" in PREDICTION_LEAK_MARKERS


# --------------------------------------------------------------------------
# 6. 相似度指标
# --------------------------------------------------------------------------

def test_phrase_similarity_of_identical_text_is_one():
    r = phrase_similarity("近三小时温度持续上升", "近三小时温度持续上升")
    assert r["jaccard"] == pytest.approx(1.0)
    assert r["containment"] == pytest.approx(1.0)


def test_phrase_similarity_of_disjoint_text_is_zero():
    r = phrase_similarity("aaaa", "bbbb")
    assert r["jaccard"] == pytest.approx(0.0)


def test_phrase_similarity_ignores_punctuation_and_space():
    a = phrase_similarity("温度 32.8℃，湿度 56.7%", "温度32.8℃，湿度56.7%")
    assert a["jaccard"] == pytest.approx(1.0)


def test_number_similarity():
    r = number_similarity("温度 32.8℃，波动 1.3℃", "波动 1.3℃，温度 32.8℃")
    assert r["jaccard"] == pytest.approx(1.0)
    assert r["common"] == [1.3, 32.8]
    r2 = number_similarity("温度 32.8℃", "温度 30.0℃")
    assert r2["jaccard"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# 7. 端到端（fake_llm）
# --------------------------------------------------------------------------

def test_run_experiment_offline_produces_six_records(fake_llm):
    """6 次消融跑通；答案用假 LLM；不碰真实 runs 目录。"""
    before = sorted(p.name for p in RUNS_DIR.glob("run_*")) if RUNS_DIR.exists() else []

    raw = run_experiment(verbose=False)

    assert len(raw["records"]) == 6
    assert {(r["anchor"], r["scope"]) for r in raw["records"]} == {
        (a, s) for a in ("A", "B") for s in SCOPES}
    for r in raw["records"]:
        assert r["answer"]
        assert r["context"]
        assert r["include"]
        assert not r["leak_check"]["leaked"]
        assert r["source_run_id"].startswith("run_")

    after = sorted(p.name for p in RUNS_DIR.glob("run_*")) if RUNS_DIR.exists() else []
    assert before == after, "创新点 1 的消融不允许写入 B07/runs/"


def test_experiment_contexts_differ_by_scope(fake_llm):
    """同一锚点下，三档上下文必须互不相同（否则消融没生效）。"""
    raw = run_experiment(verbose=False)
    by = {(r["anchor"], r["scope"]): r for r in raw["records"]}
    for tag in ("A", "B"):
        assert by[(tag, "current")]["context"] != \
            by[(tag, "current_history")]["context"]
        assert by[(tag, "current_history")]["context"] != \
            by[(tag, "current_history_prediction")]["context"]


def test_experiment_runs_json_is_not_in_runs_dir():
    assert "runs" not in RUNS_JSON_PATH.parts[-2]
    assert RUNS_JSON_PATH.parent == ARTIFACTS_DIR


# --------------------------------------------------------------------------
# 8. 报告章节
# --------------------------------------------------------------------------

def _fake_raw(fake_llm):
    return run_experiment(verbose=False)


def test_report_contains_required_sections(fake_llm):
    raw = _fake_raw(fake_llm)
    md = build_report(raw)
    for must in ("## 1 实验设计", "## 2 六次运行一览表", "## 3 文档第四步的三问",
                 "## 4 消融有效性检查", "## 5 “趋势 ≠ 预测”的实测证据",
                 "## 6 文档四步逐条自评", "## 7 Limitations", "## 8 复现命令",
                 "## 9 附录：6 次运行的回答原文"):
        assert must in md, f"报告缺少章节：{must}"
    for lim in ("**L1**", "**L2**", "**L3**", "**L4**", "**L5**", "**L6**"):
        assert lim in md, f"报告缺少 Limitation {lim}"


def test_report_includes_all_six_answers(fake_llm):
    raw = _fake_raw(fake_llm)
    md = build_report(raw)
    for r in raw["records"]:
        assert r["answer"] in md, f"{r['anchor']}/{r['scope']} 的回答没进报告"


def test_report_states_the_algorithm(fake_llm):
    """消融有效性检查必须写清算法，不能只给结论。"""
    raw = _fake_raw(fake_llm)
    md = build_report(raw)
    assert "4-gram" in md
    assert "jaccard" in md
    assert "containment" in md
