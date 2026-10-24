"""B07 S1 不变量测试

覆盖 S1-1 ~ S1-5 的关键不变量：
  1. 历史点数 == 6 且相邻间隔 == 30 分钟
  2. 历史最后一点 timestamp == analysis_time
  3. structured_judgement.predicted_attention == model_output.predicted_attention（没被改）
  4. recompute_judgement 的五字段与 build_judgement 逐字段相等
  5. 通过 run_id 能找回：当前状态 / 历史窗口 / 预测 / 判断 / 解释 五项
  6. explain_run 不依赖内存状态（新进程重读文件能跑通）
  7. 构造的实时窗口 target 为空（没有偷看未来）
  8. LLM 失败时以显式失败结束，并留下带失败原因的 run 记录

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests -v
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANCHOR = "2026-10-21T16:00:00"
LOCATION = "A区-3号温室"


# --------------------------------------------------------------------------
# 公共构件
# --------------------------------------------------------------------------

def _evidence():
    from B07.evidence import build_evidence
    return build_evidence(datetime.fromisoformat(ANCHOR), LOCATION)


def _bundle():
    """evidence + model_output（不涉及 LLM）。"""
    from B07.predict import predict_for_run
    return predict_for_run(_evidence())


# --------------------------------------------------------------------------
# 1. 历史点数 == 6 且相邻间隔 == 30 分钟
# --------------------------------------------------------------------------

def test_history_point_count_is_six():
    from B07.config import HISTORY_POINTS

    ev = _evidence()
    assert HISTORY_POINTS == 6
    assert len(ev["history"]) == 6
    assert ev["history_window"]["count"] == 6


def test_history_interval_is_30_minutes():
    ev = _evidence()
    ts = [datetime.fromisoformat(h["timestamp"]) for h in ev["history"]]
    for i in range(len(ts) - 1):
        gap = (ts[i + 1] - ts[i]).total_seconds() / 60.0
        assert gap == 30.0, f"第 {i+1}→{i+2} 点间隔 {gap} 分钟，不是 30"


def test_history_point_count_violation_raises(tmp_path):
    """点数不对时必须报错，不许静默凑数。"""
    from B07.evidence import EvidenceValidationError, build_evidence

    with pytest.raises(EvidenceValidationError):
        build_evidence(datetime.fromisoformat("2026-10-21T16:10:00"), LOCATION)


# --------------------------------------------------------------------------
# 2. 历史最后一点 timestamp == analysis_time
# --------------------------------------------------------------------------

def test_history_last_timestamp_equals_analysis_time():
    ev = _evidence()
    assert ev["history"][-1]["timestamp"] == ev["analysis_time"]
    assert ev["analysis_time"] == ANCHOR


def test_current_state_timestamp_equals_analysis_time():
    ev = _evidence()
    assert ev["current_state"]["timestamp"] == ev["analysis_time"]


# --------------------------------------------------------------------------
# 3. predicted_attention 原样照抄，没被加工
# --------------------------------------------------------------------------

def test_predicted_attention_is_copied_verbatim():
    from B07.judgement import build_judgement

    bundle = _bundle()
    judgement = build_judgement(bundle)
    assert judgement["predicted_attention"] == bundle["model_output"]["predicted_attention"]
    assert judgement["predicted_attention"] in ("NORMAL", "WATCH")


def test_judgement_has_exactly_five_fields():
    from B07.judgement import build_judgement

    judgement = build_judgement(_bundle())
    assert list(judgement.keys()) == [
        "current_environment", "recent_trend", "predicted_attention",
        "evidence_status", "limitations",
    ]
    assert len(judgement) == 5


# --------------------------------------------------------------------------
# 4. recompute_judgement 与 build_judgement 逐字段相等
# --------------------------------------------------------------------------

def test_recompute_judgement_matches_build_judgement():
    from B07.judgement import build_judgement, recompute_judgement, self_check

    bundle = _bundle()
    a = build_judgement(bundle)
    b = recompute_judgement(bundle)

    assert list(a.keys()) == list(b.keys())
    for field in a:
        assert a[field] == b[field], f"字段 {field} 复算不相等"

    check = self_check(bundle)
    assert check["identical"] is True
    assert check["differing_fields"] == []
    assert check["field_count"] == 5
    assert check["same_order"] is True


def test_judgement_is_independent_of_llm_text():
    """判断不依赖 LLM 文本：把 llm_explanation 换成完全不同的内容，五字段不变。"""
    from B07.judgement import build_judgement

    bundle = _bundle()
    before = build_judgement(bundle)
    bundle["llm_explanation"] = "完全不同的解释文本：需要立即处理，风速 99 m/s。"
    after = build_judgement(bundle)
    assert before == after


# --------------------------------------------------------------------------
# 5. 通过 run_id 能找回五项内容
# --------------------------------------------------------------------------

def test_run_id_recovers_all_five_items(fake_llm, tmp_runs_dir):
    from B07.runs import load_run, run_analysis

    archive = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    run_id = archive["run_id"]
    assert run_id.startswith("run_")

    saved = load_run(run_id)
    assert saved["run_id"] == run_id

    # 五项都能找回来
    assert saved["current_state"]["temperature"] is not None       # 当前状态
    assert saved["history_window"]["count"] == 6                  # 历史窗口
    assert saved["model_output"]["predicted_attention"] in ("NORMAL", "WATCH")  # 预测
    assert len(saved["structured_judgement"]) == 5                # 判断
    assert saved["llm_explanation"]                               # 解释
    assert saved["llm_explanation"].strip()


def test_index_records_every_run(fake_llm, tmp_runs_dir):
    from B07.runs import list_runs, run_analysis

    run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    items = list_runs()
    assert len(items) == 1
    assert items[0]["status"] == "success"
    assert items[0]["verdict"] in ("NORMAL", "WATCH")
    assert items[0]["model_version"]
    assert items[0]["question"]


def test_repeated_run_gets_new_id_same_result_keeps_id(fake_llm, tmp_runs_dir):
    """重新执行一次分析产生新 run_id；重复查看不换 id。"""
    from B07.runs import explain_run, run_analysis

    first = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    second = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    assert first["run_id"] != second["run_id"]

    a = explain_run(first["run_id"], quiet=True)
    b = explain_run(first["run_id"], quiet=True)
    assert a["run_id"] == b["run_id"] == first["run_id"]
    assert a["answer"] == b["answer"]


# --------------------------------------------------------------------------
# 6. explain_run 不依赖内存状态
# --------------------------------------------------------------------------

def test_explain_run_reads_from_disk_only(fake_llm, tmp_runs_dir):
    """同进程内：删掉内存里的对象（重新 load）后仍能回答。"""
    from B07.runs import explain_run, run_analysis

    archive = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    run_id = archive["run_id"]
    del archive

    result = explain_run(run_id, quiet=True)
    assert result["run_id"] == run_id
    assert "稳定" in result["answer"] or "稳定" in result["answer"]
    assert result["all_match"] is True


def test_explain_run_works_in_a_fresh_process(fake_llm, tmp_runs_dir):
    """新进程重读文件能跑通 —— 关掉页面再回来。"""
    from B07.runs import run_analysis

    archive = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    run_id = archive["run_id"]

    script = (
        "import sys, json;"
        f"sys.path.insert(0, r'{PROJECT_ROOT}');"
        f"sys.path.insert(0, r'{PROJECT_ROOT / 'B02'}');"
        "import B07.runs as runs;"
        f"runs.RUNS_DIR = __import__('pathlib').Path(r'{tmp_runs_dir}');"
        f"runs.INDEX_PATH = runs.RUNS_DIR / 'index.json';"
        f"res = runs.explain_run({run_id!r}, quiet=True);"
        "print(json.dumps({'run_id': res['run_id'], 'all_match': res['all_match'],"
        " 'answer_len': len(res['answer'])}, ensure_ascii=False))"
    )
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    assert proc.returncode == 0, f"新进程执行失败：{proc.stderr}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["run_id"] == run_id
    assert payload["all_match"] is True
    assert payload["answer_len"] > 200


def test_explain_run_facts_are_recomputed_not_replayed(fake_llm, tmp_runs_dir):
    """复算内容来自存档里的原始 6 点，而不是复读当初的文本。"""
    from B07.runs import explain_run, run_analysis

    archive = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    run_id = archive["run_id"]

    result = explain_run(run_id, quiet=True)
    names = {c["name"] for c in result["checks"]}
    assert "稳定判定结论" in names
    assert "历史点数" in names
    assert "hours_gap" in names

    stability_check = next(c for c in result["checks"] if c["name"] == "稳定判定结论")
    assert stability_check["match"] is True
    # 复算值确实是重新判出来的（含"判定为稳定/不稳定"字样），不是复读
    assert "判定为" in str(stability_check["recomputed"])


# --------------------------------------------------------------------------
# 7. 实时窗口 target 为空（没有偷看未来）
# --------------------------------------------------------------------------

def test_live_window_target_is_empty(monkeypatch):
    """构造给 B06 推理的实时窗口，其 target 必须是空列表。"""
    import B06.inference.inference as inference

    captured = {}
    original = inference.predict_window

    def _spy(window, model, meta, **kwargs):
        captured["window"] = window
        return original(window, model, meta, **kwargs)

    monkeypatch.setattr(inference, "predict_window", _spy)

    bundle = _bundle()
    assert "window" in captured, "predict_window 没被调用"

    window = captured["window"]
    assert window.target == [], f"实时窗口的 target 必须为空，实际 {window.target}"
    assert len(window.history) == 6
    # 窗口最后一点 == 当前时刻，不含任何未来点
    assert window.history[-1].timestamp.isoformat() == ANCHOR
    assert all(s.timestamp.isoformat() <= ANCHOR for s in window.history)


def test_model_output_target_is_not_filled_from_db(monkeypatch):
    """B06 推理返回里不应出现"目标点"这类从库里抄来的未来值。"""
    bundle = _bundle()
    mo = bundle["model_output"]
    assert "target" not in mo
    assert mo.get("errors") == []


# --------------------------------------------------------------------------
# 8. LLM 失败：显式失败 + 留下带失败原因的 run 记录
# --------------------------------------------------------------------------

def test_llm_failure_raises_explicitly(broken_llm, tmp_runs_dir):
    from B07.explain import ExplainError
    from B07.runs import run_analysis

    with pytest.raises(ExplainError):
        run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)


def test_llm_failure_leaves_failed_run_record(broken_llm, tmp_runs_dir):
    from B07.runs import list_runs, load_run, run_analysis

    with pytest.raises(Exception):
        run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)

    items = list_runs()
    assert len(items) == 1
    assert items[0]["status"] == "failed"
    assert items[0]["failed_stage"] == "llm_explanation"

    saved = load_run(items[0]["run_id"])
    assert saved["status"] == "failed"
    assert saved["failed_stage"] == "llm_explanation"
    assert "模拟 LLM 服务不可用" in saved["failure_reason"]
    # 失败之前的阶段照样留痕
    assert saved["current_state"]["temperature"] is not None
    assert saved["model_output"]["predicted_attention"] in ("NORMAL", "WATCH")


def test_failed_run_is_explainable(broken_llm, tmp_runs_dir):
    """失败运行同样能回溯，说明卡在哪一步是可查的。"""
    from B07.runs import explain_run, list_runs, run_analysis

    with pytest.raises(Exception):
        run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)

    run_id = list_runs()[0]["run_id"]
    result = explain_run(run_id, quiet=True)
    assert result["status"] == "failed"
    assert "llm_explanation" in result["answer"]


def test_failure_prints_one_line_to_stderr(broken_llm, tmp_runs_dir, capsys):
    """失败必须往 stderr 留一行（stage + 异常类型 + 消息），不许静默。"""
    from B07.runs import run_analysis

    with pytest.raises(Exception):
        run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)

    err = capsys.readouterr().err
    assert "llm_explanation" in err
    assert "RuntimeError" in err or "Error" in err
    assert "模拟 LLM 服务不可用" in err


def test_progress_callback_error_is_reported_not_swallowed(fake_llm, tmp_runs_dir, capsys):
    """进度回调出错不能影响主流程，但必须打到 stderr，不许 `except: pass`。"""
    from B07.runs import run_analysis

    def _boom(stage, extra=None):
        raise NameError("未定义的变量")

    archive = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION, progress=_boom)

    # 主流程照常完成
    assert archive["status"] == "success"

    # 但异常留了痕
    err = capsys.readouterr().err
    assert "NameError" in err
    assert "未定义的变量" in err
    assert "evidence" in err   # 记了是哪个阶段出的问题


# --------------------------------------------------------------------------
# 附：固定问题与敏感信息
# --------------------------------------------------------------------------

def test_fixed_question_is_not_mutated():
    from B07.explain import FIXED_QUESTION

    assert FIXED_QUESTION == ("分析当前番茄生长环境状态以及接下来值得关注的变化，"
                              "并说明判断依据。")


def test_archive_contains_no_secrets(fake_llm, tmp_runs_dir):
    from B07.runs import run_analysis

    archive = run_analysis(datetime.fromisoformat(ANCHOR), LOCATION)
    raw = json.dumps(archive, ensure_ascii=False)
    assert "sk-" not in raw
    assert "api_key" not in raw.lower()
    for banned in ("LLM_API_KEY", "Authorization", "bearer "):
        assert banned not in raw
