"""B07 创新点 3 的不变量测试（离线，不联网、不调 LLM）

覆盖的东西：
  1. **词表不变量**：所有检测词只用多字短语（长度 ≥ 2），禁用单字
     —— S3 已经踩过"风"命中"风险"的坑，这里把它钉死
  2. **C2 检测器**：确定性词表能命中真话、且长词优先不重复计数；
     正常句不误报
  3. **C4 检测器**：外部天气内容会命中；含免责词的合规声明整句豁免；
     豁免句里若含数值仍被单独标出（不隐藏）
  4. **存档不变量**（用真实 run_082 / run_084）：分歧必须被保留成
     conflict_unresolved；证据缺失必须是 unavailable
  5. **检查项**：C1 / C2 / C4 在真实存档上必须 PASS
     （C3 需要本轮新跑的 run_agree，存档不在时跳过）
  6. **确定性**：同样的输入渲染两次必须逐字节一致

跑法：`python -m pytest B07/tests -q`
"""

from __future__ import annotations

import pytest

from B07.innovation3 import (
    CERTAINTY_PHRASES,
    EXEMPT_PHRASES,
    EXTERNAL_TERM_PHRASES,
    EXTERNAL_VALUE_PATTERNS,
    LIMITATION_PHRASES,
    OPPOSING_PATTERNS,
    SCOPE_LIMIT_PHRASES,
    SUPPORT_PATTERNS,
    _ascii_table,
    _dwidth,
    build_checks,
    build_sections,
    extract_run,
    render_md,
    scan_certainty,
    scan_external_content,
)
from B07.runs import load_run


# --------------------------------------------------------------------------
# 1. 词表不变量
# --------------------------------------------------------------------------
def test_all_detection_phrases_are_multi_char():
    """禁用单字：所有词表条目长度必须 ≥ 2。"""
    for name, phrases in (("CERTAINTY", CERTAINTY_PHRASES),
                          ("LIMITATION", LIMITATION_PHRASES),
                          ("SCOPE_LIMIT", SCOPE_LIMIT_PHRASES),
                          ("EXEMPT", EXEMPT_PHRASES),
                          ("EXTERNAL_TERM", EXTERNAL_TERM_PHRASES)):
        for ph in phrases:
            assert len(ph) >= 2, f"{name} 词表出现单字条目：{ph!r}"


def test_pattern_labels_are_multi_char():
    for label, _pat in SUPPORT_PATTERNS:
        assert len(label) >= 2, f"SUPPORT label 是单字：{label!r}"
    for label, _pat in OPPOSING_PATTERNS:
        assert len(label) >= 2, f"OPPOSING label 是单字：{label!r}"


def test_external_value_patterns_are_present():
    """外部天气数值必须六项都在（红线 C4）。"""
    labels = [label for label, _ in EXTERNAL_VALUE_PATTERNS]
    for needed in ("降水概率 50.0%", "降水概率 10.0%", "Δ=-40.0",
                   "阈值 ±30.0", "温度 Δ=-1.0", "云量 Δ=-70.0"):
        assert needed in labels


# --------------------------------------------------------------------------
# 2. C2 检测器
# --------------------------------------------------------------------------
def test_certainty_detector_hits_real_certainty():
    hits = scan_certainty("未来一定会持续恶化。")
    assert len(hits) == 1
    assert hits[0]["phrase"] == "一定会"


def test_certainty_detector_longest_match_only_once():
    """『一定会』与『一定』都在词表里，但同一处只能算一次。"""
    hits = scan_certainty("这个结论一定会成立。")
    assert len(hits) == 1


def test_certainty_detector_no_false_positive_on_normal_text():
    text = ("根据当前的监测数据，温度为28.6℃，湿度为66.9%。"
            "系统建议提高关注程度至WATCH级别，置信度为1.0。"
            "外部天气条件显示未来降水概率降低，但仅供方向性参考。")
    assert scan_certainty(text) == []


def test_certainty_detector_ignores_single_char_context():
    """『不确定』里没有多字确定性词 —— 不能误报。"""
    assert scan_certainty("模型对趋势并不确定。") == []


# --------------------------------------------------------------------------
# 3. C4 检测器
# --------------------------------------------------------------------------
def test_external_content_is_detected():
    rows = scan_external_content("未来降水概率为 50.0%，建议提高关注。")
    violations = [r for r in rows if r["violation"]]
    assert len(violations) == 1
    assert "降水概率" in violations[0]["terms"]


def test_exempt_sentence_is_not_a_violation():
    """免责句 = 合规声明，不算伪造。"""
    text = "另外，外部天气证据当前不可用，与外部天气影响相关的判断受到限制。"
    rows = scan_external_content(text)
    assert rows[0]["terms"], "应当命中『外部天气』表述"
    assert rows[0]["exempt"] is True
    assert rows[0]["violation"] is False


def test_exempt_sentence_with_value_is_still_flagged():
    """豁免句里若含外部天气数值，仍要单独标出来（不隐藏）。"""
    text = "外部天气证据当前不可用（历史值降水概率 50.0%）。"
    rows = scan_external_content(text)
    assert rows[0]["exempt"] is True
    assert rows[0]["violation"] is False
    assert rows[0]["exempt_but_has_value"] is True
    assert rows[0]["values"]


# --------------------------------------------------------------------------
# 4. 存档不变量（真实 run）
# --------------------------------------------------------------------------
def test_conflict_run_keeps_divergence():
    run = extract_run(load_run("run_082"))
    assert run["combined_attention"]["value"] == "conflict_unresolved"
    assert run["divergence"]["has_conflict"] is True
    assert run["divergence"]["supporting"], "支持侧不能为空"
    assert run["divergence"]["opposing"], "反向侧不能为空"
    assert run["expression"]["support_hits"], "回答里应有『支持提高关注』"
    assert run["expression"]["opposing_hits"], "回答里应有『提示可能缓解/反向』"


def test_missing_weather_run_marks_external_unavailable():
    run = extract_run(load_run("run_084"))
    assert run["external"]["signal"] == "external_unavailable"
    b03 = [e for e in run["entries"] if e["source_type"] == "B03"]
    assert b03 and b03[0]["status"] == "unavailable"
    assert run["expression"]["limitation_hits"], "回答里应说明受限"


def test_real_runs_pass_c1_c2_c4():
    """用真实存档跑检查项（agree 用 conflict 的存档占位，只断言不依赖 agree 的项）。"""
    conflict = extract_run(load_run("run_082"))
    missing = extract_run(load_run("run_084"))
    runs = {
        "run_agree": conflict,          # 占位：C3 不参与断言
        "run_conflict": conflict,
        "run_missing_weather": missing,
    }
    checks = build_checks(runs)
    assert checks["C1"]["passed"], checks["C1"]["items"]
    assert checks["C2"]["passed"], checks["C2"]["items"]
    assert checks["C4"]["passed"], checks["C4"]["items"]


def test_full_checks_pass_when_agree_archive_exists():
    """本轮新跑的 run_agree 若已落盘，C1–C5 必须全 PASS。"""
    try:
        agree = extract_run(load_run("run_106"))
    except Exception:               # pragma: no cover - 存档不在时跳过
        pytest.skip("run_agree 存档不在，跳过")
    runs = {
        "run_agree": agree,
        "run_conflict": extract_run(load_run("run_082")),
        "run_missing_weather": extract_run(load_run("run_084")),
    }
    checks = build_checks(runs)
    for key in ("C1", "C2", "C3", "C4", "C5"):
        assert checks[key]["passed"], (key, checks[key]["items"])


# --------------------------------------------------------------------------
# 5. 渲染与确定性
# --------------------------------------------------------------------------
def test_ascii_table_rows_have_equal_width():
    headers = ["维度", "甲", "乙"]
    rows = [["判断范围", "内部历史+模型+外部（方向相反，保留分歧）", "否"],
            ["确定性断言数", "0", "0"]]
    table = _ascii_table(headers, rows)
    widths = {_dwidth(line) for line in table.splitlines()}
    assert len(widths) == 1, f"表格各行显示宽度不一致：{widths}"


def test_report_rendering_is_deterministic():
    runs = {
        "run_agree": extract_run(load_run("run_084")),
        "run_conflict": extract_run(load_run("run_082")),
        "run_missing_weather": extract_run(load_run("run_084")),
    }
    checks = build_checks(runs)
    sections = build_sections(runs, checks)
    data = {"runs": runs, "checks": checks}
    assert render_md(sections, data) == render_md(sections, data)


def test_sections_cover_the_six_steps_and_the_checks():
    runs = {
        "run_agree": extract_run(load_run("run_084")),
        "run_conflict": extract_run(load_run("run_082")),
        "run_missing_weather": extract_run(load_run("run_084")),
    }
    checks = build_checks(runs)
    sections = build_sections(runs, checks)
    titles = " ".join(s["title"] for s in sections)
    for needed in ("第一步", "第二步", "第三步", "第四步", "第五步", "第六步",
                   "检查汇总", "结论", "Limitations", "复现命令"):
        assert needed in titles, f"报告缺少章节：{needed}"
    assert len(sections) == 12
    assert set(checks) == {"C1", "C2", "C3", "C4", "C5"}
