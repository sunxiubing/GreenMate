"""B08 第一遍 · 冻结基线的不变量测试（离线，不调 LLM、不调天气 API）

★ 这一遍钉死的四件事（文档 p.89–92）：

  F1 **版本冻结**：app_version / model_version / 知识版本 / 固定问题
     四样都有指纹，且指纹来自真实文件（不是手写常量）。
  F2 **场景冻结**：S1~S5 五个固定场景文件齐全，
     `scenario_sha256` 能重算出来（场景没被手改过），
     且内容仍与来源存档一致。
  F3 **只读存档**：run.json 里**不许出现** app_version 之类的新字段
     （app_version 只活在基线文件里，不许污染存档契约）。
  F4 **健康检查分层**：/health 永远 200 且不碰外部服务；
     /health/deep 逐项给出 ok | unavailable | error；
     B03 默认不发真实请求。

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from B07 import b08_baseline, health, scenarios_build
from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT, _sha12

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = PROJECT_ROOT / "B07" / "scenarios"
BASELINE_PATH = PROJECT_ROOT / "B07" / "artifacts" / "b08_baseline.json"

SCENARIO_IDS = ["S1", "S2", "S3", "S4", "S5"]


@pytest.fixture(scope="module")
def baseline() -> dict:
    assert BASELINE_PATH.exists(), (
        f"缺少基线快照 {BASELINE_PATH} —— 先跑 python -m B07.b08_baseline")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _load(scenario_id: str) -> dict:
    p = SCENARIOS_DIR / f"{scenario_id}.json"
    assert p.exists(), f"缺少固定场景文件 {p} —— 跑 python -m B07.scenarios_build"
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# F1 版本冻结
# --------------------------------------------------------------------------

def test_baseline_has_all_four_fingerprints(baseline):
    assert baseline["app_version"] == "B08_baseline_01"
    assert baseline["model"]["version"]
    assert baseline["model"]["artifact_manifest_sha256"]
    assert baseline["knowledge"]["combined_sha256"]
    assert baseline["prompt"]["question_sha256"]


def test_app_version_records_git_commit(baseline):
    commit = baseline["git"]["commit"]
    assert len(commit) == 40, f"不是完整 sha：{commit!r}"
    assert baseline["git"]["commit_short"]
    assert baseline["app_version_composition"]["full"].endswith(commit)


def test_dirty_flag_is_real(baseline):
    """dirty 必须真实反映工作区 —— 不能永远写死 false。"""
    current = b08_baseline.git_state()
    assert baseline["git"]["dirty"] == current["dirty"], (
        "基线里的 dirty 与当前工作区对不上；"
        "提交/改动后请重新跑 python -m B07.b08_baseline")


def test_model_fingerprints_match_real_files(baseline):
    import hashlib

    manifest = PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1.json"
    weights = PROJECT_ROOT / "B06" / "artifacts" / "b06-20261020-v1.joblib"
    assert baseline["model"]["artifact_manifest_sha256"] == \
        hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert baseline["model"]["model_weights_sha256"] == \
        hashlib.sha256(weights.read_bytes()).hexdigest()


def test_knowledge_combined_sha_matches_rule(baseline):
    import hashlib

    combined = hashlib.sha256()
    for s in sorted(baseline["knowledge"]["sources"], key=lambda x: x["path"]):
        combined.update(s["path"].encode("utf-8"))
        combined.update(b"\n")
        combined.update(s["sha256"].encode("utf-8"))
        combined.update(b"\n")
    assert baseline["knowledge"]["combined_sha256"] == combined.hexdigest()


def test_prompt_fingerprints_are_the_frozen_ones(baseline):
    """★ SYSTEM_PROMPT / FIXED_QUESTION 绝对不许改。"""
    assert baseline["prompt"]["system_prompt_sha256"] == "aa1400cf8568"
    assert baseline["prompt"]["question_sha256"] == "d3cc394bfcbe"
    assert baseline["prompt"]["system_prompt_sha256"] == _sha12(SYSTEM_PROMPT)
    assert baseline["prompt"]["question_sha256"] == _sha12(FIXED_QUESTION)
    assert baseline["prompt"]["question"] == FIXED_QUESTION


def test_baseline_covers_five_scenarios(baseline):
    ids = [s["scenario_id"] for s in baseline["scenarios"]]
    assert ids == SCENARIO_IDS


# --------------------------------------------------------------------------
# F2 场景冻结
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sid", SCENARIO_IDS)
def test_scenario_file_has_required_fields(sid):
    obj = _load(sid)
    for key in ("scenario_id", "name", "app_version", "model_version",
                "analysis_time", "location", "question",
                "system_prompt_sha256", "question_sha256",
                "current_state", "history_window", "history",
                "source_run_id", "source_run_sha256", "scenario_sha256"):
        assert key in obj, f"{sid}.json 缺字段 {key}"
    assert obj["scenario_id"] == sid
    assert obj["app_version"] == "B08_baseline_01"
    assert obj["question"] == FIXED_QUESTION
    assert obj["question_sha256"] == "d3cc394bfcbe"
    assert obj["system_prompt_sha256"] == "aa1400cf8568"


@pytest.mark.parametrize("sid", SCENARIO_IDS)
def test_scenario_digest_matches(sid):
    """场景文件有没有被手改过 —— 重算指纹对不上就是被改过。"""
    obj = _load(sid)
    assert obj["scenario_sha256"] == scenarios_build.scenario_digest(obj)


@pytest.mark.parametrize("sid", SCENARIO_IDS)
def test_scenario_history_is_the_frozen_window(sid):
    obj = _load(sid)
    assert obj["history_window"]["count"] == len(obj["history"]) == 6


@pytest.mark.parametrize("sid", SCENARIO_IDS)
def test_scenario_source_run_still_exists_and_matches(sid):
    obj = _load(sid)
    p = scenarios_build.run_path(obj["source_run_id"])
    assert p.exists(), f"来源存档不见了：{p}"
    import hashlib
    assert obj["source_run_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()


def test_s5_keeps_weather_unavailable_and_error_text():
    """S5 是退化场景 —— unavailable 状态与 error 原文必须留着。"""
    obj = _load("S5")
    w = obj["weather_evidence_snapshot"]
    assert w is not None
    assert w["available"] is False
    assert w["status"] != "ok"
    assert w["error"], "S5 必须保留 B03 的 error 原文"


def test_s1_s2_have_no_weather_snapshot():
    for sid in ("S1", "S2"):
        obj = _load(sid)
        assert obj["weather_evidence_snapshot"] is None, \
            f"{sid} 按设计不接入外部天气，不该有天气快照"


def test_weather_snapshots_keep_provider_and_forecast_time():
    """有快照的场景必须保留 provider / forecast_time（原件不得丢）。"""
    for sid in ("S3", "S4", "S5"):
        w = _load(sid)["weather_evidence_snapshot"]
        assert w["provider"], f"{sid} 的快照丢了 provider"
        assert "forecast_time" in w, f"{sid} 的快照丢了 forecast_time"
        assert "retrieved_at" in w, f"{sid} 的快照丢了 retrieved_at 字段"
        assert w["snapshot_taken_from_run_id"] == _load(sid)["source_run_id"]


def test_verify_mode_passes():
    ok, rows = scenarios_build.verify()
    assert ok, "场景文件与存档不一致：" + "; ".join(
        f"{r['scenario_id']}: {r['reason']}" for r in rows if not r["ok"])


# --------------------------------------------------------------------------
# F3 存档契约不被污染
# --------------------------------------------------------------------------

def test_run_json_has_no_app_version_field():
    """★ app_version 不许进 run.json（存档结构一字不改）。"""
    sample = sorted((PROJECT_ROOT / "B07" / "runs").glob("run_*/run.json"))[:5]
    assert sample, "没有找到任何 run 存档"
    for p in sample:
        raw = p.read_text(encoding="utf-8")
        assert '"app_version"' not in raw, f"{p.name} 里出现了 app_version"
        j = json.loads(raw)
        assert "app_version" not in j


def test_scenario_files_live_outside_runs_dir():
    """固定场景是独立产物，不许塞进 run 存档目录。"""
    for sid in SCENARIO_IDS:
        assert (SCENARIOS_DIR / f"{sid}.json").exists()
        assert (PROJECT_ROOT / "B07" / "runs" / sid).exists() is False


# --------------------------------------------------------------------------
# F4 健康检查分层
# --------------------------------------------------------------------------

def test_shallow_health_is_ok_and_touches_nothing():
    res = health.shallow_health()
    assert res["status"] == "ok"
    assert res["scope"] == "shallow"
    assert res["external_services_touched"] is False
    assert res["app_version"]
    assert res["model_version"]


def test_deep_health_reports_four_dependencies():
    res = health.deep_health(probe=False)
    assert set(res["checks"]) == {"B02_database", "B03_weather",
                                  "B04_knowledge", "B06_model"}
    for name, item in res["checks"].items():
        assert item["status"] in ("ok", "unavailable", "error"), \
            f"{name} 的状态非法：{item['status']}"
        assert item["detail"], f"{name} 必须给出原因"


def test_deep_health_does_not_call_weather_by_default():
    res = health.deep_health(probe=False)
    assert res["probe"] is False
    assert res["checks"]["B03_weather"]["network_requested"] is False
    assert res["checks"]["B03_weather"]["endpoint"]


def test_health_endpoints_registered():
    from B07 import views_web

    paths = {r.path for r in views_web.app.routes}
    assert "/health" in paths
    assert "/health/deep" in paths


def test_b01_health_endpoints_registered():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "b01_main_under_test", PROJECT_ROOT / "B01" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    paths = {r.path for r in mod.app.routes}
    assert "/health" in paths
    assert "/health/deep" in paths
