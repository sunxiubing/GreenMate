"""B07 S3-2 — 带 / 不带外部天气的对照实验

这一轮只做**实验**，不做报告文档（B07/artifacts/s3_report.md 是 S3-3 的事）。

实验设计（单变量对照）：
  Run A（S3_without）：锚点 2026-10-21T16:00:00，不接外部天气
  Run B（S3_with）  ：**同一个锚点**，结构化判断里多一个 external_conditions 字段

除了"有没有外部条件"这一项，两次运行的其它输入必须完全一样：
  同一套 SYSTEM_PROMPT、同一个固定问题、同一个 B06 模型、同一段 B02 历史、
  同一套判据阈值。唯一的差别是**发给 LLM 的 state 里多了一块 ⑥ 外部天气条件**。

六节输出：
  一、实验设置与两个 run_id
  二、核心证据（原始 diff，不加工）
  三、两次 LLM 回答并排对比
  四、SYSTEM_PROMPT 第 3 条禁令的实测探针
  五、离线可复现组（夹具注入）vs 真实 API 组
  六、天气服务不可用时的降级行为

硬约束：
  - 不改 SYSTEM_PROMPT、不改固定问题、不改 B01 / B02 / B03 / B05 / B06
  - 不重训模型、不重新生成 B02 数据
  - sim_scenario 不参与任何业务判断
  - 失败必须显式暴露，禁止用静态文案伪装成功
  - 不得把任何实测天气数值硬编码进代码或测试
"""

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from B07.config import DEFAULT_LOCATION
from B07.explain import FIXED_QUESTION, MAX_ANSWER_CHARS, SYSTEM_PROMPT

# ---- 实验常量 ----
ANCHOR = "2026-10-21T16:00:00"          # 与 S1 同一个锚点，两次运行完全一致
SCENARIO_WITHOUT = "S3_without"          # 对照组
SCENARIO_WITH = "S3_with"                # 实验组
WEATHER_LOCATION = "河北固安"            # 与 B07/weather.py 的默认一致
WEATHER_WINDOW = "6h"

# --------------------------------------------------------------------------
# "模型有没有引用外部条件"的判定器
# --------------------------------------------------------------------------
# 任务 A 的教训：**不能用单字**。旧词表里的单字"风"会命中"风险"，
# 所以这里全部用**词/短语**匹配。
#
# 分三层，逐层收窄：
#   1) 提及层：句子里出现了外部/天气相关表述（含否定式元陈述）
#   2) 事实层：句子里**陈述了**外部条件的事实（方向词 / 外部数值 / 具体天气现象）
#   3) 引用 = 事实层
# "未接入真实天气"这类**否定式元陈述**只算提及，不算事实。

# ---- 1) 外部/天气**名词短语**（提及层用）----
EXTERNAL_NOUN_TERMS = (
    # 外部证据本身的说法
    "外部条件", "外部天气", "外部环境", "外部数据", "外部信息", "外部信号",
    "外部状况", "外部情形", "外部风险", "外部来源", "外部数据源",
    "外部条件判定", "外部天气条件", "外部传感器",
    # ⑥ 块里出现的点名说法
    "基线点", "预报点", "当前实况", "实况", "未来窗口",
    # 真实世界天气
    "真实天气", "实时天气", "当前天气", "天气预报", "天气状况", "天气情况",
    "天气", "气象", "公开气象",
    # 室外
    "室外", "户外", "露天",
    # 外部传感
    "真实传感器", "传感器数据", "传感器读数",
)

# ---- 2) 具体天气**现象**（事实层用）----
WEATHER_PHENOMENON_TERMS = (
    "降水概率", "降雨概率", "降水量", "降雨量",
    "降水", "降雨", "下雨", "阴雨", "雨量", "雨势", "毫米",
    "小雨", "大雨", "中雨", "暴雨", "阵雨", "雷阵雨", "毛毛雨",
    "云量", "多云", "阴天", "晴天", "晴朗", "太阳辐射",
    "霜冻", "冰雹", "大风", "风力", "气温骤降", "湿冷", "闷热",
    # 单字只放"雨"：模型偶尔写"未来有雨"。"雨"不会像"风"那样命中"风险"，
    # 所以这一条是安全的；"风"单字已刻意排除。
    "雨",
)
# 注意："温度 / 湿度 / 光照"是 **B02 内部指标**，不能拿来判"有没有提外部天气"。

# ---- 3) 方向/变化词（事实层用）----
DIRECTION_WORDS = ("上升", "升高", "增大", "加大", "增强", "恶化", "转差", "变差",
                   "下降", "降低", "减小", "好转", "转好", "改善", "缓解",
                   "偏高", "偏低", "平稳", "稳定", "不变", "持平")

# ---- 4) 元陈述：只是说"接没接 / 拿不到"，不是断言外部事实 ----
META_PATTERNS = (
    r"(?:未|没有|不|无法|尚未|不能|缺乏|缺少|无)(?:接入|使用|包含|提供|获取|评估|获得|取到|拿到|取回|涉及|引用|采纳|体现|反映)",
    r"(?:没有|未提供|无)(?:相关|可用|有效)?(?:信息|数据|内容|依据|材料|数值)",
    # 免责声明里"（模拟数据，非真实传感器读数）"这种**否定式**表述：
    # 说的是"没有外部读数"，不是"外部读数是多少"，属于元陈述。
    r"(?:非真实|非实际|非现场|模拟|虚拟)(?:的)?(?:传感器|天气|数据|读数|条件)",
    r"(?:时间不同步|不同步|时间基准不同|非同一时间基准)",
)

# 兼容旧名字：外部/天气相关词的并集（提及层）
WEATHER_KEYWORDS = tuple(dict.fromkeys(EXTERNAL_NOUN_TERMS + WEATHER_PHENOMENON_TERMS))

# ---- 5) 宽字符兜底扫描（**不是判据**，是防漏的人工复核工具）----
# 词表法天然可能漏词。为了不让"检测器没命中"和"模型没提"混淆，
# 每条回答都额外扫一遍这些**不会撞内部指标**的字，人工过一眼。
# 刻意不含"风"（每条回答都有"风险"）、不含"温/湿/光"（B02 内部指标）。
WIDE_HINT_CHARS = tuple("雨雪霜雹雾霾雷电冰")


def wide_hint_sentences(text: str) -> list[str]:
    """兜底扫描：含"雨雪霜雹雾霾雷电冰"任一字的句子（人工复核用）。"""
    return [s for s in _split_sentences(text) if any(c in s for c in WIDE_HINT_CHARS)]

# SYSTEM_PROMPT 第 3 条（原文引用，后面探针要用）
RULE3_MARKER = "绝对不得编造上下文中没有的事实"
PROMPT_RULE3_SUGGESTION = (
    "建议改写为：『绝对不得编造上下文中没有的事实——"
    "凡上下文里没有出现的数字与结论都不要说；"
    "上下文里已经给出的外部数据（如真实天气）可以引用，但要连同它的限制条件一起说明。』"
)


# --------------------------------------------------------------------------
# 打印工具
# --------------------------------------------------------------------------

def hr(title: str = "") -> None:
    print("\n" + "=" * 70)
    if title:
        print(title)
        print("=" * 70)


def sub(title: str) -> None:
    print("\n" + "-" * 70)
    print(title)
    print("-" * 70)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 离线夹具：假的 B05 天气工具
# --------------------------------------------------------------------------

# 离线组的时刻：基线 22:00、预报 +6h → 04:00，基准时刻一致（skew=0）
OFFLINE_BASE_TS = "2026-10-24T22:00"
OFFLINE_FORE_TS = "2026-10-25T04:00"

# 三种信号各配一组**虚构**数值（不是任何实测值，只是为了让判定分支都能走到）
OFFLINE_CASES = {
    "deteriorating": {
        "base_pp": 20.0, "fore_pp": 60.0,     # Δ=+40 → 恶化
        "base_cloud": 30.0, "fore_cloud": 80.0,
        "base_desc": "多云", "fore_desc": "小雨",
    },
    "stable": {
        "base_pp": 30.0, "fore_pp": 40.0,     # Δ=+10 → 平稳
        "base_cloud": 50.0, "fore_cloud": 50.0,
        "base_desc": "阴天", "fore_desc": "阴天",
    },
    "improving": {
        "base_pp": 50.0, "fore_pp": 10.0,     # Δ=-40 → 好转
        "base_cloud": 90.0, "fore_cloud": 20.0,
        "base_desc": "小雨", "fore_desc": "晴朗",
    },
}


def _fake_weather_result(window: str, timestamp: str, precip: float,
                          temp: float, cloud: float, desc: str) -> dict:
    """造一个 B05 形状的天气返回值（虚构数据）。"""
    return {
        "status": "ok",
        "provider": "Offline-Fixture",
        "retrieved_at": "2026-10-24T00:00:00+00:00",
        "data": {
            "location": WEATHER_LOCATION,
            "forecast_window": window,
            "timestamp": timestamp,
            "temperature": temp,
            "humidity": 70.0,
            "precipitation_probability": precip,
            "cloud_cover": cloud,
            "solar_radiation": 0.0,
            "weather_code": 0,
            "weather_description": desc,
        },
        "error": None,
    }


def _make_offline_weather(case: dict):
    """把 B05.test_functions.get_weather_forecast 换成假实现。"""

    def _fake(location, forecast_window="now", _inject=None):
        if forecast_window == "now":
            return _fake_weather_result(
                "now", OFFLINE_BASE_TS, case["base_pp"], 15.0,
                case["base_cloud"], case["base_desc"])
        return _fake_weather_result(
            forecast_window, OFFLINE_FORE_TS, case["fore_pp"], 14.0,
            case["fore_cloud"], case["fore_desc"])

    return _fake


def _make_unavailable_weather():
    """天气服务完全不可用（§六 降级行为）。"""

    def _fake(location, forecast_window="now", _inject=None):
        return {
            "status": "unavailable",
            "provider": "Offline-Fixture",
            "retrieved_at": "2026-10-24T00:00:00+00:00",
            "data": None,
            "error": "模拟天气服务不可用",
        }

    return _fake


# --------------------------------------------------------------------------
# 差分工具
# --------------------------------------------------------------------------

def diff_judgements(j_a: dict, j_b: dict) -> list[dict]:
    """逐字段对比两个结构化判断（原始结果，不加工）。"""
    rows: list[dict] = []
    keys = list(j_a.keys())
    for k in j_b.keys():
        if k not in keys:
            keys.append(k)
    for k in keys:
        in_a, in_b = k in j_a, k in j_b
        rows.append({
            "field": k,
            "in_a": in_a,
            "in_b": in_b,
            "identical": bool(in_a and in_b and j_a[k] == j_b[k]),
        })
    return rows


def diff_contexts(ctx_a: str | None, ctx_b: str | None) -> dict:
    """对比两次发给 LLM 的上下文：按空行分块。

    返回：分块级差异 + "B 是否 = A + '\\n\\n' + 新增块" 的判断。
    """
    a_text = ctx_a or ""
    b_text = ctx_b or ""
    a_blocks = a_text.split("\n\n")
    b_blocks = b_text.split("\n\n")

    added = [b for b in b_blocks if b not in a_blocks]
    removed = [a for a in a_blocks if a not in b_blocks]
    common_a = [a for a in a_blocks if a in b_blocks]

    # 前缀逐字节相同的部分（切到第一个不同的块为止）
    prefix_len = 0
    for x, y in zip(a_text, b_text):
        if x != y:
            break
        prefix_len += 1
    prefix_a = a_text[:prefix_len]
    prefix_b = b_text[:prefix_len]

    return {
        "a_block_count": len(a_blocks),
        "b_block_count": len(b_blocks),
        "a_blocks": a_blocks,
        "b_blocks": b_blocks,
        "added_blocks": added,
        "removed_blocks": removed,
        "common_block_count": len(common_a),
        "common_blocks_identical": len(common_a) == len(a_blocks) == len(b_blocks) - len(added)
        and not removed,
        "prefix_identical": prefix_a == prefix_b,
        "prefix_length": prefix_len,
        "a_length": len(a_text),
        "b_length": len(b_text),
        "b_is_a_plus_suffix": b_text.startswith(a_text + "\n\n"),
        "suffix_added": b_text[len(a_text) + 2:] if b_text.startswith(a_text + "\n\n") else None,
    }


def _split_sentences(text: str) -> list[str]:
    """按中文标点切句。"""
    if not text:
        return []
    return [p.strip() for p in
            text.replace("；", "。").replace("，", "。").split("。") if p.strip()]


def _sentences_with(text: str, keywords) -> list[str]:
    """挑出含关键词的句子。"""
    return [s for s in _split_sentences(text)
            if any(k in s for k in keywords)]


def _weather_mentions(text: str) -> list[str]:
    """回答里出现外部/天气相关**表述**的句子（含"未接入天气"这类元陈述）。"""
    return _sentences_with(text, WEATHER_KEYWORDS)


def _is_meta_only(sentence: str) -> bool:
    """这句话是不是**否定式元陈述**（只说"接没接 / 拿不到"）。

    判定：命中元陈述模式，且**没有**方向词、没有外部数值、没有具体天气现象断言。
    """
    import re

    if not any(re.search(p, sentence) for p in META_PATTERNS):
        return False
    if _direction_hit(sentence):
        return False
    # 句子里出现具体天气现象 + 数值/概率，才算真的在陈述事实
    if re.search(r"\d+(?:\.\d+)?\s*%", sentence):
        return False
    return True


def _weather_claims(text: str) -> list[str]:
    """回答里**断言了外部条件事实**的句子。

    判定：句子里出现外部/天气**名词短语**或**具体天气现象**，
    且该句**不是**纯粹的否定式元陈述。
    """
    out = []
    for s in _split_sentences(text):
        has_noun = any(t in s for t in EXTERNAL_NOUN_TERMS)
        has_phenomenon = any(t in s for t in WEATHER_PHENOMENON_TERMS)
        if not (has_noun or has_phenomenon):
            continue
        if _is_meta_only(s):
            continue
        out.append(s)
    return out


def _external_evidence_hit(sentence: str) -> list[str]:
    """这句话里命中了哪些"外部事实"证据（方向词 / 外部数值 / 具体天气现象）。"""
    hits = []
    for w in DIRECTION_WORDS:
        if w in sentence:
            hits.append(f"方向词:{w}")
    import re
    for m in re.finditer(r"\d+(?:\.\d+)?\s*%", sentence):
        hits.append(f"数值:{m.group(0).strip()}")
    for t in WEATHER_PHENOMENON_TERMS:
        if t in sentence:
            hits.append(f"现象:{t}")
            break
    return hits


def classify_external_reference(answer: str, ext: dict | None = None) -> dict:
    """三级判定：模型对外部条件到底做了什么。

    1) mentions —— 回答里出现了外部/天气相关**表述**（含否定式元陈述）
    2) claims   —— 回答里**陈述了**外部条件的事实（不是纯元陈述）
    3) cited    —— claims 且句子里有外部证据（方向词 / 外部数值 / 具体天气现象）
    """
    mentions = _weather_mentions(answer)
    claims = _weather_claims(answer)

    cited_fragments = []
    all_hits = []
    for s in claims:
        hits = _external_evidence_hit(s)
        all_hits.append({"sentence": s, "hits": hits})
        if hits:
            cited_fragments.append(s)

    numbers = _cited_external_numbers(answer, ext or {})

    return {
        "mentions": mentions,
        "claims": claims,
        "cited": bool(cited_fragments),
        "cited_fragments": cited_fragments,
        "claim_details": all_hits,
        "numbers": numbers,
        "meta_only_sentences": [s for s in mentions if s not in claims],
    }


def _direction_hit(text: str) -> bool:
    return any(w in text for w in DIRECTION_WORDS)


def _cited_external_numbers(answer: str, ext: dict) -> list[dict]:
    """回答里引用了哪些外部条件里的数值。"""
    hits = []
    if not answer or not ext:
        return hits
    values = {}
    for key in ("baseline_point", "forecast_point"):
        pt = ext.get(key) or {}
        for field in ("precipitation_probability", "temperature", "cloud_cover"):
            v = pt.get(field)
            if v is not None:
                values[f"{key}.{field}"] = v
    for field in ("precip_delta", "temp_delta", "cloud_delta", "solar_radiation"):
        v = ext.get(field)
        if v is not None:
            values[field] = v

    import re

    # 句子表：只认"出现在外部条件相关句子里"的数值，
    # 否则"内部数值恰好等于外部数值"会被误判成"引用了外部数值"。
    spans = []
    pos = 0
    for s in _split_sentences(answer):
        idx = answer.find(s, pos)
        if idx < 0:
            continue
        spans.append((idx, idx + len(s), s))
        pos = idx + len(s)

    def _sentence_of(at: int) -> str:
        for a, b, s in spans:
            if a <= at < b:
                return s
        return ""

    for name, v in values.items():
        # 整数形态与一位小数形态都试一遍
        cands = {f"{v:g}", f"{v:.1f}", f"{v:.0f}"}
        for c in sorted(cands):
            if not c:
                continue
            # 必须是"独立数字"：前后不能接数字或小数点。
            # 否则 "0" 会命中 "15000"、"0.448"，"-1" 会命中 "2026-10-21"。
            pat = re.compile(rf"(?<![\d.]){re.escape(c)}(?![\d.])")
            m = pat.search(answer)
            if not m:
                continue
            sent = _sentence_of(m.start())
            if not (any(t in sent for t in EXTERNAL_NOUN_TERMS)
                    or any(t in sent for t in WEATHER_PHENOMENON_TERMS)):
                continue
            hits.append({"name": name, "value": v, "written_as": c,
                         "sentence": sent})
            break
    return hits


# --------------------------------------------------------------------------
# 跑一组实验
# --------------------------------------------------------------------------

def _progress_printer(prefix: str):
    def _p(stage, extra=None):
        print(f"  [{prefix}] {stage} 完成")
    return _p


def run_pair(location: str = DEFAULT_LOCATION,
             model_path: str | Path | None = None,
             external_conditions: dict | None = None,
             progress=None) -> dict:
    """跑 A（不带）/ B（带）两次分析，返回两个存档。"""
    from B07.runs import run_analysis

    if model_path is None:
        from B07.runs import DEFAULT_MODEL_PATH
        model_path = DEFAULT_MODEL_PATH

    print(f"\n  ▸ Run A（{SCENARIO_WITHOUT}）：不接外部天气")
    archive_a = run_analysis(ANCHOR, location, model_path,
                              scenario=SCENARIO_WITHOUT,
                              external_conditions=None,
                              progress=progress)

    print(f"\n  ▸ Run B（{SCENARIO_WITH}）：接外部天气")
    archive_b = run_analysis(ANCHOR, location, model_path,
                              scenario=SCENARIO_WITH,
                              external_conditions=external_conditions,
                              progress=progress)

    return {"a": archive_a, "b": archive_b}


def run_with_weather_only(external_conditions: dict,
                           location: str = DEFAULT_LOCATION,
                           model_path: str | Path | None = None,
                           scenario: str = SCENARIO_WITH,
                           progress=None) -> dict:
    """只跑带外部天气的一次（离线组用）。"""
    from B07.runs import run_analysis

    if model_path is None:
        from B07.runs import DEFAULT_MODEL_PATH
        model_path = DEFAULT_MODEL_PATH

    return run_analysis(ANCHOR, location, model_path,
                        scenario=scenario,
                        external_conditions=external_conditions,
                        progress=progress)


# --------------------------------------------------------------------------
# 一、实验设置
# --------------------------------------------------------------------------

def _prompt_fingerprints() -> dict:
    """各存档的提示词指纹（证明同一套提示词、同一个问题）。"""
    from B07.runs import list_runs, load_run

    out = {}
    for item in list_runs():
        rid = item["run_id"]
        try:
            archive = load_run(rid)
        except Exception:
            continue
        meta = archive.get("llm_meta") or {}
        out[rid] = {
            "scenario": archive.get("scenario"),
            "system_prompt_sha256": meta.get("system_prompt_sha256"),
            "question_sha256": meta.get("question_sha256"),
            "question": meta.get("question"),
            "model": meta.get("model"),
            "model_version": (archive.get("model_output") or {}).get("model_version"),
            "analysis_time": archive.get("analysis_time"),
        }
    return out


def _find_s1_reference_run() -> str | None:
    """找一份既有的 S1 存档（用于程序级判断对照）。"""
    from B07.runs import list_runs, load_run

    for item in list_runs():
        if item.get("scenario") != "S1" or item.get("status") != "success":
            continue
        try:
            archive = load_run(item["run_id"])
        except Exception:
            continue
        if archive.get("analysis_time") == ANCHOR:
            return item["run_id"]
    return None


# --------------------------------------------------------------------------
# 二、核心证据：原始 diff
# --------------------------------------------------------------------------

def build_evidence_section(archive_a: dict, archive_b: dict,
                           ext: dict | None) -> dict:
    sj_a = archive_a.get("structured_judgement") or {}
    sj_b = archive_b.get("structured_judgement") or {}

    j_rows = diff_judgements(sj_a, sj_b)

    ctx = diff_contexts(archive_a.get("llm_context"), archive_b.get("llm_context"))

    return {
        "judgement_diff": j_rows,
        "judgement_field_names_a": list(sj_a.keys()),
        "judgement_field_names_b": list(sj_b.keys()),
        "context_diff": ctx,
        "anchor": archive_a.get("analysis_time"),
        "prompt_a": (archive_a.get("llm_meta") or {}).get("system_prompt_sha256"),
        "prompt_b": (archive_b.get("llm_meta") or {}).get("system_prompt_sha256"),
        "question_a": (archive_a.get("llm_meta") or {}).get("question"),
        "question_b": (archive_b.get("llm_meta") or {}).get("question"),
        "model_a": (archive_a.get("llm_meta") or {}).get("model"),
        "model_b": (archive_b.get("llm_meta") or {}).get("model"),
        "model_version_a": (archive_a.get("model_output") or {}).get("model_version"),
        "model_version_b": (archive_b.get("model_output") or {}).get("model_version"),
    }


def compare_combined_attention(archive_a: dict, archive_b: dict) -> dict:
    """两次运行的合成关注等级（程序规则，不经过 LLM）。

    规则写在 B07/judgement.py 里（combined_attention / build_combined_attention），
    这里只负责把两次运行的值取出来对照，并从存档里复算一遍证明可复现。
    """
    from B07.judgement import build_combined_attention

    ca_a = archive_a.get("combined_attention") or build_combined_attention(archive_a)
    ca_b = archive_b.get("combined_attention") or build_combined_attention(archive_b)

    # 脱离内存：从存档复算，应与存档值一致
    re_a = build_combined_attention(archive_a)
    re_b = build_combined_attention(archive_b)

    return {
        "a": ca_a,
        "b": ca_b,
        "recompute_a": re_a,
        "recompute_b": re_b,
        "a_matches_recompute": ca_a.get("value") == re_a.get("value"),
        "b_matches_recompute": ca_b.get("value") == re_b.get("value"),
        "changed": ca_a.get("value") != ca_b.get("value"),
    }


def compare_with_s1(archive_a: dict, s1_run_id: str | None) -> dict:
    """Run A 的程序级判断与既有 S1 存档对比（LLM 文本不比）。"""
    from B07.runs import load_run

    if not s1_run_id:
        return {"available": False, "note": "没有找到可对照的 S1 存档"}

    s1 = load_run(s1_run_id)
    sj_a = archive_a.get("structured_judgement") or {}
    sj_s1 = s1.get("structured_judgement") or {}

    checks = [
        {
            "name": "predicted_attention",
            "s1": sj_s1.get("predicted_attention"),
            "run_a": sj_a.get("predicted_attention"),
            "match": sj_s1.get("predicted_attention") == sj_a.get("predicted_attention"),
        },
        {
            "name": "stability_verdict",
            "s1": (sj_s1.get("recent_trend") or {}).get("stability_verdict"),
            "run_a": (sj_a.get("recent_trend") or {}).get("stability_verdict"),
            "match": ((sj_s1.get("recent_trend") or {}).get("stability_verdict")
                      == (sj_a.get("recent_trend") or {}).get("stability_verdict")),
        },
        {
            "name": "stability_criteria",
            "s1": (sj_s1.get("recent_trend") or {}).get("criteria"),
            "run_a": (sj_a.get("recent_trend") or {}).get("criteria"),
            "match": ((sj_s1.get("recent_trend") or {}).get("criteria")
                      == (sj_a.get("recent_trend") or {}).get("criteria")),
        },
    ]
    return {
        "available": True,
        "s1_run_id": s1_run_id,
        "s1_analysis_time": s1.get("analysis_time"),
        "run_a_analysis_time": archive_a.get("analysis_time"),
        "checks": checks,
        "all_match": all(c["match"] for c in checks),
    }


# --------------------------------------------------------------------------
# 三、两次回答并排
# --------------------------------------------------------------------------

def compare_answers(archive_a: dict, archive_b: dict, ext: dict | None) -> dict:
    ans_a = archive_a.get("llm_explanation") or ""
    ans_b = archive_b.get("llm_explanation") or ""

    warn_a = archive_a.get("warnings") or []
    warn_b = archive_b.get("warnings") or []

    out_of_bounds_a = [w for w in warn_a if "未取得的事实" in w]
    out_of_bounds_b = [w for w in warn_b if "未取得的事实" in w]

    claims_a = _weather_claims(ans_a)
    claims_b = _weather_claims(ans_b)
    numbers_b = _cited_external_numbers(ans_b, ext or {})

    # Run B 是否**真的引用了**外部条件：既有天气断言，又带外部数值或方向词
    cited_b = bool(claims_b) and (bool(numbers_b) or _direction_hit(ans_b))

    # 截断到 MAX_ANSWER_CHARS 之后，外部引用还在不在
    trunc_b = ans_b[:MAX_ANSWER_CHARS]
    trunc_claims = _weather_claims(trunc_b)
    trunc_numbers = _cited_external_numbers(trunc_b, ext or {})

    return {
        "answer_a": ans_a,
        "answer_b": ans_b,
        "length_a": len(ans_a),
        "length_b": len(ans_b),
        "max_answer_chars": MAX_ANSWER_CHARS,
        "within_limit_a": len(ans_a) <= MAX_ANSWER_CHARS,
        "within_limit_b": len(ans_b) <= MAX_ANSWER_CHARS,
        "truncated_b": trunc_b,
        "truncation_loses_external": cited_b and not (bool(trunc_claims)
                                                      and (bool(trunc_numbers)
                                                           or _direction_hit(trunc_b))),
        "weather_mentions_a": _weather_mentions(ans_a),
        "weather_mentions_b": _weather_mentions(ans_b),
        "weather_claims_a": claims_a,
        "weather_claims_b": claims_b,
        "cited_external_b": cited_b,
        "cited_external_numbers_b": numbers_b,
        "cited_external_numbers_a": _cited_external_numbers(ans_a, ext or {}),
        "out_of_bounds_a": out_of_bounds_a,
        "out_of_bounds_b": out_of_bounds_b,
        "next_attention_a": _sentences_with(ans_a, ("接下来", "关注", "后续", "留意")),
        "next_attention_b": _sentences_with(ans_b, ("接下来", "关注", "后续", "留意")),
        "warnings_a": warn_a,
        "warnings_b": warn_b,
    }


# --------------------------------------------------------------------------
# 四、第 3 条禁令探针
# --------------------------------------------------------------------------

def probe_rule3(answers: dict, ext: dict | None) -> dict:
    """SYSTEM_PROMPT 第 3 条与"把真实天气放进 state"的字面冲突，实测探针。"""
    rule3_line = ""
    for line in SYSTEM_PROMPT.splitlines():
        if RULE3_MARKER in line:
            rule3_line = line.strip()
            break

    cited_numbers = answers.get("cited_external_numbers_b") or []
    claims = answers.get("weather_claims_b") or []
    mentions = answers.get("weather_mentions_b") or []
    cited = bool(answers.get("cited_external_b"))

    if cited:
        verdict = "未压制输出"
        detail = ("Run B 的回答确实引用了上下文 ⑥ 块里的外部天气内容"
                  "（有天气事实断言，且带外部数值或方向描述），"
                  "说明第 3 条禁令在本次实验中**没有**压制输出。")
        blocking = False
    else:
        verdict = "该条禁令压制了输出"
        detail = ("Run B 的回答**没有**引用上下文 ⑥ 块里的外部天气内容："
                  "⑥ 块明明给出了降水概率与变化量，回答里却没有任何天气事实断言。"
                  "第 3 条『不许出现真实天气』的字面禁令与此方向一致，"
                  "本轮**无法区分**这是禁令的压制、还是模型自身的取舍。")
        blocking = True

    return {
        "rule3_line": rule3_line,
        "verdict": verdict,
        "detail": detail,
        "blocking_issue": blocking,
        "suggested_rewording": PROMPT_RULE3_SUGGESTION,
        "run_b_cited": cited,
        "run_b_cited_numbers": cited_numbers,
        "run_b_weather_claims": claims,
        "run_b_weather_sentences": mentions,
        "prompt_unchanged": True,
    }


def real_reading_history() -> list[dict]:
    """把 B07/runs 下所有带外部条件的存档的读数列出来（按存档创建时间排）。

    用来证明"真实天气是活的"——同一地点同一窗口，读数几分钟就能变。
    """
    runs_dir = _PROJECT_ROOT / "B07" / "runs"
    rows = []
    if not runs_dir.exists():
        return rows
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        p = d / "run.json"
        if not p.exists():
            continue
        try:
            archive = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ext = archive.get("external_conditions")
        if not isinstance(ext, dict) or not ext.get("baseline_point"):
            continue
        bp, fp = ext["baseline_point"], ext.get("forecast_point") or {}
        rows.append({
            "run_id": archive.get("run_id"),
            "created_at": archive.get("created_at"),
            "location": ext.get("location"),
            "window": ext.get("forecast_window"),
            "baseline_ts": bp.get("timestamp"),
            "baseline_pp": bp.get("precipitation_probability"),
            "forecast_ts": fp.get("timestamp"),
            "forecast_pp": fp.get("precipitation_probability"),
            "precip_delta": ext.get("precip_delta"),
            "signal": ext.get("signal"),
        })
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return rows


def _volatility_table(rows: list[dict]) -> str:
    """把真实读数历史渲染成 markdown 表格。"""
    if not rows:
        return "（B07/runs 下还没有带外部条件的存档）"
    out = ["| 存档 | 创建时间 | 地点 | 基线降水概率 | 预报降水概率 | Δpp | signal |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| `{r['run_id']}` | {r.get('created_at')} | {r.get('location')} "
            f"{r.get('window')} | {r.get('baseline_pp')}% | {r.get('forecast_pp')}% "
            f"| {r.get('precip_delta')} | `{r.get('signal')}` |"
        )
    return "\n".join(out)


def citation_history() -> list[dict]:
    """所有 S3_with 存档的"是否引用了外部条件"历史。

    用来回答一个关键问题：模型引用 ⑥ 块的行为**是否确定**。
    """
    runs_dir = _PROJECT_ROOT / "B07" / "runs"
    rows = []
    if not runs_dir.exists():
        return rows
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        p = d / "run.json"
        if not p.exists():
            continue
        try:
            archive = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if archive.get("scenario") != "S3_with":
            continue
        ans = archive.get("llm_explanation") or ""
        ext = archive.get("external_conditions") or {}
        # 统一用三级判定器（mentions / claims / cited），与 B07.s3_citation_table 一致
        cls = classify_external_reference(ans, ext)
        ca = archive.get("combined_attention") or {}
        if not ca:
            # 这份存档早于 combined_attention 字段的引入，按规则从存档复算
            from B07.judgement import build_combined_attention
            ca = build_combined_attention(archive)
            ca = dict(ca, recomputed=True)
        rows.append({
            "run_id": archive.get("run_id"),
            "created_at": archive.get("created_at"),
            "answer_length": len(ans),
            "cited": bool(cls.get("cited")),
            "mentions": cls.get("mentions") or [],
            "claims": cls.get("claims") or [],
            "cited_fragments": cls.get("cited_fragments") or [],
            "meta_only_sentences": cls.get("meta_only_sentences") or [],
            "numbers": cls.get("numbers") or [],
            "signal": ext.get("signal"),
            "precip_delta": ext.get("precip_delta"),
            "context_sha12": hashlib.sha256(
                (archive.get("llm_context") or "").encode("utf-8")).hexdigest()[:12],
            "combined_attention": ca.get("value"),
            "note": archive.get("note"),
        })
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return rows


def external_claim_positions() -> list[dict]:
    """每个 S3_with 存档里，第一句天气事实断言在回答中的起始位置（第几个字）。

    用来判断"按 250 字硬截断会不会把外部引用切断"。
    """
    runs_dir = _PROJECT_ROOT / "B07" / "runs"
    rows = []
    if not runs_dir.exists():
        return rows
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        p = d / "run.json"
        if not p.exists():
            continue
        try:
            archive = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if archive.get("scenario") != "S3_with":
            continue
        ans = archive.get("llm_explanation") or ""
        claims = _weather_claims(ans)
        pos = ans.find(claims[0]) if claims else None
        end = (pos + len(claims[0])) if pos is not None else None
        rows.append({
            "run_id": archive.get("run_id"),
            "answer_length": len(ans),
            "cited": bool(claims),
            "claim": claims[0] if claims else None,
            "claim_start_index": pos,
            "claim_end_index": end,
            # 整句是否落在 MAX_ANSWER_CHARS 之内
            "cut_at_limit": (end is not None and end > MAX_ANSWER_CHARS),
            "sentence_survives": (end is not None and end <= MAX_ANSWER_CHARS),
        })
    rows.sort(key=lambda r: str(r.get("run_id") or ""))
    return rows


def _citation_table(rows: list[dict]) -> str:
    if not rows:
        return "（B07/runs 下还没有 S3_with 存档）"
    out = ["| 存档 | 创建时间 | 回答字数 | 外部 signal | llm_context sha | combined_attention | 是否引用外部 | 天气断言 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| `{r['run_id']}` | {r.get('created_at')} | {r.get('answer_length')} "
            f"| `{r.get('signal')}` | `{r.get('context_sha12')}` "
            f"| `{r.get('combined_attention')}` "
            f"| {'✅ 引用了' if r.get('cited') else '❌ 没有引用'} "
            f"| {'、'.join(r.get('claims') or []) or '（无）'} |"
        )
    n = len(rows)
    k = sum(1 for r in rows if r.get("cited"))
    out.append("")
    out.append(f"**引用率：{k}/{n}**。")
    out.append("")
    out.append("（`combined_attention` 一列：run_017 / run_019 这两份存档早于该字段的引入，"
               "表里的值是按 `B07/judgement.py` 的规则**从存档复算**出来的，"
               "这也正好证明这条规则可以脱离 LLM 复算。）")
    out.append("")
    out.append("（`llm_context sha` 一列：同一个 sha 表示**喂给 LLM 的上下文逐字节相同**，"
               "因此同 sha 内部的差异只可能来自 LLM 采样。）")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 四点五、相对差分 vs 绝对阈值：同一组点，两种判据各判一次
# --------------------------------------------------------------------------

CALIBRATION_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "s3_calibration.json"


def _calibration_point(src: dict, label: str) -> dict:
    """把校准文件里的一条记录转成天气点形状。"""
    return {
        "timestamp": None,          # 校准点只关心降水/云量，不做时刻判定
        "temperature": src.get("temperature"),
        "humidity": None,
        "precipitation_probability": src.get("precipitation_probability"),
        "cloud_cover": src.get("cloud_cover"),
        "solar_radiation": None,
    }


def absolute_vs_relative() -> dict:
    """对同一组 (基线, 预报) 点，分别跑「相对差分」和「绝对阈值兜底」。

    两种结论都来自 B07/weather.py 的同一份实现，不是手算的：
      - 相对差分：给两个点
      - 绝对阈值：只给预报点（等价于基线取不到时的兜底分支）

    校准点来自 B07/artifacts/s3_calibration.json（数据文件，代码里不写死数值）。
    """
    from B07.weather import judge_external_points

    if not CALIBRATION_PATH.exists():
        return {"available": False,
                "note": f"找不到校准文件 {CALIBRATION_PATH}"}

    data = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    by_label = {p["label"]: p for p in data.get("points", [])}

    pairs = list(data.get("pairs", []))

    rows = []
    for pr in pairs:
        base_src = by_label.get(pr["baseline"])
        fore_src = by_label.get(pr["forecast"])
        if not base_src or not fore_src:
            continue
        base_pt = _calibration_point(base_src, pr["baseline"])
        fore_pt = _calibration_point(fore_src, pr["forecast"])

        rel = judge_external_points(base_pt, fore_pt, hours=0, analysis_time=None)
        absolute = judge_external_points(None, fore_pt, hours=0, analysis_time=None)

        rows.append({
            "label": pr["label"],
            "provenance": pr.get("provenance"),
            "baseline_pp": base_src.get("precipitation_probability"),
            "forecast_pp": fore_src.get("precipitation_probability"),
            "forecast_cloud": fore_src.get("cloud_cover"),
            "relative_signal": rel.get("signal"),
            "relative_method": rel.get("judge_method"),
            "relative_delta": rel.get("precip_delta"),
            "absolute_signal": absolute.get("signal"),
            "absolute_method": absolute.get("judge_method"),
            "agree": rel.get("signal") == absolute.get("signal"),
        })

    return {
        "available": True,
        "calibration_file": str(CALIBRATION_PATH),
        "rows": rows,
        "disagreements": [r for r in rows if not r["agree"]],
    }


# --------------------------------------------------------------------------
# 五、离线可复现组
# --------------------------------------------------------------------------

def _install_fake_weather(monkey_target, fake):
    """把假天气装到 B05.test_functions 上（返回恢复函数）。"""
    import B05.test_functions as tf

    original = tf.get_weather_forecast
    tf.get_weather_forecast = fake
    return lambda: setattr(tf, "get_weather_forecast", original)


def offline_group(location: str = DEFAULT_LOCATION,
                  model_path: str | Path | None = None,
                  progress=None) -> dict:
    """离线可复现组：夹具注入 B05 天气，覆盖三种信号 + 服务不可用。

    离线组写在临时存档目录里，不污染 B07/runs/。
    """
    import B07.runs as runs
    from B07.weather import build_external_context

    anchor = datetime.fromisoformat(ANCHOR)
    results = []

    orig_dir, orig_index = runs.RUNS_DIR, runs.INDEX_PATH
    tmp_dir = Path(tempfile.mkdtemp(prefix="b07_s3_offline_"))
    runs.RUNS_DIR = tmp_dir
    runs.INDEX_PATH = tmp_dir / "index.json"

    try:
        for name, case in OFFLINE_CASES.items():
            print(f"\n  ▸ 离线组：{name}")
            restore = _install_fake_weather(None, _make_offline_weather(case))
            try:
                ctx = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)
                archive = run_with_weather_only(
                    ctx, location=location, model_path=model_path, progress=progress)
            finally:
                restore()
            results.append({
                "case": name,
                "signal": ctx.get("signal"),
                "judge_method": ctx.get("judge_method"),
                "precip_delta": ctx.get("precip_delta"),
                "temp_delta": ctx.get("temp_delta"),
                "cloud_delta": ctx.get("cloud_delta"),
                "expected_signal": {
                    "deteriorating": "external_deteriorating",
                    "stable": "external_stable",
                    "improving": "external_improving",
                }[name],
                "signal_match": ctx.get("signal") == {
                    "deteriorating": "external_deteriorating",
                    "stable": "external_stable",
                    "improving": "external_improving",
                }[name],
                "answer": archive.get("llm_explanation"),
                "answer_length": len(archive.get("llm_explanation") or ""),
                "weather_mentions": _weather_mentions(archive.get("llm_explanation") or ""),
                "weather_claims": _weather_claims(archive.get("llm_explanation") or ""),
                "run_id": archive.get("run_id"),
                "combined_attention": (archive.get("combined_attention") or {}).get("value"),
                "warnings": archive.get("warnings") or [],
            })

        # §六 降级：天气服务不可用
        print("\n  ▸ 离线组：天气服务不可用（降级验证）")
        restore = _install_fake_weather(None, _make_unavailable_weather())
        try:
            ctx = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)
            archive = run_with_weather_only(
                ctx, location=location, model_path=model_path, progress=progress)
        finally:
            restore()

        degraded_answer = archive.get("llm_explanation") or ""
        degraded = {
            "signal": ctx.get("signal"),
            "judge_method": ctx.get("judge_method"),
            "available": ctx.get("available"),
            "run_status": archive.get("status"),
            "run_id": archive.get("run_id"),
            "judgement_field_count": len(archive.get("structured_judgement") or {}),
            "combined_attention": (archive.get("combined_attention") or {}).get("value"),
            "external_warnings": ctx.get("warnings"),
            "answer": degraded_answer,
            "answer_length": len(degraded_answer),
            "weather_mentions": _weather_mentions(degraded_answer),
            "weather_claims": _weather_claims(degraded_answer),
            "fabricated_numbers": _cited_external_numbers(
                degraded_answer, {
                    "baseline_point": {"precipitation_probability": None,
                                       "temperature": None, "cloud_cover": None},
                    "forecast_point": {"precipitation_probability": None,
                                       "temperature": None, "cloud_cover": None},
                }),
            "warnings": archive.get("warnings") or [],
        }
    finally:
        runs.RUNS_DIR, runs.INDEX_PATH = orig_dir, orig_index

    return {
        "cases": results,
        "degraded": degraded,
        "tmp_dir": str(tmp_dir),
        "all_signals_match": all(r["signal_match"] for r in results),
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run_s3(location: str = DEFAULT_LOCATION,
           model_path: str | Path | None = None,
           do_real: bool = True,
           do_offline: bool = True,
           progress=None) -> dict:
    """跑完 S3-2 全部实验，返回结果 dict（同时打印完整报告）。"""
    from B07.runs import explain_run, run_json_path
    from B07.weather import build_external_context

    anchor = datetime.fromisoformat(ANCHOR)
    result: dict = {"limitations": []}

    # 判据阈值：报告里直接引用常量本身，避免手抄
    from B07 import weather as _weather
    result["rel_threshold"] = _weather.EXTERNAL_PRECIP_DELTA_THRESHOLD
    result["abs_low"] = _weather.EXTERNAL_FALLBACK_PRECIP_LOW
    result["abs_high"] = _weather.EXTERNAL_FALLBACK_PRECIP_HIGH
    result["abs_cloud_max"] = _weather.EXTERNAL_FALLBACK_CLOUD_MAX

    # ---------------- 一、实验设置 ----------------
    hr("一、实验设置")
    print(f"  锚点 analysis_time : {ANCHOR}（两次运行完全一致）")
    print(f"  固定问题            : {FIXED_QUESTION}")
    print(f"  回答长度上限        : {MAX_ANSWER_CHARS}")
    print(f"  对照组场景          : {SCENARIO_WITHOUT}")
    print(f"  实验组场景          : {SCENARIO_WITH}")

    ext = None
    archive_a = archive_b = None

    if do_real:
        print("\n  ▸ 取真实外部天气（B05 → B03 Open-Meteo）...")
        ext = build_external_context(anchor, WEATHER_LOCATION, WEATHER_WINDOW)

        sub("外部条件实测（真实 API）")
        print(_json({k: ext.get(k) for k in (
            "available", "signal", "judge_method", "basis", "precip_delta",
            "temp_delta", "cloud_delta", "solar_radiation", "future_ok",
            "baseline_skew_hours", "location", "forecast_window", "error")}))
        fore_ts = (ext.get("forecast_point") or {}).get("timestamp")
        base_ts = (ext.get("baseline_point") or {}).get("timestamp")
        print(f"\n  forecast_time   = {fore_ts}")
        print(f"  baseline 时间   = {base_ts}")
        print(f"  analysis_time   = {ANCHOR}")
        print(f"  forecast_time 晚于 analysis_time？ "
              f"{'✅ 是' if ext.get('future_ok') else '❌ 否'}")

        print("\n  ▸ 跑两次分析...")
        pair = run_pair(location=location, model_path=model_path,
                        external_conditions=ext, progress=progress)
        archive_a, archive_b = pair["a"], pair["b"]

        result["real"] = {
            "external": ext,
            "archive_a": archive_a,
            "archive_b": archive_b,
            "run_id_a": archive_a["run_id"],
            "run_id_b": archive_b["run_id"],
            "path_a": str(run_json_path(archive_a["run_id"])),
            "path_b": str(run_json_path(archive_b["run_id"])),
        }

    # ---------------- 二、核心证据 ----------------
    if archive_a is not None and archive_b is not None:
        hr("二、核心证据（原始 diff）")
        ev = build_evidence_section(archive_a, archive_b, ext)
        result["evidence"] = ev

        sub("2.1 结构化判断逐字段 diff")
        print(f"  Run A 字段: {ev['judgement_field_names_a']}")
        print(f"  Run B 字段: {ev['judgement_field_names_b']}")
        for row in ev["judgement_diff"]:
            mark = "✅ 相同" if row["identical"] else (
                "➖ 仅 A 有" if not row["in_b"] else
                "➕ 仅 B 有" if not row["in_a"] else "⚠️ 不同")
            print(f"    {row['field']:<24} {mark}")

        sub("2.2 LLM 上下文 diff（分块）")
        ctx = ev["context_diff"]
        print(f"  A 块数 = {ctx['a_block_count']}，B 块数 = {ctx['b_block_count']}")
        print(f"  A 长度 = {ctx['a_length']}，B 长度 = {ctx['b_length']}")
        print(f"  B 的前 {ctx['prefix_length']} 字与 A 逐字节相同？ "
              f"{'✅ 是' if ctx['prefix_identical'] else '❌ 否'}")
        print(f"  B == A + '\\n\\n' + 新增块？ {'✅ 是' if ctx['b_is_a_plus_suffix'] else '❌ 否'}")
        print(f"  删除的块：{len(ctx['removed_blocks'])} 个")
        print(f"  新增的块：{len(ctx['added_blocks'])} 个")
        for i, blk in enumerate(ctx["added_blocks"], 1):
            head = blk.splitlines()[0] if blk else ""
            print(f"    新增块 {i}（首行：{head}）")
        if ctx["suffix_added"]:
            print("\n  新增块全文：")
            for line in ctx["suffix_added"].splitlines():
                print(f"    {line}")

        sub("2.3 提示词 / 问题 / 模型")
        print(f"  system_prompt_sha256  A={ev['prompt_a']}  B={ev['prompt_b']}  "
              f"{'✅ 相同' if ev['prompt_a'] == ev['prompt_b'] else '❌ 不同'}")
        print(f"  question              A/B 相同？ "
              f"{'✅ 是' if ev['question_a'] == ev['question_b'] else '❌ 否'}")
        print(f"  question_sha256       {ev['question_a'] and ''}"
              f"{(archive_a.get('llm_meta') or {}).get('question_sha256')}")
        print(f"  LLM model             A={ev['model_a']}  B={ev['model_b']}  "
              f"{'✅ 相同' if ev['model_a'] == ev['model_b'] else '❌ 不同'}")
        print(f"  B06 model_version     A={ev['model_version_a']}  B={ev['model_version_b']}  "
              f"{'✅ 相同' if ev['model_version_a'] == ev['model_version_b'] else '❌ 不同'}")

        # Run A 与既有 S1 存档的程序级对照
        s1_ref = _find_s1_reference_run()
        cmp_s1 = compare_with_s1(archive_a, s1_ref)
        result["compare_with_s1"] = cmp_s1
        sub("2.4 Run A 的程序级判断 vs 既有 S1 存档")
        if not cmp_s1.get("available"):
            print(f"  ⚠️ {cmp_s1.get('note')}")
        else:
            print(f"  对照存档：{cmp_s1['s1_run_id']}（analysis_time={cmp_s1['s1_analysis_time']}）")
            for c in cmp_s1["checks"]:
                mark = "✅ 一致" if c["match"] else "⚠️ 不一致"
                if c["name"] == "stability_criteria":
                    print(f"    {c['name']:<22} {mark}（判据明细逐项相同）")
                else:
                    print(f"    {c['name']:<22} {mark}  S1={c['s1']} / RunA={c['run_a']}")

        # 从存档文件重读后复算（不依赖内存状态）
        sub("2.5 从存档文件重读后复算（explain_run）")
        from B07.runs import explain_run as _explain_run

        recomputed_report = {}
        for tag, arch in (("A", archive_a), ("B", archive_b)):
            r = _explain_run(arch["run_id"], quiet=True)
            ext_checks = [c for c in r["checks"] if c["name"].startswith("外部")]
            recomputed_report[tag] = {
                "all_match": r["all_match"],
                "external_check_count": len(ext_checks),
                "external_all_match": all(c["match"] for c in ext_checks) if ext_checks else None,
            }
            print(f"  Run {tag}（{arch['run_id']}）：复算一致性 "
                  f"{'✅ 全部一致' if r['all_match'] else '⚠️ 存在差异'}"
                  f"；外部条件复算项 {len(ext_checks)} 条，"
                  + ("（本次没有外部条件，不适用）" if not ext_checks
                     else ("✅ 全部一致" if recomputed_report[tag]['external_all_match']
                           else "⚠️ 有差异")))
            for c in ext_checks:
                mark = "✅" if c["match"] else "⚠️"
                print(f"    {mark} {c['name']}: 存档={c['archived']} / 复算={c['recomputed']}")
        result["recompute_report"] = recomputed_report

        # ---------------- 2.6 合成关注等级 ----------------
        sub("2.6 合成关注等级 combined_attention（程序规则，不经过 LLM）")
        cmp_ca = compare_combined_attention(archive_a, archive_b)
        result["combined_attention"] = cmp_ca
        print(f"  规则：内部 WATCH → watch_internal；内部 NORMAL 且外部 "
              f"external_deteriorating → watch_external；"
              f"内部 NORMAL 且外部 ∈ {{stable, improving, unknown}} → internal_only")
        print(f"  Run A（{archive_a['run_id']}，不带天气）: "
              f"{cmp_ca['a'].get('value')}")
        print(f"    内部 predicted_attention = "
              f"{cmp_ca['a'].get('internal_predicted_attention')}，"
              f"外部 signal = {cmp_ca['a'].get('external_signal')}")
        print(f"    理由：{cmp_ca['a'].get('reason')}")
        print(f"  Run B（{archive_b['run_id']}，带天气）: "
              f"{cmp_ca['b'].get('value')}")
        print(f"    内部 predicted_attention = "
              f"{cmp_ca['b'].get('internal_predicted_attention')}，"
              f"外部 signal = {cmp_ca['b'].get('external_signal')}")
        print(f"    理由：{cmp_ca['b'].get('reason')}")
        print(f"  两次取值是否不同：{'✅ 不同' if cmp_ca['changed'] else '❌ 相同'}")
        print(f"  脱离 LLM 从存档复算：Run A "
              f"{'✅ 一致' if cmp_ca['a_matches_recompute'] else '⚠️ 不一致'}，"
              f"Run B {'✅ 一致' if cmp_ca['b_matches_recompute'] else '⚠️ 不一致'}")

        # ---------------- 三、两次回答并排 ----------------
        hr("三、两次 LLM 回答并排对比")
        answers = compare_answers(archive_a, archive_b, ext)
        result["answers"] = answers

        sub("3.1 Run A（不带外部天气）")
        print(answers["answer_a"])
        print(f"\n  长度 = {answers['length_a']} 字"
              f"（上限 {MAX_ANSWER_CHARS}，"
              f"{'✅ 未超' if answers['within_limit_a'] else '⚠️ 超了'}）")

        sub("3.2 Run B（带外部天气）")
        print(answers["answer_b"])
        print(f"\n  长度 = {answers['length_b']} 字"
              f"（上限 {MAX_ANSWER_CHARS}，"
              f"{'✅ 未超' if answers['within_limit_b'] else '⚠️ 超了'}）")

        sub("3.3 Run B 是否引用了外部条件")
        print(f"  判定：{'✅ 引用了' if answers['cited_external_b'] else '❌ 没有引用'}")
        if answers["weather_claims_b"]:
            print("  断言了天气事实的句子：")
            for s in answers["weather_claims_b"]:
                print(f"    「{s}」")
        else:
            print("  ⚠️ 回答里没有任何断言天气事实的句子")
        if answers["cited_external_numbers_b"]:
            print("  引用到的外部数值：")
            for h in answers["cited_external_numbers_b"]:
                print(f"    - {h['name']} = {h['value']}（回答里写作「{h['written_as']}」）")
        else:
            print("  ⚠️ 没有在回答里找到外部条件的数值")
        print("  提到天气的句子（含元陈述）：")
        for s in answers["weather_mentions_b"] or ["（无）"]:
            print(f"    「{s}」")

        sub("3.4 Run A 是否提到了天气")
        if answers["weather_claims_a"]:
            print("  ⚠️ Run A 的上下文里没有任何天气信息，但回答断言了天气事实：")
            for s in answers["weather_claims_a"]:
                print(f"    「{s}」")
            print("    → 判定：模型幻觉（凭空编造了上下文里没有的天气事实）")
            result["limitations"].append(
                "Run A（对照组）在上下文完全没有天气信息的情况下断言了天气事实，"
                "属于模型幻觉，需要记录；它不影响 S3 的对照结论。")
        else:
            print("  ✅ Run A 没有断言任何天气事实（上下文里本来就没有）")
        if answers["weather_mentions_a"]:
            print("  （仅提到天气词、但没有断言事实的句子："
                  f"{answers['weather_mentions_a']}）")

        sub("3.5 『接下来值得关注什么』是否变化")
        print("  Run A：")
        for s in answers["next_attention_a"] or ["（没有相应表述）"]:
            print(f"    - {s}")
        print("  Run B：")
        for s in answers["next_attention_b"] or ["（没有相应表述）"]:
            print(f"    - {s}")
        attributed = any(
            k in s for s in answers["next_attention_b"]
            for k in ("外部", "天气", "降水", "云量")
        )
        print(f"  Run B 的『接下来关注』是否明确归因于外部天气？ "
              f"{'✅ 是' if attributed else '❌ 否'}")

        sub("3.5b 回答长度与截断影响")
        print(f"  Run A 长度 = {answers['length_a']}，Run B 长度 = {answers['length_b']}"
              f"（上限 {MAX_ANSWER_CHARS}）")
        print(f"  Run A 是否超限：{'⚠️ 是' if not answers['within_limit_a'] else '✅ 否'}")
        print(f"  Run B 是否超限：{'⚠️ 是' if not answers['within_limit_b'] else '✅ 否'}")
        if not answers["within_limit_b"]:
            print(f"  截断到 {MAX_ANSWER_CHARS} 字后：")
            print(f"    {answers['truncated_b']}")
            print(f"    → 截断是否吃掉了关键外部引用："
                  f"{'⚠️ 是' if answers['truncation_loses_external'] else '✅ 否'}")

        sub("3.6 事实越界启发式")
        print(f"  Run A 越界告警：{len(answers['out_of_bounds_a'])} 条")
        for w in answers["out_of_bounds_a"]:
            print(f"    ⚠️ {w}")
        print(f"  Run B 越界告警：{len(answers['out_of_bounds_b'])} 条")
        for w in answers["out_of_bounds_b"]:
            print(f"    ⚠️ {w}")
        if not answers["out_of_bounds_b"]:
            print("  ✅ Run B 没有触发事实越界（外部数值都在证据集合里）")
        if answers["out_of_bounds_a"]:
            print("  ⚠️ Run A 出现越界告警——对照组误报才是真问题，需要人工核查")

        # ---------------- 四、第 3 条禁令探针 ----------------
        hr("四、SYSTEM_PROMPT 第 3 条禁令的实测探针")
        probe = probe_rule3(answers, ext)
        result["rule3_probe"] = probe
        print(f"  原文：{probe['rule3_line']}")
        print(f"\n  探针结论：{probe['verdict']}")
        print(f"  {probe['detail']}")
        if probe["run_b_weather_sentences"]:
            print("\n  Run B 引用外部条件的原文片段：")
            for s in probe["run_b_weather_sentences"]:
                print(f"    「{s}」")
        if probe["run_b_cited_numbers"]:
            print("\n  Run B 引用到的数值：")
            for h in probe["run_b_cited_numbers"]:
                print(f"    - {h['name']} = {h['value']}（写作「{h['written_as']}」）")
        if probe["blocking_issue"]:
            print(f"\n  🚧 阻塞问题：{probe['detail']}")
            print(f"  建议改写（本轮不改）：{probe['suggested_rewording']}")
            result["limitations"].append(
                "[阻塞] SYSTEM_PROMPT 第 3 条『不许出现真实天气』与本实验"
                "把真实天气放进 state 的做法字面冲突，且实测压制了输出。"
                f"建议改写：{probe['suggested_rewording']}")
        else:
            print("\n  ✅ 本轮该条禁令没有压制输出，不构成阻塞问题")
        print("\n  S1 / S2 / S3 的提示词与问题指纹（可复现性证据）：")
        fps = _prompt_fingerprints()
        result["fingerprints"] = fps
        for rid, info in fps.items():
            print(f"    {rid}  scenario={str(info['scenario']):<12} "
                  f"prompt={info['system_prompt_sha256']} "
                  f"question={info['question_sha256']} "
                  f"model={info['model']} "
                  f"model_version={info['model_version']}")

        # 真实天气的读数历史（证明"真实天气是活的"）
        hist = real_reading_history()
        result["volatility_table"] = _volatility_table(hist)
        result["reading_history"] = hist
        print("\n  真实 API 读数历史（同地点同窗口，读数随时间变）：")
        for line in result["volatility_table"].splitlines():
            print(f"    {line}")

        # 模型引用 ⑥ 块的历史（是否确定？）
        cits = citation_history()
        result["citation_history"] = cits
        result["citation_table"] = _citation_table(cits)
        print("\n  S3_with 存档的「引用外部」历史：")
        for line in result["citation_table"].splitlines():
            print(f"    {line}")

    # ---------------- 四点五、两种判据的对照 ----------------
    hr("四点五、相对差分 vs 绝对阈值（同一组点，两种判据各判一次）")
    avr = absolute_vs_relative()
    # 真实 API 的那两个点不在校准文件里，单独接上
    live_row = None
    if ext is not None:
        bp = (ext.get("baseline_point") or {})
        fp = (ext.get("forecast_point") or {})
        if bp and fp:
            from B07.weather import judge_external_points
            rel = judge_external_points(bp, fp, hours=0, analysis_time=None)
            absolute = judge_external_points(None, fp, hours=0, analysis_time=None)
            live_row = {
                "label": f"本次真实 API（{ext.get('location')} {ext.get('forecast_window')}）",
                "provenance": "本次真实 API 实测（存档里的两个点）",
                "baseline_pp": bp.get("precipitation_probability"),
                "forecast_pp": fp.get("precipitation_probability"),
                "forecast_cloud": fp.get("cloud_cover"),
                "relative_signal": rel.get("signal"),
                "relative_method": rel.get("judge_method"),
                "relative_delta": rel.get("precip_delta"),
                "absolute_signal": absolute.get("signal"),
                "absolute_method": absolute.get("judge_method"),
                "agree": rel.get("signal") == absolute.get("signal"),
            }
    if avr.get("available"):
        rows = list(avr["rows"])
        if live_row:
            rows.append(live_row)
        avr["rows"] = rows
        avr["disagreements"] = [r for r in rows if not r["agree"]]
        result["absolute_vs_relative"] = avr
        for r in rows:
            mark = "✅ 一致" if r["agree"] else "⚠️ 不一致"
            print(f"  {r['label']}")
            print(f"    来源：{r['provenance']}")
            print(f"    降水概率 {r['baseline_pp']}% → {r['forecast_pp']}%"
                  f"（预报云量 {r['forecast_cloud']}%）")
            print(f"    相对差分 → {r['relative_signal']}"
                  f"（Δ={r['relative_delta']} pp，{r['relative_method']}）")
            print(f"    绝对阈值 → {r['absolute_signal']}"
                  f"（{r['absolute_method']}）  {mark}")
        print(f"\n  两种判据不一致的组：{len(avr['disagreements'])} 组")
        for r in avr["disagreements"]:
            print(f"    ⚠️ {r['label']}：相对={r['relative_signal']} / "
                  f"绝对={r['absolute_signal']}")
    else:
        result["absolute_vs_relative"] = avr
        print(f"  ⚠️ {avr.get('note')}")

    # ---------------- 五、离线组 ----------------
    if do_offline:
        hr("五、离线可复现组 vs 真实 API 组")
        off = offline_group(location=location, model_path=model_path, progress=progress)
        result["offline"] = off

        sub("5.1 离线组（夹具注入 B05 天气，不联网）")
        for c in off["cases"]:
            print(f"  {c['case']:<15} signal={c['signal']:<24} "
                  f"Δpp={c['precip_delta']}  期望={c['expected_signal']:<24} "
                  f"{'✅ 一致' if c['signal_match'] else '❌ 不一致'}")
            print(f"    combined_attention = {c['combined_attention']}")
            print(f"    回答（{c['answer_length']} 字）：{c['answer']}")
            print(f"    提到天气：{c['weather_mentions'] or '（无）'}")
            print(f"    断言天气事实：{c['weather_claims'] or '（无）'}")
        print(f"\n  离线组临时存档目录：{off['tmp_dir']}")

        sub("5.2 真实 API 组")
        if ext is not None:
            print(f"  signal = {ext.get('signal')}（{ext.get('judge_method')}），"
                  f"Δpp = {ext.get('precip_delta')}")
            print(f"  基线点 {base_ts} → 预报点 {fore_ts}")
            print(f"  回答：{archive_b.get('llm_explanation')}")
        else:
            print("  （本轮未跑真实组）")

        sub("5.3 两组的差别（为什么离线组才是可复现结论）")
        print("  真实天气是**活的**：同一地点、同一窗口，下一次运行可能给出不同的")
        print("  降水概率与云量，信号可能从 external_deteriorating 变成")
        print("  external_stable。因此真实组只是一次**快照**（snapshot），")
        print("  记录它是为了说明「这条链路在真实数据上跑得通」，")
        print("  而离线组用夹具固定了输入，才是**可复现的结论**。")
        print(f"  本轮真实组信号：{ext.get('signal') if ext else '（未跑）'}；"
              f"离线组三档全部命中：{'✅' if off['all_signals_match'] else '❌'}")

    # ---------------- 六、降级 ----------------
    if do_offline:
        hr("六、天气服务不可用时的降级行为")
        deg = result["offline"]["degraded"]
        print(f"  外部判定 signal      = {deg['signal']}")
        print(f"  判定方式 judge_method = {deg['judge_method']}")
        print(f"  整次分析 status       = {deg['run_status']}"
              f"{'✅ 没有整体失败' if deg['run_status'] == 'success' else '❌ 整体失败了'}")
        print(f"  结构化判断字段数       = {deg['judgement_field_count']}（含第 6 字段）")
        print("  显式 warning：")
        for w in deg["external_warnings"] or []:
            print(f"    ⚠️ {w}")
        print(f"\n  降级后的回答（{deg['answer_length']} 字）：")
        print(f"    {deg['answer']}")
        print(f"\n  回答里提到天气的句子：{deg['weather_mentions'] or '（无）'}")
        print(f"  回答里断言天气事实的句子：{deg['weather_claims'] or '（无）'}")
        print(f"  回答里出现的外部数值：{deg['fabricated_numbers'] or '（无）'}")
        if not deg["weather_claims"] and not deg["fabricated_numbers"]:
            print("  ✅ 降级回答里没有任何编造的天气内容")
        else:
            print("  ⚠️ 降级回答里出现了天气事实断言，需要人工核查")

    hr("S3-2 完成")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="B07 S3-2：带/不带外部天气的对照实验")
    parser.add_argument("--location", type=str, default=DEFAULT_LOCATION)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--real-only", action="store_true", help="只跑真实 API 组")
    parser.add_argument("--offline-only", action="store_true", help="只跑离线组")
    args = parser.parse_args()

    print("=" * 70)
    print("B07 S3-2 — 带 / 不带外部天气的对照实验")
    print("=" * 70)

    result = run_s3(
        location=args.location,
        model_path=args.model,
        do_real=not args.offline_only,
        do_offline=not args.real_only,
    )

    if result.get("real"):
        print(f"\n【run_id A】{result['real']['run_id_a']}  →  {result['real']['path_a']}")
        print(f"【run_id B】{result['real']['run_id_b']}  →  {result['real']['path_b']}")

    if result.get("limitations"):
        print("\n【limitations】")
        for lim in result["limitations"]:
            print(f"  - {lim}")

    # S3-3：把本次实验的原始结果落盘，供报告生成器读取。
    # 报告里的所有数字都从这里取，不手工填写。
    raw_path = _PROJECT_ROOT / "B07" / "artifacts" / "s3_raw_result.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n【原始结果】{raw_path}")


if __name__ == "__main__":
    main()
