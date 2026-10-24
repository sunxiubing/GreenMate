"""B07 S2（渐进漂移）不变量测试

锁住 S2 的硬约束：
  - 问题与 S1 一字不差
  - 选窗只看 temperature / humidity / lux 三列，不看 sim_scenario
  - 选中的窗口必须被判为不稳定
  - 失败结论必须写清"不稳定"、超了哪个阈值、超了多少
  - 提示词指纹可验证且与 S1 一致
  - S1 的判据阈值与默认结论文本不受影响
  - 存档里的 scenario 字段正确（S1="S1" / S2="S2"）
  - 测试**不往 B07/runs/ 真实目录写文件**（用 tmp_runs_dir 隔离）
"""

import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from B07 import stability
from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT
from B07.scenarios import SCENARIOS, check_expectation, project_rows, select_window

S1_ANCHOR = "2026-10-21T16:00:00"
S2_ANCHOR = "2026-10-20T12:00:00"
REAL_RUNS_DIR = Path(__file__).resolve().parents[2] / "B07" / "runs"


@pytest.fixture(scope="module")
def samples():
    from B07.stability import load_all_samples
    return load_all_samples()


@pytest.fixture(scope="module")
def s2(samples):
    return select_window("S2", samples=samples)


# --------------------------------------------------------------------------
# 一、场景注册表
# --------------------------------------------------------------------------

def test_s2_registry_fields():
    s2_def = SCENARIOS["S2"]
    assert s2_def["key"] == "S2"
    assert s2_def["title"] == "渐进漂移"
    assert s2_def["anchor_policy"] == "drifting_comparable_to_S1"
    assert s2_def["expect_stability"] is False


def test_s2_question_is_identical_to_s1():
    """一字不改：两个场景引用的是同一个常量对象。"""
    assert SCENARIOS["S2"]["question"] == SCENARIOS["S1"]["question"]
    assert SCENARIOS["S2"]["question"] == FIXED_QUESTION


def test_s1_expectation_unchanged():
    assert SCENARIOS["S1"]["expect_stability"] is True


# --------------------------------------------------------------------------
# 二、选窗只看三列数值
# --------------------------------------------------------------------------

def test_selection_does_not_see_sim_scenario(samples, s2):
    """反事实：把 sim_scenario 全改乱码，选窗结果必须一模一样。"""
    polluted = [replace(s, sim_scenario="XXXXXXXX") for s in samples]
    again = stability.pick_drifting_window(polluted)
    assert again["selected"]["start_time"] == s2["selected"]["start_time"]
    assert again["selected"]["end_time"] == s2["selected"]["end_time"]
    assert again["selected"]["temp_range"] == s2["selected"]["temp_range"]


def test_projection_has_no_sim_scenario_field(samples):
    """投影后的行里根本没有 sim_scenario 字段，想用也用不上。"""
    rows = project_rows(samples)
    assert not hasattr(rows[0], "sim_scenario")
    assert rows[0]._fields == ("timestamp", "temperature", "humidity", "lux")


def test_pick_drifting_window_lives_in_stability():
    """选窗策略的实现放在 B07/stability.py。"""
    assert callable(stability.pick_drifting_window)


# --------------------------------------------------------------------------
# 三、选中的窗口
# --------------------------------------------------------------------------

def test_selected_window_matches_acceptance(s2):
    sel = s2["selected"]
    assert sel["start_time"] == "2026-10-20T09:30:00"
    assert sel["end_time"] == "2026-10-20T12:00:00"
    assert sel["delta_t"] == 7.5
    assert sel["end_temperature"] == 32.8


def test_selected_window_is_monotone_rising(s2):
    temps = [p["temperature"] for p in s2["selected"]["points"]]
    assert len(temps) == 6
    assert all(temps[i + 1] > temps[i] for i in range(len(temps) - 1))


def test_selected_window_is_judged_unstable(s2):
    assert s2["selected"]["passed"] is False
    check_expectation("S2", {"passed": s2["selected"]["passed"]})


def test_end_temperature_comparable_to_s1(samples, s2):
    """末点温度与 S1 末点可比（相差 ≤ 0.8℃）。"""
    from B07.scenarios import s1_reference
    ref = s1_reference(project_rows(samples))
    s1_t = ref["end_temperature"]
    s2_t = s2["selected"]["end_temperature"]
    assert abs(s2_t - s1_t) <= 0.8
    assert s2_t >= s1_t


def test_anchor_is_window_end(s2):
    assert s2["anchor_time"].isoformat() == s2["selected"]["end_time"]


# --------------------------------------------------------------------------
# 四、结论句
# --------------------------------------------------------------------------

def test_rich_conclusion_states_unstable_and_threshold(s2):
    concl = s2["selected"]["conclusion"]
    assert "不稳定" in concl
    assert "超过阈值" in concl
    assert "持续升温" in concl
    assert "7.5" in concl and "2.0" in concl
    assert "超出" in concl


def test_failed_criteria_carry_excess(s2):
    """超出的幅度要进判据表，LLM 复述时才不会被判成事实越界。"""
    failed = [c for c in s2["selected"]["criteria"] if not c["passed"]]
    assert failed
    for c in failed:
        if isinstance(c["threshold"], (int, float)):
            assert "excess" in c
            assert c["excess"] > 0


def test_default_conclusion_text_unchanged(samples):
    """S1 默认模式的结论文本不受 S2 影响（没有"超出"这类富化措辞）。"""
    from datetime import datetime, timedelta

    rows = project_rows(samples)
    from B07.scenarios import s1_reference
    ref = s1_reference(rows)
    end = datetime.fromisoformat(ref["end_time"])
    win = [r for r in rows if end - timedelta(hours=2.5) <= r.timestamp <= end]
    judged = stability.judge_stability(win)
    assert judged["passed"] is True
    assert "超出" not in judged["conclusion"]
    assert all("excess" not in c for c in judged["criteria"])


# --------------------------------------------------------------------------
# 五、提示词指纹
# --------------------------------------------------------------------------

def test_explain_returns_prompt_fingerprints(fake_llm):
    """llm_meta 里有 system_prompt_sha256 / question_sha256，可与 S1 直接比对。"""
    from B07.explain import _sha12, explain_judgement

    bundle = {
        "current_state": {"temperature": 32.8, "humidity": 56.7, "lux": 39842,
                          "source_type": "simulated", "sim_scenario": "normal",
                          "timestamp": "2026-10-21T16:00:00"},
        "history": [],
        "model_output": {"predicted_attention": "NORMAL", "confidence": 1.0},
    }
    out = explain_judgement(bundle)
    assert out["system_prompt_sha256"] == _sha12(SYSTEM_PROMPT)
    assert out["question_sha256"] == _sha12(FIXED_QUESTION)
    assert len(out["system_prompt_sha256"]) == 12


def test_scenario_label_handles_legacy_archives():
    """旧存档没有 scenario 字段时显示 '(旧存档未记录)'，不报错。"""
    from B07.runs import scenario_label
    assert scenario_label({"scenario": "S2"}) == "S2"
    assert scenario_label({}) == "(旧存档未记录)"
    assert scenario_label({"scenario": None}) == "(旧存档未记录)"


# --------------------------------------------------------------------------
# 六、阈值没被动过
# --------------------------------------------------------------------------

def test_thresholds_unchanged():
    assert stability.TEMP_RANGE_MAX == 2.0
    assert stability.HUMI_TOTAL_MAX == 3.0
    assert stability.HUMI_STEP_MAX == 1.5
    assert stability.LUX_JUMP_MAX == 15000
    assert stability.REQUIRE_NO_MONOTONE is True
    assert stability.DAYTIME_LUX_MIN == 1000.0


# --------------------------------------------------------------------------
# 七、S1 / S2 两套流程跑出来的存档必须可对照
# --------------------------------------------------------------------------

@pytest.fixture
def two_runs(fake_llm, tmp_runs_dir):
    """用假 LLM 各跑一次 S1 / S2，拿到两份可对照的存档。"""
    from B07.runs import run_analysis

    s1 = run_analysis(datetime.fromisoformat(S1_ANCHOR), "A区-3号温室", scenario="S1")
    s2 = run_analysis(datetime.fromisoformat(S2_ANCHOR), "A区-3号温室", scenario="S2")
    return s1, s2


def test_s1_and_s2_questions_are_identical(two_runs):
    """两份存档里实际提给 LLM 的问题必须逐字相同。"""
    s1, s2 = two_runs
    assert s1["question"] == s2["question"] == FIXED_QUESTION
    assert s1["llm_meta"]["question"] == s2["llm_meta"]["question"]


def test_s1_and_s2_system_prompt_fingerprints_are_identical(two_runs):
    """两次的 system_prompt_sha256 必须完全相同（提示词没被改过）。"""
    s1, s2 = two_runs
    fp1 = s1["llm_meta"]["system_prompt_sha256"]
    fp2 = s2["llm_meta"]["system_prompt_sha256"]
    assert fp1 and fp2
    assert fp1 == fp2
    # 也等于现场实算的值
    from B07.explain import _sha12
    assert fp1 == _sha12(SYSTEM_PROMPT)


def test_s1_and_s2_question_fingerprints_are_identical(two_runs):
    s1, s2 = two_runs
    assert s1["llm_meta"]["question_sha256"] == s2["llm_meta"]["question_sha256"]


def test_s1_stability_passed_true_s2_passed_false(two_runs):
    """S1 判稳定、S2 判不稳定——两套流程共用同一套阈值。"""
    s1, s2 = two_runs
    assert s1["history_window"]["stability"]["passed"] is True
    assert s2["history_window"]["stability"]["passed"] is False


def test_archives_record_their_scenario(two_runs):
    """存档里的 scenario 字段要写对。"""
    s1, s2 = two_runs
    assert s1["scenario"] == "S1"
    assert s2["scenario"] == "S2"


def test_index_records_scenario_for_each_run(two_runs):
    from B07.runs import list_runs

    items = list_runs()
    by_scenario = {i["scenario"] for i in items}
    assert "S1" in by_scenario
    assert "S2" in by_scenario


def test_legacy_archives_without_scenario_are_tolerated():
    """旧存档取不到 scenario 时不报错（显示 '(旧存档未记录)'）。"""
    from B07.runs import scenario_label

    assert scenario_label({}) == "(旧存档未记录)"
    assert scenario_label({"scenario": None}) == "(旧存档未记录)"
    assert scenario_label({"scenario": "S1"}) == "S1"
    assert scenario_label({"scenario": "S2"}) == "S2"


def test_end_temperature_gap_between_s1_and_s2(samples, s2):
    """S1 与 S2 的末点温度差 ≤ 0.8℃（当前值可比）。"""
    from B07.scenarios import s1_reference

    t1 = s1_reference(project_rows(samples))["end_temperature"]
    t2 = s2["selected"]["end_temperature"]
    assert abs(t2 - t1) <= 0.8


# --------------------------------------------------------------------------
# 八、测试不许往 B07/runs/ 真实目录写文件
# --------------------------------------------------------------------------

def test_runs_are_written_to_tmp_dir_not_real_runs_dir(fake_llm, tmp_runs_dir):
    """跑完两次分析后，B07/runs/ 真实目录必须一个文件都没多。"""
    from B07.runs import run_analysis

    def _snapshot():
        if not REAL_RUNS_DIR.exists():
            return set()
        return {p.name for p in REAL_RUNS_DIR.iterdir()}

    before = _snapshot()
    run_analysis(datetime.fromisoformat(S1_ANCHOR), "A区-3号温室", scenario="S1")
    run_analysis(datetime.fromisoformat(S2_ANCHOR), "A区-3号温室", scenario="S2")
    after = _snapshot()

    assert before == after, f"真实存档目录被写入了：{after - before}"
    # 存档确实落在临时目录里
    assert (tmp_runs_dir / "index.json").exists()
