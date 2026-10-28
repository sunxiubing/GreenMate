"""B07 第三遍 · 视图一 | 状态总览（文档 p.58–59）

这个视图只回答一个问题：**当前番茄生长环境现在处于什么状态？**

它不是"系统拥有多少东西"的展示页 —— 原始数据、知识资料、工具日志
都不塞进来；它只把**这一次运行最重要的结果**集中呈现。

═══════════════════════════════════════════════════════════════════════
★ 生死线：只读存档，绝不重新计算
═══════════════════════════════════════════════════════════════════════
  · 视图一的**所有**数据只从 `B07/runs/<run_id>/run.json` 读取；
  · **绝对禁止**：调用 LLM、跑 B06 模型、请求天气、生成历史窗口、检索知识；
  · 渲染路径里不 import 任何会发网络请求或跑模型的东西；
  · 核心状态（当前环境 / 关注等级 / 近期趋势 / 下一窗口预测）
    全部读存档里**已经算好**的结构化判断，不在页面层重算。

为什么要这么死板：状态总览如果自己再调一次 LLM"请根据这些数据显示当前状态"，
就可能得到一个**和原始 run_id 不一样的新答案** —— 那一屏看到的东西
就不再是"那一次分析"，而是"现在重新问了一次"。本视图刻意不做这件事：
刷新页面，看到的永远是存档里那一次分析的结论。

用法：
  python -m B07.views overview run_082
  python -m B07.views overview --run-id run_082

视图二 | 状态演化（同一文件里，只读存档，绝不重新计算）：

  python -m B07.views evolution run_015
  python -m B07.views evolution --compare run_012 run_015

视图三 | 证据空间（同一文件里，只读存档，绝不重新计算）：

  python -m B07.views evidence run_082
  python -m B07.views evidence --run-id run_084

视图四 | 场景回放与对比（同一文件里，只读存档，绝不重新计算）：

  python -m B07.views compare run_012 run_015
  python -m B07.views compare run_012 run_023      # 只改"外部天气接入"的干净对照

视图四的**回放器**（唯一允许发起新运行的地方）在 B07/replay.py：

  python -m B07.replay S1 --dry-run

四视图统一检查（文档 p.69）：

  python -m B07.views check run_082


═══════════════════════════════════════════════════════════════════════
Limitations
═══════════════════════════════════════════════════════════════════════
  L1 视图一读取的是**已经落盘的那一次分析**，不是实时状态；
     重新运行会因 LLM 采样波动得到不同结果，所以视图刻意不做实时计算。
  L2 "证据完整度"是按台账 status 统计得到的**代理指标**，
     不等于"这份分析的质量评分"。
  L3 S1/S2/S3 的那批 run 早于证据层，没有台账，
     证据完整度对这些 run 不可计算（视图里明确标注，不假装）。
  L4 视图一 / 视图二 / 视图三（证据空间）/ 视图四（场景回放与对比）已实现。
  L5（视图二）读的是存档里那一次用的历史窗口，不是"当前最近 3 小时"；
     重新运行会因取到不同窗口得到不同曲线，所以刻意不做实时取数。
  L6（视图二）一次 run 只有一次模型判断和一个关注等级，档案没有时间序列，
     "模型由 NORMAL 变为 WATCH""系统提高了关注程度"这两类标记在单 run 内
     做不出来 —— 页面里显式写出来，只能靠多 run 对比（视图四）看到。
  L7（视图二）外部条件是单点判定，不是时间序列，只能作覆盖整个窗口的整体标注。
  L8（视图二）曲线是 6 个采样点的连线，未做平滑/插值；trend_shape 是规则判定的
     分类（规则写在页面上），不是模型输出。
  L9（视图二）S1 与 S2 的窗口不在同一天（10-21 vs 10-20），
     对比用的是窗口内相对时间。
  L10（视图三）读的是**存档里那一次**的证据台账，不是实时重新取数；
      刷新页面不会重新调 LLM / 不重跑 B06 / 不再取一次天气。
  L11（视图三）「证据可用度」按 status == "ok" 统计，是**证据链可审计性**的
      代理指标，不等于"分析质量评分"；counts.adopted_entries 是"被采用为
      证据条目的数"，**不是**有效条数（降级场景 adopted=4 但只有 3 条有效）。
  L12（视图三）B04 知识证据的取得时间是**知识检索时刻**，可能与分析时刻
      差好几天；界面显示真实时间，不做对齐。
  L13（视图三）天气 caveats 只覆盖本次实测情形（夜间、solar_radiation 恒 0、
      高温分支未覆盖），不是通用天气结论。
  L14（视图三）外部天气走真实 API 的真实时间轴，内部 B02 是模拟时间轴，
      两者不可时刻级对齐，天气只作方向参考。
  L15（视图三）S1/S2/S3 的 24 个 run 早于证据层，没有台账，
      证据空间对这些 run 不可用（明确标注，不假装有证据）。
  L16（视图四）回放不是可复现的：真实天气 API 走真实世界时间，
      重放同一场景会得到不同的外部条件；LLM 采样本身也有波动。
      默认的离线构造天气可复现，但那是构造用例，不是实时天气。
  L17（视图四）回放会**真的调用 LLM 并落盘新 run**，消耗额度；
      对比与归因部分完全离线。
  L18（视图四）归因是**基于存档字段的规则推导**，不是因果推断；
      "输入变了"不等于"输入变了一定导致了判断变"，
      只能说"本次存档里差异出现在这一层"。
  L19（视图四）R4（条件没变结果却变）说明系统存在**输出不稳定**，
      这是 S3 已观察到的采样波动现象在产品层的复现，
      不应被解读为判断错误。
  L20（视图四）文档指定的 S2↔S3 在本档案中**不是单一变量对照**
      （历史窗口与稳定判定同时变了）；真正干净的对照是 S1↔S3。
  L21（视图四）S1/S2/S3 的 26 个 run 没有证据台账，
      S1↔S2 的"证据层"只能判为**无法比较**，不能判为"证据相同"。
  L22（视图四）统一检查只覆盖"四个视图讲的是同一次分析"这一件事，
      **不检查判断本身对不对**。
  L23（视图四）三组对比各只取了 1 个代表 run；S4 有 19 个同构 run、
      S5 有 44 个（内含 3 种状态），结论来自代表样本，
      不等于对全部带台账 run 的普查结论。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":                      # Windows 控制台按 codepage 解码
    try:                                  # 不切 codepage 就一定乱码
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:                     # pragma: no cover
        pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

RUNS_DIR = _PROJECT_ROOT / "B07" / "runs"

#: 没有台账时的固定说明（S1/S2/S3 早于 S4 的证据层）
NO_LEDGER_NOTE = ("本次运行没有证据台账（该 run 早于 S4 的证据层），"
                   "证据完整度不可计算")

#: 视图三默认展示的 run（分歧齐全的一组：run_082）
DEFAULT_EVIDENCE_RUN = "run_082"

#: 视图三专用：没有台账时的说明（视图一的 NO_LEDGER_NOTE 不动）
EVIDENCE_NO_LEDGER_NOTE = ("本次运行没有证据台账（该 run 早于 S4 的证据层），"
                           "证据空间对这批 run 不可用")

#: 关注等级的取值集合**不写死在这里** —— 从 B07.judgement 读常量
ATTENTION_EXPLANATIONS = None            # 见 _attention_explanations()


def _attention_explanations() -> dict:
    """关注等级取值 → 人话说明。

    取值集合来自 `B07.judgement` 的常量（COMBINED_*），
    不在这里另写一份字符串 —— 免得两处取值各说各话。
    """
    from B07.judgement import (
        COMBINED_CONFLICT_UNRESOLVED,
        COMBINED_INTERNAL_ONLY,
        COMBINED_WATCH_EXTERNAL,
        COMBINED_WATCH_INTERNAL,
    )
    return {
        COMBINED_CONFLICT_UNRESOLVED: "证据方向不一致，保留分歧未做合并",
        COMBINED_WATCH_INTERNAL: "内部证据支持关注",
        COMBINED_WATCH_EXTERNAL: "外部条件支持关注",
        COMBINED_INTERNAL_ONLY: "仅内部证据支持",
    }


# --------------------------------------------------------------------------
# 数据层（纯函数：只做 json.loads + 取字段）
# --------------------------------------------------------------------------

def load_run(run_id: str) -> dict:
    """读一个 run 存档。只做 json.loads，不做任何计算。"""
    path = RUNS_DIR / run_id / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到 run 存档：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _get(obj, *keys, default=None):
    """按 key 逐层取，缺一层就返回 default（不抛）。"""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


def _evidence_completeness(bundle: dict) -> dict:
    """证据完整度：从 evidence_ledger.entries 统计（不重算任何判断）。

    没有台账的 run（S1/S2/S3）→ 明确说"不可计算"，不假装完整、不报错。
    """
    ledger = bundle.get("evidence_ledger") or {}
    entries = list(ledger.get("entries") or [])
    if not entries:
        return {
            "has_ledger": False,
            "total": 0,
            "ok": 0,
            "not_ok": 0,
            "missing": [],
            "summary": NO_LEDGER_NOTE,
        }

    missing = []
    ok = 0
    for e in entries:
        status = e.get("status")
        if status == "ok":
            ok += 1
            continue
        missing.append({
            "evidence_id": e.get("evidence_id"),
            "source_type": e.get("source_type"),
            "source": e.get("source"),
            "status": status,
            "error": e.get("error"),
            "direction": e.get("direction"),
        })

    total = len(entries)
    if not missing:
        summary = (f"{ok}/{total} 条证据全部可用（status=ok），"
                   f"本次分析没有证据缺口")
    else:
        names = "、".join(
            f"{m['evidence_id']}（{m['source_type']}，status={m['status']}）"
            for m in missing)
        summary = (f"{ok}/{total} 条证据可用，{len(missing)} 条不可用：{names}")
    return {
        "has_ledger": True,
        "total": total,
        "ok": ok,
        "not_ok": len(missing),
        "missing": missing,
        "summary": summary,
    }


def _four_questions(data: dict) -> list[dict]:
    """用**存档里已有的字段**回答那四个问题（规则化拼装，不调 LLM）。"""
    und = data["understood"]
    ev = data["evidence"]
    verdict = und["state_verdict"] or "（存档未记录状态判定）"
    attention = und["attention"]
    trend = und["recent_trend"]

    # 1) 现在整体怎样？
    if attention.get("value"):
        q1 = (f"{verdict}；关注等级 {attention['value']}"
              f"（{attention.get('explanation') or '—'}）")
    else:
        q1 = f"{verdict}；该 run 没有 combined_attention 字段，关注等级未知"

    # 2) 最近有没有值得注意的变化？
    failed = trend.get("failed_criteria") or []
    if failed:
        q2 = (f"有。{trend.get('stability_verdict') or '状态有变化'}："
              + "；".join(failed))
    elif trend.get("stability_verdict"):
        q2 = f"没有触发任何不稳定判据（{trend['stability_verdict']}）"
    else:
        q2 = "（存档未记录近期趋势）"

    # 3) 下一窗口需要关注吗？
    pa = und["predicted_attention"]
    if pa.get("value"):
        q3 = (f"{pa['value']}"
              + (f"（model_version={pa['model_version']}）"
                 if pa.get("model_version") else "")
              + (f"，置信度 {pa['confidence']}"
                 if pa.get("confidence") is not None else ""))
        if attention.get("value"):
            q3 += f"；合并关注等级 {attention['value']}"
    else:
        q3 = "（存档未记录下一窗口预测）"

    # 4) 当前证据完整吗？
    q4 = ev["summary"]
    if ev["has_ledger"] and ev["missing"]:
        for m in ev["missing"]:
            q4 += (f"；{m['evidence_id']} 不可用："
                   f"{m.get('error') or 'status=' + str(m.get('status'))}")
    return [
        {"q": "现在整体怎样？", "a": q1},
        {"q": "最近有没有值得注意的变化？", "a": q2},
        {"q": "下一窗口需要关注吗？", "a": q3},
        {"q": "当前证据完整吗？", "a": q4},
    ]


def view_data(run_id: str) -> dict:
    """装配视图一要用的全部数据（一次装配好，纯读存档）。"""
    bundle = load_run(run_id)
    sj = bundle.get("structured_judgement") or {}
    ca = bundle.get("combined_attention") or {}
    llm_meta = bundle.get("llm_meta") or {}
    mo = bundle.get("model_output") or {}
    trend = sj.get("recent_trend") or {}

    # ---- 关注等级：取值来自 judgement 常量，说明取自存档里的 reason ----
    attention = {"value": None, "explanation": None, "reason": None,
                 "internal_predicted_attention": None,
                 "external_signal": None, "has_conflict": False}
    if ca:
        vocab = _attention_explanations()
        attention = {
            "value": ca.get("value"),
            "explanation": vocab.get(ca.get("value")),
            "reason": ca.get("reason"),
            "internal_predicted_attention": ca.get(
                "internal_predicted_attention"),
            "external_signal": ca.get("external_signal"),
            "has_conflict": bool(ca.get("has_conflict")),
        }

    # ---- 近期趋势：判据未通过的项如实列出 ----
    failed = []
    crit = trend.get("criteria") or {}
    for name, c in crit.items():
        if isinstance(c, dict) and c.get("passed") is False:
            failed.append(f"{name}（实测 {c.get('value')} / 阈值 "
                          f"{c.get('threshold')}，{c.get('note')}）")

    data = {
        "run_id": run_id,
        "run_path": f"B07/runs/{run_id}/run.json",
        "status": bundle.get("status"),
        "scenario": bundle.get("scenario"),
        "created_at": bundle.get("created_at"),
        "analysis_time": bundle.get("analysis_time"),
        "location": bundle.get("location"),
        "fingerprint": {
            "model": llm_meta.get("model"),
            "system_prompt_sha256": llm_meta.get("system_prompt_sha256"),
            "question_sha256": llm_meta.get("question_sha256"),
        },
        # ---- 【一】原始数值：这次分析真正使用的当前环境 ----
        "raw": {
            "timestamp": _get(bundle, "current_state", "timestamp"),
            "temperature": _get(bundle, "current_state", "temperature"),
            "humidity": _get(bundle, "current_state", "humidity"),
            "lux": _get(bundle, "current_state", "lux"),
            "source_type": _get(bundle, "current_state", "source_type"),
            "sim_scenario": _get(bundle, "current_state", "sim_scenario"),
        },
        # ---- 【二】系统理解后的状态 ----
        "understood": {
            "current_environment": sj.get("current_environment") or {},
            "state_verdict": trend.get("stability_verdict"),
            "attention": attention,
            "recent_trend": {
                "stability_verdict": trend.get("stability_verdict"),
                "conclusion": trend.get("conclusion"),
                "window_span": trend.get("window_span"),
                "window_start": trend.get("window_start"),
                "window_end": trend.get("window_end"),
                "failed_criteria": failed,
            },
            "predicted_attention": {
                "value": sj.get("predicted_attention")
                or mo.get("predicted_attention"),
                "model_version": mo.get("model_version")
                or _get(bundle, "model_meta", "model_version"),
                "confidence": mo.get("confidence"),
            },
        },
        # ---- 证据完整度 ----
        "evidence": _evidence_completeness(bundle),
        # ---- 【三】已保存的 LLM 解释（原样，不重新生成） ----
        "llm_explanation": bundle.get("llm_explanation") or "",
        # ---- 【四】四个问题 ----
        "questions": [],
    }
    data["questions"] = _four_questions(data)
    data["data_sha256"] = _data_sha256(data)
    return data


def _data_sha256(data: dict) -> str:
    """对装配好的数据求 sha256（确定性，用于"刷新不变"的自证）。"""
    payload = {k: v for k, v in data.items() if k != "data_sha256"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 终端渲染
# --------------------------------------------------------------------------

WIDTH = 78
SEP = "=" * WIDTH
SEP2 = "-" * WIDTH


def _fmt_num(v):
    return "—" if v is None else str(v)


def render_overview(run_id: str) -> str:
    """渲染视图一（终端版）。只读存档，不重算。"""
    d = view_data(run_id)
    fp = d["fingerprint"]
    raw = d["raw"]
    und = d["understood"]
    ce = und["current_environment"]
    units = {"temperature": "℃", "humidity": "%", "lux": "lux"}
    if isinstance(ce.get("unit"), dict):
        units = {k: ce["unit"].get(k, fb)
                 for k, fb in (("temperature", "℃"), ("humidity", "%"),
                               ("lux", "lux"))}

    L: list[str] = []
    L.append(SEP)
    L.append(" B07 · 视图一 | 状态总览")
    L.append(f" 本次分析 = {d['run_id']}   created_at={d['created_at']}  "
             f"analysis_time={d['analysis_time']}")
    L.append(f" model={fp['model']}   提示词指纹={fp['system_prompt_sha256']}  "
             f"问题指纹={fp['question_sha256']}")
    L.append(f" 数据 sha256（本次渲染）= {d['data_sha256'][:12]}")
    L.append(SEP2 + " 【一】原始数值 —— 这次分析真正使用的当前环境")
    L.append(f"     采样时间   : {raw['timestamp']}")
    L.append(f"     温度       : {raw['temperature']} {units['temperature']}")
    L.append(f"     湿度       : {raw['humidity']} {units['humidity']}")
    L.append(f"     光照       : {raw['lux']} {units['lux']}")
    L.append(f"     source_type: {raw['source_type']}")
    L.append(f"     模拟场景标签: {raw['sim_scenario']}")
    L.append("     ⚠ 这一块只是数值，本身不构成判断")

    L.append(SEP2)
    L.append("    【二】系统理解后的状态 —— 基于已有证据形成的判断")
    verdict = und["state_verdict"] or "（存档未记录状态判定）"
    L.append(f"       当前环境状态 ：{verdict}")
    L.append(f"           数值快照 ：温度 {ce.get('temperature')} "
             f"{units['temperature']} / 湿度 {ce.get('humidity')} "
             f"{units['humidity']} / 光照 {ce.get('lux')} {units['lux']}"
             f"（采样 {ce.get('sampled_at')}）")
    att = und["attention"]
    if att.get("value"):
        L.append(f"       关注等级     ：{att['value']}"
                 f"（{att.get('explanation') or '—'}）")
        if att.get("reason"):
            L.append(f"           判定理由 ：{att['reason']}")
        L.append(f"           内部预测 {att.get('internal_predicted_attention')} / "
                 f"外部信号 {att.get('external_signal')} / "
                 f"有分歧 {att.get('has_conflict')}")
    else:
        L.append("       关注等级     ：（该 run 没有 combined_attention 字段）")
    tr = und["recent_trend"]
    L.append(f"       近期趋势     ：{tr.get('stability_verdict') or '—'}"
             f"（窗口 {tr.get('window_span') or '—'}："
             f"{tr.get('window_start') or '—'} → {tr.get('window_end') or '—'}）")
    if tr.get("conclusion"):
        L.append(f"           结论     ：{tr['conclusion']}")
    if tr.get("failed_criteria"):
        L.append("           触发的判据（未通过）：")
        for f in tr["failed_criteria"]:
            L.append(f"             · {f}")
    pa = und["predicted_attention"]
    mv = f"（model_version={pa['model_version']}）" if pa.get("model_version") \
        else ""
    L.append(f"       下一窗口预测 ：{pa.get('value') or '—'}{mv}")
    ev = d["evidence"]
    L.append(f"       证据完整度   ：{ev['summary']}")
    if ev["has_ledger"]:
        if not ev["missing"]:
            L.append("           缺失清单 ：（无）")
        else:
            L.append("           缺失清单 ：")
            for m in ev["missing"]:
                L.append(f"             · {m['evidence_id']}（{m['source_type']}，"
                         f"source={m['source']}）status={m['status']}")
                L.append(f"               不可用的原因：{m.get('error') or '（存档未记录 error 字段）'}")
    else:
        L.append(f"           缺失清单 ：{NO_LEDGER_NOTE}")

    L.append(SEP2)
    L.append("    【三】这次分析的 LLM 解释（已保存，未重新生成）")
    for line in (d["llm_explanation"] or "").splitlines() or ["（空）"]:
        L.append(f"       {line}")

    L.append(SEP2)
    L.append("    【四】四个问题（做完就该能答上来）")
    for i, item in enumerate(d["questions"], 1):
        L.append(f"       {item['q']:<16}→ {item['a']}")
    L.append(SEP)
    return "\n".join(L)


# ==========================================================================
# 视图二 | 状态演化（文档 p.60–62）
#
# ★ 只读存档，绝不重新计算：
#   · 曲线的数据源只有一个 —— run.json 里的 history 列表；
#   · history_window 本身**不含序列**（只有边界 + 判据结果），
#     它只用来核对"这段曲线就是那次分析真正用的那一段"；
#   · 过程量（delta / range / max_step / monotone / trend_shape）
#     **只从 history 序列算**，不许读 structured_judgement 的结论；
#   · 判据的数值/阈值一律读存档里的 criteria，不重新实现判据逻辑；
#   · 不重新取数、不重新生成窗口、不重新跑判据、不调 LLM、不跑 B06、
#     不取天气、不查 B02。
# ==========================================================================

#: 过程量关注的三个物理量（键 / 中文名 / 单位）
EV_METRICS = (("temperature", "温度", "℃"),
              ("humidity", "湿度", "%"),
              ("lux", "光照", "lux"))

#: 趋势形状"稳定"判定用的相对波动幅度上限：
#: 相对波动幅度 = (max - min) / max(|值|)
STABLE_REL_SPAN_MAX = 0.05

#: 趋势形状的全部取值
TREND_SHAPES = ("稳定", "持续上升", "持续下降", "短时波动后恢复", "波动", "其他")

#: 判据越界"发生在哪一段"的定位规则（名字来自 stability.py 的判据名）
CRITERION_SEGMENT_RULES = {
    "温度最大波动": "temp_range",
    "湿度首尾总变化": "humi_total",
    "湿度单步最大变化": "humi_step",
    "光照单步最大跳变": "lux_step",
    "湿度无持续单向趋势": "humi_monotone",
}


# --------------------------------------------------------------------------
# 纯计算：序列 → 过程量（★ 不读 structured_judgement，不读任何结论）
# --------------------------------------------------------------------------

def _diffs(values: list[float]) -> list[float]:
    return [values[i + 1] - values[i] for i in range(len(values) - 1)]


def _sparkline(values: list[float]) -> str:
    """方块字符 sparkline（不用 ASCII 折线），按本序列自己的 min/max 归一化。"""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return "▁" * len(values)
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        idx = int(round((v - lo) / (hi - lo) * (len(blocks) - 1)))
        out.append(blocks[max(0, min(len(blocks) - 1, idx))])
    return "".join(out)


def _trend_shape(values: list[float]) -> tuple[str, str]:
    """判定趋势形状 —— **只看序列本身**，返回 (形状, 判定依据)。

    规则（写在这里，页面上也会原样写出来 —— 免得读者以为这是模型输出）：
      1. 全程单调不降（相邻差值 ≥ 0）且至少一次严格上升 → 持续上升
      2. 全程单调不升（相邻差值 ≤ 0）且至少一次严格下降 → 持续下降
      3. 相对波动幅度 = (max-min)/max(|值|) ≤ 5%          → 稳定
      4. 存在内部极值（中间点高于/低于首尾）且 |末点-首点| ≤ 波动幅度/2
                                                          → 短时波动后恢复
      5. 其余                                          → 波动
      6. 点数 < 2                                       → 其他
    """
    n = len(values)
    if n < 2:
        return "其他", "点数不足（<2），无法判定趋势"
    d = _diffs(values)
    up = all(x >= 0 for x in d)
    down = all(x <= 0 for x in d)
    strict_up = any(x > 0 for x in d)
    strict_down = any(x < 0 for x in d)
    lo, hi = min(values), max(values)
    span = hi - lo
    scale = max(abs(v) for v in values)
    rel = (span / scale) if scale else 0.0
    if up and strict_up:
        return "持续上升", (f"相邻差值全部 ≥ 0（全程单调不降），"
                            f"相对波动幅度 {rel:.2%}")
    if down and strict_down:
        return "持续下降", (f"相邻差值全部 ≤ 0（全程单调不升），"
                            f"相对波动幅度 {rel:.2%}")
    if rel <= STABLE_REL_SPAN_MAX:
        return "稳定", (f"相对波动幅度 {rel:.2%} ≤ {STABLE_REL_SPAN_MAX:.0%}"
                        f"（(max-min)/max|值|）")
    peak = any(values[i] > values[0] and values[i] > values[-1]
               for i in range(1, n - 1))
    valley = any(values[i] < values[0] and values[i] < values[-1]
                 for i in range(1, n - 1))
    if (peak or valley) and abs(values[-1] - values[0]) <= span / 2:
        kind = "先冲高再回落" if peak else "先下探再回升"
        return "短时波动后恢复", (
            f"{kind}，末点回到首点附近（|末-首|="
            f"{abs(values[-1] - values[0]):.2f} ≤ 波动幅度/2={span / 2:.2f}）")
    return "波动", (f"非单调、相对波动幅度 {rel:.2%} > "
                    f"{STABLE_REL_SPAN_MAX:.0%}，且末点未回到首点附近")


def _monotone_run_start(values: list[float], direction: str):
    """窗口末尾那段单调段的**起始点索引**（含）；不存在则 None。

    direction: "up" / "down"。
    """
    n = len(values)
    if n < 2:
        return None
    d = _diffs(values)
    i = len(d) - 1
    while i >= 0 and (d[i] >= 0 if direction == "up" else d[i] <= 0):
        i -= 1
    start = i + 1
    if start >= n - 1:            # 末尾没有一段（≥2 个点）的同向变化
        return None
    return start


def compute_process_metrics(history: list[dict]) -> dict:
    """★ 过程量 —— **只从 history 序列算**。

    这个函数**不 import、不读取** structured_judgement / combined_attention /
    任何结论字段；它只看 history 里的采样点。
    """
    out = {
        "point_count": len(history or []),
        "metrics": {},
    }
    for key, label, unit in EV_METRICS:
        raw = [p.get(key) for p in (history or [])]
        if any(v is None or isinstance(v, bool) or not isinstance(v, (int, float))
               for v in raw):
            out["metrics"][key] = {
                "key": key, "label": label, "unit": unit,
                "available": False,
                "note": f"history 序列里的 {label} 有缺失，无法计算过程量",
            }
            continue
        values = [float(v) for v in raw]
        d = _diffs(values)
        shape, basis = _trend_shape(values)
        up = all(x >= 0 for x in d)
        down = all(x <= 0 for x in d)
        abs_steps = [abs(x) for x in d]
        max_step = max(abs_steps) if abs_steps else 0.0
        max_step_idx = abs_steps.index(max_step) if abs_steps else None
        tail_up = _monotone_run_start(values, "up")
        tail_down = _monotone_run_start(values, "down")
        out["metrics"][key] = {
            "key": key, "label": label, "unit": unit,
            "available": True,
            "start_value": values[0],
            "end_value": values[-1],
            "delta": values[-1] - values[0],
            "min": min(values),
            "max": max(values),
            "range_width": max(values) - min(values),
            "max_step": max_step,
            "max_step_index": max_step_idx,
            "monotone_up": up,
            "monotone_down": down,
            "trend_shape": shape,
            "shape_basis": basis,
            "sparkline": _sparkline(values),
            "tail_up_start": tail_up,
            "tail_down_start": tail_down,
        }
    return out


# --------------------------------------------------------------------------
# 序列 → 逐点读数（带变化方向箭头）
# --------------------------------------------------------------------------

def _arrow(delta) -> str:
    if delta is None:
        return "—"
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def _parse_ts(ts: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _build_points(history: list[dict]) -> list[dict]:
    """逐点读数：时刻 / 窗口内相对分钟 / 三个物理量 + 相邻变化方向箭头。"""
    pts = []
    t0 = None
    for i, p in enumerate(history or []):
        ts = p.get("timestamp")
        dt = _parse_ts(ts)
        rel = None
        if dt is not None:
            if t0 is None:
                t0 = dt
            rel = int(round((dt - t0).total_seconds() / 60.0))
        prev = history[i - 1] if i > 0 else None
        rec = {
            "index": i,
            "timestamp": ts,
            "relative_minute": rel,
            "temperature": p.get("temperature"),
            "humidity": p.get("humidity"),
            "lux": p.get("lux"),
        }
        for key in ("temperature", "humidity", "lux"):
            if prev is None:
                rec[f"arrow_{key}"] = "—"
            else:
                a, b = prev.get(key), p.get(key)
                if not isinstance(a, (int, float)) or \
                        not isinstance(b, (int, float)):
                    rec[f"arrow_{key}"] = "—"
                else:
                    rec[f"arrow_{key}"] = _arrow(b - a)
        pts.append(rec)
    return pts


# --------------------------------------------------------------------------
# 时间线标记（第三步）
# --------------------------------------------------------------------------

def _breach_segment(name: str, points: list[dict],
                    metrics: dict) -> str:
    """给"越界发生在窗口的哪一段"一个**来自序列**的定位。"""
    n = len(points)
    m = metrics.get("metrics", {})

    def vals(key):
        mt = m.get(key) or {}
        if not mt.get("available"):
            return []
        return [p.get(key) for p in points]

    rule = CRITERION_SEGMENT_RULES.get(name)
    if rule == "temp_range":
        t = vals("temperature")
        if not t:
            return f"整个窗口（第1~{n}点）"
        i_min = t.index(min(t))
        i_max = t.index(max(t))
        lo_i, hi_i = sorted((i_min, i_max))
        return (f"第{lo_i + 1}~{hi_i + 1}点（{t[lo_i]}℃ → {t[hi_i]}℃，"
                f"跨 {hi_i - lo_i} 个采样间隔）")
    if rule == "humi_total":
        h = vals("humidity")
        if not h:
            return f"整个窗口（第1~{n}点）"
        return (f"第1点（首 {h[0]}%）→ 第{n}点（尾 {h[-1]}%），整段累计")
    if rule in ("humi_step", "lux_step"):
        key = "humidity" if rule == "humi_step" else "lux"
        unit = "%" if key == "humidity" else " lux"
        v = vals(key)
        if len(v) < 2:
            return f"整个窗口（第1~{n}点）"
        steps = [abs(v[i + 1] - v[i]) for i in range(len(v) - 1)]
        k = steps.index(max(steps))
        return (f"第{k + 1}→第{k + 2}点之间（{v[k]}{unit} → "
                f"{v[k + 1]}{unit}，跳变 {max(steps):.1f}{unit}）")
    if rule == "humi_monotone":
        h = vals("humidity")
        if len(h) < 2:
            return f"整个窗口（第1~{n}点）"
        if all(h[i + 1] <= h[i] for i in range(len(h) - 1)):
            start = _monotone_run_start(h, "down")
            seg = (f"全程（第1~{n}点）单调下降")
            if start is not None and start > 0:
                seg += f"，该单调段起点在第{start + 1}点"
            return seg
        if all(h[i + 1] >= h[i] for i in range(len(h) - 1)):
            start = _monotone_run_start(h, "up")
            seg = (f"全程（第1~{n}点）单调上升")
            if start is not None and start > 0:
                seg += f"，该单调段起点在第{start + 1}点"
            return seg
        return f"整个窗口（第1~{n}点）"
    return f"整个窗口（第1~{n}点）"


def _build_timeline(points: list[dict], metrics: dict,
                    bundle: dict) -> dict:
    """时间线标记。

    每条"能做的"标记都必须带着**来自序列的时刻**；
    做不了的必须**显式写出来**，不许留空、不许假装有。
    """
    markers: list[dict] = []
    n = len(points)
    analysis_time = bundle.get("analysis_time")

    # 1) 窗口开始
    if n:
        p0 = points[0]
        markers.append({
            "at": p0["timestamp"],
            "point_index": 0,
            "scope": "point",
            "kind": "窗口开始",
            "title": "窗口开始（该次分析所用历史窗口的起点）",
            "text": (f"温度 {p0['temperature']}℃ / 湿度 {p0['humidity']}% / "
                     f"光照 {p0['lux']} lux"),
            "source": "history[0]（history_window.start 核对一致）",
        })
        pn = points[-1]
        markers.append({
            "at": pn["timestamp"],
            "point_index": n - 1,
            "scope": "point",
            "kind": "窗口结束 = 本次分析时刻",
            "title": "窗口结束 = 本次分析时刻（当前状态就是这一刻）",
            "text": (f"温度 {pn['temperature']}℃ / 湿度 {pn['humidity']}% / "
                     f"光照 {pn['lux']} lux"
                     + (f"；analysis_time={analysis_time}"
                        if analysis_time else "")),
            "source": "history[-1]（history_window.end 核对一致）",
        })

    # 2) 单调趋势的起点（温度 / 湿度）—— 起点时刻来自序列
    for key, label, unit in EV_METRICS:
        if key == "lux":
            continue                      # 规格要求温度/湿度；光照在过程量表里
        mt = (metrics.get("metrics") or {}).get(key) or {}
        if not mt.get("available"):
            continue
        for direction, start, word in (("up", mt.get("tail_up_start"), "持续上升"),
                                       ("down", mt.get("tail_down_start"),
                                        "持续下降")):
            if start is None or start >= n - 1:
                continue
            covers = (start == 0)
            p = points[start]
            markers.append({
                "at": p["timestamp"],
                "point_index": start,
                "scope": "point",
                "kind": f"{label}{word}的起点",
                "title": f"{label}从这一刻起{word}",
                "text": (f"起点 {p[key]}{unit}（第{start + 1}点）"
                         + ("，该单调段覆盖整个窗口"
                            if covers else
                            f"，一直持续到窗口末点（第{n}点）")),
                "source": f"history 序列第{start + 1}~{n}点相邻差值同向",
            })

    # 3) 判据越界标记：数值/阈值读 criteria，位置从序列算
    hw = bundle.get("history_window") or {}
    stability = hw.get("stability") or {}
    criteria = stability.get("criteria") or []
    if isinstance(criteria, dict):        # 兼容另一种形状
        criteria = [{"name": k, **v} for k, v in criteria.items()]
    for c in criteria:
        if c.get("passed") is not False:
            continue
        name = c.get("name")
        value = c.get("value")
        threshold = c.get("threshold")
        excess = c.get("excess")
        seg = _breach_segment(name, points, metrics)
        detail = (f"实测 {value} / 阈值 {threshold}"
                  + (f"（超出 {excess}）" if excess is not None else ""))
        markers.append({
            "at": "整个窗口",
            "point_index": None,
            "scope": "window",
            "kind": f"判据越界：{name}",
            "title": f"{name} 越界 —— 越界发生在窗口的这一段",
            "text": f"{detail}；越界位置：{seg}",
            "source": ("阈值与实测值读存档 history_window.stability.criteria；"
                       "越界位置由 history 序列算出"),
        })

    # 4) 外部条件 —— 单点判定，只能作为"覆盖整个窗口"的整体标注
    ext = bundle.get("external_conditions")
    if ext:
        basis = str(ext.get("basis") or "").strip()
        basis_head = basis.split("；")[0] if basis else ""
        markers.append({
            "at": "整个窗口",
            "point_index": None,
            "scope": "window",
            "kind": "外部条件（覆盖整个窗口，不是某一个时刻）",
            "title": f"外部条件：{ext.get('signal')}",
            "text": (f"signal={ext.get('signal')}；判定方式="
                     f"{ext.get('judge_method')}；依据：{basis_head}"
                     f"　※ 该标注覆盖整个窗口，不是时点"),
            "source": "run.json 的 external_conditions（单点判定）",
        })

    # ---- 做不了的：必须显式写出来 ----
    not_available = [
        {
            "kind": "模型由 NORMAL 变为 WATCH",
            "title": "无法标注『模型由 NORMAL 变为 WATCH』",
            "reason": ("本 run 档案不含模型输出的时间序列，无法标注"
                       "『模型由 NORMAL 变为 WATCH』。一次分析只有一次模型判断；"
                       "模型/关注等级随时间的变化需要在多个 run 之间对比才能看到"
                       "（见视图四 · 场景回放对比）。"),
        },
        {
            "kind": "系统提高了关注程度",
            "title": "无法标注『这里系统提高了关注程度』",
            "reason": ("本 run 档案只有一个 combined_attention 值，"
                       "没有关注等级的时间序列，无法标注关注程度的升降。"
                       "关注等级随时间的变化需要在多个 run 之间对比才能看到"
                       "（见视图四 · 场景回放对比）。"),
        },
    ]
    return {"markers": markers, "not_available": not_available}


# --------------------------------------------------------------------------
# 视图二数据装配
# --------------------------------------------------------------------------

def _trim_window_selection(sel) -> dict:
    if not isinstance(sel, dict):
        return {}
    selected = sel.get("selected") or {}
    out = {
        "scenario": sel.get("scenario"),
        "anchor_policy": sel.get("anchor_policy"),
        "reason": sel.get("reason"),
        "candidate_count": sel.get("candidate_count"),
        "daytime_count": sel.get("daytime_count"),
        "rejected_count": sel.get("rejected_count"),
        "pool": sel.get("pool"),
        "reference": sel.get("reference"),
        "selected": {
            "start_time": selected.get("start_time"),
            "end_time": selected.get("end_time"),
            "conclusion": selected.get("conclusion"),
        } if selected else {},
    }
    return out


def evolution_data_from_bundle(bundle: dict) -> dict:
    """从 run 存档装配视图二数据（纯读存档，零重算）。"""
    run_id = bundle.get("run_id")
    history = bundle.get("history") or []
    hw = bundle.get("history_window") or {}
    llm_meta = bundle.get("llm_meta") or {}

    points = _build_points(history)
    metrics = compute_process_metrics(history)      # ★ 只看序列

    # 窗口核对：history_window 不含序列，只用来核对曲线就是那次分析用的那段
    alignment = {"checked": False, "ok": None, "note": ""}
    if hw.get("start") and points:
        ok = (points[0]["timestamp"] == hw.get("start")
              and points[-1]["timestamp"] == hw.get("end"))
        alignment = {
            "checked": True,
            "ok": bool(ok),
            "note": ("history 序列的首/末时刻与 history_window.start/end "
                     + ("一致" if ok else "**不一致** —— 请检查存档")),
        }
    elif not points:
        alignment = {"checked": False, "ok": False,
                     "note": "该 run 存档没有 history 序列，无法画曲线"}

    timeline = _build_timeline(points, metrics, bundle)

    data = {
        "run_id": run_id,
        "scenario": bundle.get("scenario"),
        "status": bundle.get("status"),
        "created_at": bundle.get("created_at"),
        "analysis_time": bundle.get("analysis_time"),
        "curve_source": (f"B07/runs/{run_id}/run.json · history 字段"
                         f"（{len(points)} 个采样点）"),
        "window": {
            "start": hw.get("start"),
            "end": hw.get("end"),
            "count": hw.get("count"),
            "stability_passed": (hw.get("stability") or {}).get("passed"),
            "stability_conclusion": (hw.get("stability") or {}).get("conclusion"),
        },
        "alignment": alignment,
        "points": points,
        "process": metrics,
        "timeline": timeline,
        "window_selection": _trim_window_selection(bundle.get("window_selection")),
        "external_conditions": bundle.get("external_conditions"),
        "fingerprint": {
            "model": llm_meta.get("model"),
            "system_prompt_sha256": llm_meta.get("system_prompt_sha256"),
            "question_sha256": llm_meta.get("question_sha256"),
        },
        "stable_rel_span_max": STABLE_REL_SPAN_MAX,
        "trend_shapes": list(TREND_SHAPES),
    }
    data["data_sha256"] = _data_sha256(data)
    return data


def view_evolution_data(run_id: str) -> dict:
    """视图二的全部数据（视图一与视图二共用同一个 load_run）。"""
    return evolution_data_from_bundle(load_run(run_id))


# --------------------------------------------------------------------------
# 同源对照：找出用了同一段历史窗口的其他 run
# --------------------------------------------------------------------------

def find_same_window_runs(run_id: str) -> list[dict]:
    """找出与该 run 使用**同一段历史窗口**的其他 run（同源对照用）。"""
    ref = load_run(run_id)
    hw = ref.get("history_window") or {}
    start, end = hw.get("start"), hw.get("end")
    if not start or not end:
        return []
    out = []
    for p in sorted(RUNS_DIR.glob("*/run.json")):
        rid = p.parent.name
        if rid == run_id:
            continue
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        other = b.get("history_window") or {}
        if other.get("start") == start and other.get("end") == end:
            out.append({
                "run_id": rid,
                "scenario": b.get("scenario"),
                "analysis_time": b.get("analysis_time"),
                "status": b.get("status"),
            })
    return out


def control_check(run_id: str) -> dict:
    """对照检查：同源 run（同段窗口）的过程量必须与本 run **完全相同**。

    证明过程量判定不是恒真的 —— 它确实跟着序列走。
    """
    ref = load_run(run_id)
    ref_metrics = compute_process_metrics(ref.get("history") or [])
    siblings = find_same_window_runs(run_id)
    items = []
    for s in siblings:
        b = load_run(s["run_id"])
        other_metrics = compute_process_metrics(b.get("history") or [])
        same = json.dumps(other_metrics, sort_keys=True) == \
            json.dumps(ref_metrics, sort_keys=True)
        items.append({
            "run_id": s["run_id"],
            "scenario": s["scenario"],
            "analysis_time": s["analysis_time"],
            "identical": bool(same),
            "note": ("过程量与本 run 完全相同（同段窗口 → 同一条序列）"
                     if same else
                     "★ 过程量与本 run **不同** —— 同段窗口却结果不同，需要排查"),
        })
    return {"reference": run_id, "siblings": items,
            "has_control": bool(items),
            "same_count": sum(1 for i in items if i["identical"]),
            "diff_count": sum(1 for i in items if not i["identical"])}


#: 规格指定的同源对照 run（与 run_012 用的是同一段窗口）
CONTROL_RUN = "run_023"


# --------------------------------------------------------------------------
# S1 ↔ S2 对比（第四步）
# --------------------------------------------------------------------------

def _cmp_row(label, a, b, fmt=str):
    diff = ""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        diff = f"{b - a:+.2f}" if isinstance(a, float) or isinstance(b, float) \
            else f"{b - a:+d}"
    elif a != b:
        diff = "不同"
    return {"metric": label, "a": fmt(a), "b": fmt(b), "diff": diff}


def compare_data(run_a: str, run_b: str) -> dict:
    """S1 ↔ S2 并排对比（全部从 history 序列算，不读 structured_judgement）。"""
    a = evolution_data_from_bundle(load_run(run_a))
    b = evolution_data_from_bundle(load_run(run_b))
    ma = a["process"]["metrics"]
    mb = b["process"]["metrics"]

    def g(m, key, field):
        mt = (m.get(key) or {})
        if not mt.get("available"):
            return None
        return mt.get(field)

    def num(v, nd=1):
        return "—" if v is None else f"{v:.{nd}f}"

    rows = [
        _cmp_row("末点温度℃", g(ma, "temperature", "end_value"),
                 g(mb, "temperature", "end_value"), lambda v: num(v)),
        _cmp_row("温度首尾差℃", g(ma, "temperature", "delta"),
                 g(mb, "temperature", "delta"), lambda v: num(v)),
        _cmp_row("温度波动区间℃",
                 f"{num(g(ma, 'temperature', 'min'))}~"
                 f"{num(g(ma, 'temperature', 'max'))}",
                 f"{num(g(mb, 'temperature', 'min'))}~"
                 f"{num(g(mb, 'temperature', 'max'))}", lambda v: v),
        _cmp_row("温度波动区间宽度℃", g(ma, "temperature", "range_width"),
                 g(mb, "temperature", "range_width"), lambda v: num(v)),
        _cmp_row("温度最大单步跳变℃", g(ma, "temperature", "max_step"),
                 g(mb, "temperature", "max_step"), lambda v: num(v)),
        _cmp_row("温度单调不降", "是" if g(ma, "temperature", "monotone_up")
                 else "否", "是" if g(mb, "temperature", "monotone_up") else "否"),
        _cmp_row("温度趋势形状", g(ma, "temperature", "trend_shape"),
                 g(mb, "temperature", "trend_shape")),
        _cmp_row("湿度首尾差%", g(ma, "humidity", "delta"),
                 g(mb, "humidity", "delta"), lambda v: num(v)),
        _cmp_row("湿度趋势形状", g(ma, "humidity", "trend_shape"),
                 g(mb, "humidity", "trend_shape")),
        _cmp_row("光照趋势形状", g(ma, "lux", "trend_shape"),
                 g(mb, "lux", "trend_shape")),
    ]

    # ---- 机器判定 C1~C5（相对比较，不拍绝对阈值） ----
    end_a = g(ma, "temperature", "end_value")
    end_b = g(mb, "temperature", "end_value")
    dlt_a = g(ma, "temperature", "delta")
    dlt_b = g(mb, "temperature", "delta")
    wid_a = g(ma, "temperature", "range_width")
    wid_b = g(mb, "temperature", "range_width")
    up_a = bool(g(ma, "temperature", "monotone_up"))
    up_b = bool(g(mb, "temperature", "monotone_up"))
    sh_a = g(ma, "temperature", "trend_shape")
    sh_b = g(mb, "temperature", "trend_shape")

    assertions = [
        {"id": "C1", "claim": "末点温度差 |Δ| ≤ 0.5℃ → 当前值几乎相同",
         "a_value": num(end_a), "b_value": num(end_b),
         "delta": (abs(end_b - end_a) if None not in (end_a, end_b) else None),
         "passed": (None not in (end_a, end_b)
                    and abs(end_b - end_a) <= 0.5),
         "note": "只看当前值分不出 S1/S2 —— 这正是需要看过程的原因"},
        {"id": "C2", "claim": "温度首尾差：后一个 run 更大",
         "a_value": num(dlt_a), "b_value": num(dlt_b),
         "delta": (dlt_b - dlt_a if None not in (dlt_a, dlt_b) else None),
         "passed": (None not in (dlt_a, dlt_b) and dlt_b > dlt_a),
         "note": "过程量差异，不是最终结论差异"},
        {"id": "C3", "claim": "温度波动区间宽度：后一个 run 更大",
         "a_value": num(wid_a), "b_value": num(wid_b),
         "delta": (wid_b - wid_a if None not in (wid_a, wid_b) else None),
         "passed": (None not in (wid_a, wid_b) and wid_b > wid_a),
         "note": "窗口内 max-min"},
        {"id": "C4", "claim": "温度单调上升：后一个 = True，前一个 = False",
         "a_value": str(up_a), "b_value": str(up_b),
         "delta": None,
         "passed": (up_b is True and up_a is False),
         "note": "全程单调不降（相邻差值均 ≥ 0）"},
        {"id": "C5", "claim": "两者温度趋势形状不同",
         "a_value": str(sh_a), "b_value": str(sh_b),
         "delta": None,
         "passed": (sh_a != sh_b),
         "note": "形状由序列判定（规则见页面说明），不是模型输出"},
    ]

    end_txt = (f"若只看末点温度，能否区分 {run_a}/{run_b}？→ "
               + ("不能（两者都是 " + num(end_a) + "℃）"
                  if None not in (end_a, end_b) and abs(end_b - end_a) <= 0.5
                  else f"能（相差 {abs(end_b - end_a):.1f}℃）"))

    # 相对时间轴（只用窗口内相对位置，不把两个 run 的绝对时间混在一起）
    axis = []
    for p in a["points"]:
        axis.append({
            "index": p["index"],
            "relative_minute": p["relative_minute"],
            "a_timestamp": p["timestamp"],
            "b_timestamp": (b["points"][p["index"]]["timestamp"]
                            if p["index"] < len(b["points"]) else None),
        })

    same_day = str(a["window"]["start"])[:10] == str(b["window"]["start"])[:10]

    data = {
        "a": a, "b": b,
        "run_a": run_a, "run_b": run_b,
        "same_calendar_day": same_day,
        "axis": axis,
        "rows": rows,
        "assertions": assertions,
        "reverse_check": end_txt,
        "control_a": control_check(run_a),
        "control_b": control_check(run_b),
        "why_comparable": [
            {"run_id": run_a,
             "reason": (a.get("window_selection") or {}).get("reason")
             or "（该 run 存档没有 window_selection）"},
            {"run_id": run_b,
             "reason": (b.get("window_selection") or {}).get("reason")
             or "（该 run 存档没有 window_selection）"},
        ],
    }
    data["data_sha256"] = _data_sha256(data)
    return data


# --------------------------------------------------------------------------
# 终端渲染 —— 视图二
# --------------------------------------------------------------------------

def _dw(text: str) -> int:
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in str(text))


def _pad(text: str, width: int, align: str = "left") -> str:
    text = str(text)
    space = width - _dw(text)
    if space <= 0:
        return text
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


def _table(headers: list[str], rows: list[list[str]],
           aligns: list[str] | None = None, indent: str = "   ") -> list[str]:
    ncol = len(headers)
    aligns = aligns or ["left"] * ncol
    widths = []
    for i in range(ncol):
        w = _dw(headers[i])
        for r in rows:
            w = max(w, _dw(r[i]))
        widths.append(w)
    out = [indent + "  ".join(_pad(headers[i], widths[i], aligns[i])
                              for i in range(ncol))]
    out.append(indent + "  ".join("-" * widths[i] for i in range(ncol)))
    for r in rows:
        out.append(indent + "  ".join(_pad(r[i], widths[i], aligns[i])
                                      for i in range(ncol)))
    return out


def _fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render_evolution(run_id: str) -> str:
    """渲染视图二（终端版）：单 run 的状态演化。"""
    d = view_evolution_data(run_id)
    fp = d["fingerprint"]
    win = d["window"]
    pts = d["points"]
    metrics = d["process"]["metrics"]

    L: list[str] = []
    L.append(SEP)
    L.append(" B07 · 视图二 | 状态演化")
    L.append(f" 本次分析 = {d['run_id']}   scenario={d['scenario']}   "
             f"analysis_time={d['analysis_time']}")
    L.append(f" model={fp['model']}   提示词指纹={fp['system_prompt_sha256']}  "
             f"问题指纹={fp['question_sha256']}")
    L.append(f" 曲线数据源 = {d['curve_source']}")
    L.append(f" 存档窗口   = {win['start']} → {win['end']}（{win['count']} 点）")
    if d["alignment"]["checked"]:
        ok = d["alignment"]["ok"]
        L.append(f" 窗口核对   = {'一致 ✔（history 就是那次分析用的那段）' if ok else '**不一致 ✘ ' + d['alignment']['note']}")
    L.append(" ★ 只读存档：不重新取数、不重新生成窗口、不重跑判据、不调 LLM")
    L.append("   （history_window 只用来核对边界；判据数值一律读存档里的 criteria）")

    # ---- 【一】过程：逐点读数 ----
    L.append(SEP2 + " 【一】过程 —— 逐点读数（箭头 = 相邻两点变化方向）")
    rows = []
    for p in pts:
        rows.append([
            str(p["index"] + 1),
            str(p["timestamp"]),
            ("—" if p["relative_minute"] is None
             else f"+{p['relative_minute']}′"),
            _fmt(p["temperature"]), p["arrow_temperature"],
            _fmt(p["humidity"]), p["arrow_humidity"],
            _fmt(p["lux"], 0), p["arrow_lux"],
        ])
    L += _table(
        ["#", "时刻", "窗口内", "温度℃", "向", "湿度%", "向", "光照lux", "向"],
        rows,
        ["right", "left", "right", "right", "center", "right", "center",
         "right", "center"])
    L.append("   温度单位 ℃，湿度单位 %，光照单位 lux；6 个点就是 6 个点，"
             "未做平滑/插值")

    # ---- 【二】过程量 ----
    L.append(SEP2 + " 【二】过程量（★ 只从 history 序列算，不读结论）")
    mrows = []
    for key, label, unit in EV_METRICS:
        m = metrics.get(key) or {}
        if not m.get("available"):
            mrows.append([label + f"（{unit}）", m.get("note", "—"), "", "", ""])
            continue
        mrows.append([
            f"{label}（{unit}）",
            _fmt(m["start_value"]), _fmt(m["end_value"]),
            f"{m['delta']:+.2f}",
            f"{m['min']}~{m['max']}（宽 {m['range_width']:.2f}）",
        ])
    L += _table(["过程量", "起始值", "结束值", "首尾差", "窗口内区间"], mrows,
                ["left", "right", "right", "right", "left"])
    srows = []
    for key, label, unit in EV_METRICS:
        m = metrics.get(key) or {}
        if not m.get("available"):
            continue
        srows.append([
            label,
            "是" if m["monotone_up"] else "否",
            "是" if m["monotone_down"] else "否",
            f"{m['max_step']:.2f}" + (
                f"（第{m['max_step_index'] + 1}→第{m['max_step_index'] + 2}点）"
                if m["max_step_index"] is not None else ""),
            m["trend_shape"],
        ])
    L += _table(["趋势", "单调不降", "单调不升", "最大单步跳变", "趋势形状"], srows)
    L.append("   形状 sparkline（各自按自己的 min/max 归一化）：")
    for key, label, unit in EV_METRICS:
        m = metrics.get(key) or {}
        if m.get("available"):
            L.append(f"     {label} {m['sparkline']}   "
                     f"{_fmt(m['start_value'])} → {_fmt(m['end_value'])} {unit}")
    L.append(f"   趋势形状判定依据（规则判定，不是模型输出）：")
    L.append(f"     · 全程单调不降且至少一次严格上升 → 持续上升；"
             f"全程单调不升且至少一次严格下降 → 持续下降")
    L.append(f"     · 相对波动幅度 (max-min)/max|值| ≤ "
             f"{d['stable_rel_span_max']:.0%} → 稳定")
    L.append(f"     · 有内部极值且 |末点-首点| ≤ 波动幅度/2 → 短时波动后恢复")
    L.append(f"     · 其余 → 波动；点数 < 2 → 其他")
    for key, label, unit in EV_METRICS:
        m = metrics.get(key) or {}
        if m.get("available"):
            L.append(f"     · {label}：{m['trend_shape']} —— {m['shape_basis']}")

    # ---- 【三】时间线标记 ----
    L.append(SEP2 + " 【三】时间线标记 —— 状态是怎么走到现在这一步的")
    L.append("   【能做】每条都带着序列里的时刻：")
    if d["timeline"]["markers"]:
        for m in d["timeline"]["markers"]:
            L.append(f"     · [{m['at']}] {m['kind']}")
            L.append(f"         {m['text']}")
            L.append(f"         依据来源：{m['source']}")
    else:
        L.append("     · （无可标注的时点）")
    L.append("   【做不了 —— 必须显式说明，不许留空、不许假装有】")
    for na in d["timeline"]["not_available"]:
        L.append(f"     · {na['title']}")
        L.append(f"         {na['reason']}")

    # ---- 【四】对照 ----
    L.append(SEP2 + " 【四】对照检查（同段窗口的另一个 run）")
    ctrl = control_check(d["run_id"])
    if not ctrl["has_control"]:
        L.append("     （没有找到使用同一段历史窗口的其他 run）")
    else:
        L.append(f"     共 {len(ctrl['siblings'])} 个 run 使用同一段历史窗口；"
                 f"其中 {ctrl['same_count']} 个的过程量与本 run 完全相同，"
                 f"{ctrl['diff_count']} 个不同")
        shown = [s for s in ctrl["siblings"]
                 if not s["identical"] or s["run_id"] == CONTROL_RUN]
        for s in shown:
            L.append(f"     · {s['run_id']}（scenario={s['scenario']}，"
                     f"analysis_time={s['analysis_time']}）")
            L.append(f"         {s['note']}")
    L.append("   要对比 S1 与 S2（只看末点温度分不出，必须看过程）：")
    L.append("     python -m B07.views evolution --compare run_012 run_015")

    if d.get("window_selection", {}).get("reason"):
        L.append(SEP2 + " 【附】窗口是怎么选出来的（为什么这段可比）")
        L.append(f"   scenario={d['window_selection'].get('scenario')}  "
                 f"anchor_policy={d['window_selection'].get('anchor_policy')}")
        L.append(f"   {d['window_selection']['reason']}")
    if d.get("external_conditions"):
        ext = d["external_conditions"]
        L.append(SEP2 + " 【附】外部条件（单点判定，覆盖整个窗口，不是时点）")
        L.append(f"   signal={ext.get('signal')}  "
                 f"judge_method={ext.get('judge_method')}")
        L.append(f"   basis：{ext.get('basis')}")
    L.append(SEP)
    return "\n".join(L)


def render_evolution_compare(run_a: str, run_b: str) -> str:
    """渲染视图二（终端版）：S1 ↔ S2 并排对比。"""
    d = compare_data(run_a, run_b)
    a, b = d["a"], d["b"]

    L: list[str] = []
    L.append(SEP)
    L.append(" B07 · 视图二 | 状态演化 —— S1 ↔ S2 并排对比")
    L.append(f" 对比对象：{run_a}（{a['scenario']}） vs {run_b}（{b['scenario']}）")
    L.append(f" 数据 sha256 = {d['data_sha256'][:12]}　"
             f"（全部从 history 序列算，未读 structured_judgement）")
    L.append(SEP2 + " 【一】两个 run 的身份 —— 绝对时间不在同一天")
    for r, x in ((run_a, a), (run_b, b)):
        fp = x["fingerprint"]
        L.append(f"   {r}：scenario={x['scenario']}  "
                 f"analysis_time={x['analysis_time']}")
        L.append(f"        窗口 {x['window']['start']} → {x['window']['end']}")
        L.append(f"        model={fp['model']}  提示词指纹="
                 f"{fp['system_prompt_sha256']}  问题指纹={fp['question_sha256']}")
    if not d["same_calendar_day"]:
        L.append("   ⚠ 两个 run 不在同一天（"
                 f"{str(a['window']['start'])[:10]} vs "
                 f"{str(b['window']['start'])[:10]}）—— "
                 "下面的 X 轴一律用**窗口内相对位置**，不是绝对时间")
    else:
        L.append("   两个 run 的窗口在同一天；X 轴仍用窗口内相对位置对齐")

    # ---- 【二】曲线（相对时间轴） ----
    L.append(SEP2 + " 【二】曲线（相对时间轴，两个 run 上下对齐）")
    L.append("   相对时间    " + "  ".join(
        f"+{ax['relative_minute']}′" if ax["relative_minute"] is not None
        else "—" for ax in d["axis"]))
    for rid, x in ((run_a, a), (run_b, b)):
        L.append(f"   ┌─ {rid}（{x['scenario']}）绝对窗口 "
                 f"{x['window']['start']} → {x['window']['end']}")
        for key, label, unit in EV_METRICS:
            m = x["process"]["metrics"].get(key) or {}
            if not m.get("available"):
                continue
            L.append(f"   │  {label}曲线  {m['sparkline']}   "
                     + " ".join(_fmt(p[key]) for p in x["points"]))
            L.append(f"   │  {label}方向  " + " ".join(
                p[f"arrow_{key}"].ljust(2) for p in x["points"]))
        L.append("   └─")

    # ---- 【三】过程量对比表 ----
    L.append(SEP2 + " 【三】过程量对比表")
    rows = []
    for r in d["rows"]:
        rows.append([r["metric"], r["a"], r["b"], r["diff"] or "—"])
    L += _table(["过程量", f"{run_a}({a['scenario']})",
                 f"{run_b}({b['scenario']})", "差异"], rows,
                ["left", "right", "right", "right"])

    # ---- 【四】机器判定 ----
    L.append(SEP2 + " 【四】机器判定 C1~C5（相对比较，不拍绝对阈值）")
    for c in d["assertions"]:
        mark = "PASS" if c["passed"] else "FAIL"
        L.append(f"   [{mark}] {c['id']} {c['claim']}")
        L.append(f"          {run_a}={c['a_value']}  {run_b}={c['b_value']}"
                 + (f"  Δ={c['delta']:+.2f}" if c.get("delta") is not None
                    else ""))
        L.append(f"          {c['note']}")
    L.append(f"   反向检查：{d['reverse_check']}")
    L.append("   ★ C1~C5 全部只从 history 序列算，不读 structured_judgement")

    # ---- 【五】对照检查 ----
    L.append(SEP2 + " 【五】对照检查（同段窗口 → 过程量必须完全相同）")
    for ctrl, rid in ((d["control_a"], run_a), (d["control_b"], run_b)):
        if not ctrl["has_control"]:
            L.append(f"   {rid}：（没有找到使用同一段历史窗口的其他 run）")
            continue
        L.append(f"   {rid}：共 {len(ctrl['siblings'])} 个 run 使用同一段窗口，"
                 f"{ctrl['same_count']} 个过程量完全相同，"
                 f"{ctrl['diff_count']} 个不同")
        shown = [s for s in ctrl["siblings"]
                 if not s["identical"] or s["run_id"] == CONTROL_RUN]
        for s in shown:
            L.append(f"     · {rid} vs {s['run_id']}："
                     + ("过程量完全相同 ✔" if s["identical"]
                        else "过程量不同 ✘ —— 需要排查"))
            L.append(f"         {s['note']}")

    # ---- 【六】为什么可比 ----
    L.append(SEP2 + " 【六】为什么这两个 run 可比（窗口选择依据）")
    for w in d["why_comparable"]:
        L.append(f"   {w['run_id']}：{w['reason']}")
    L.append(SEP)
    return "\n".join(L)


# ==========================================================================
# 视图三 | 证据空间（文档 p.63–65）
#
# ★ 只读存档，绝不重新计算：
#   · 证据台账只有一个来源 —— run.json 里的 evidence_ledger；
#   · 方向徽章（supporting / opposing / undetermined）取存档里**已判定**的
#     direction，取不到才用 structured_judgement.evidence_divergence
#     .each_source 兜底；**不重新判定方向**（方向由 B07/evidence_ledger
#     的程序规则判出，不经过 LLM）；
#   · 不调 LLM、不跑 B06、不取天气、不检索知识库、不重查 B02、不重跑判据。
#
# ★ "可用条数"必须按 status == "ok" 统计：
#   counts.adopted_entries 是"**被采用为证据条目**的数"，不是"有效条数"。
#   降级场景里 adopted_entries=4，但天气那条 status=unavailable，
#   真正有效的只有 3 条 —— 用 adopted_entries 会把最该暴露的缺失抹平。
#
# ★ "不存在" ≠ "没有显示"：
#   · 证据存在但取数失败（status=unavailable）→ **照常显示这一块**，
#     并写清"为什么不可用"和"它影响了什么判断"；
#   · 整个 run 没有台账（S1/S2/S3 那 24 个 run）→ 明确标注不可用，
#     不假装有证据、不报错。
# ==========================================================================

#: 四个证据分区（固定四块，缺一块也要显示成"缺"，不许整块消失）
EVIDENCE_BLOCKS = [
    ("B02", "历史证据 · B02"),
    ("B06", "模型证据 · B06"),
    ("B03", "天气证据 · B03"),
    ("B04", "知识证据 · B04"),
]

#: direction → 中文徽章（三色：绿 / 橙红 / 灰）
DIRECTION_LABEL = {
    "supporting": "支持",
    "opposing": "反向",
    "undetermined": "未定",
}
DIRECTION_COLOR = {
    "supporting": "#7fd1a0",
    "opposing": "#e0796b",
    "undetermined": "#8b9a9f",
}


def _direction_of(entry: dict, each_source: dict) -> str:
    """取存档里已判定的方向徽章。

    优先 entries[i].direction；取不到用 evidence_divergence.each_source
    兜底；都没有 → undetermined（灰）。**不重新判定**。
    """
    d = entry.get("direction")
    if d in DIRECTION_LABEL:
        return d
    d = (each_source or {}).get(entry.get("source_type"))
    if d in DIRECTION_LABEL:
        return d
    return "undetermined"


def _div_items(divergence: dict, source_type: str) -> list[dict]:
    """从 evidence_divergence 的三列里取出某个来源的判断项。"""
    out = []
    for bucket in ("supporting", "opposing", "undetermined"):
        for it in (divergence or {}).get(bucket) or []:
            if isinstance(it, dict) and it.get("source_type") == source_type:
                out.append({
                    "bucket": bucket,
                    "bucket_label": DIRECTION_LABEL.get(bucket, bucket),
                    "summary": it.get("summary"),
                    "basis": it.get("basis"),
                })
    return out


def _fmt_pt(pt) -> str:
    if not isinstance(pt, dict):
        return "—"
    return (f"{pt.get('timestamp')} 温度 {pt.get('temperature')}℃ / "
            f"湿度 {pt.get('humidity')}% / 降水概率 "
            f"{pt.get('precipitation_probability')}% / 云量 "
            f"{pt.get('cloud_cover')}% / {pt.get('weather_description')}")


def _block_b02(entry: dict) -> tuple[list[str], list[str]]:
    """历史证据（B02）：它从哪里来 / 它说了什么。"""
    res = entry.get("result") or {}
    ext = entry.get("ext") or {}
    where = [
        f"证据编号 {entry.get('evidence_id')}",
        f"来源 source = {entry.get('source')}",
        f"分析时刻 time = {entry.get('time')}",
        f"窗口采样点数 = {ext.get('point_count', '—')}",
    ]
    said = []
    if res.get("stability_conclusion"):
        said.append(f"稳定判据结论：{res['stability_conclusion']}")
    if res.get("stability_passed") is not None:
        said.append(f"stability_passed = {res.get('stability_passed')}")
    temps = res.get("temps") or []
    if temps:
        said.append(f"窗口温度序列 = {temps}")
    for c in ext.get("stability_criteria") or []:
        if not isinstance(c, dict):
            continue
        said.append(
            f"判据「{c.get('name')}」：实测 {c.get('value')} / 阈值 "
            f"{c.get('threshold')} → "
            f"{'通过' if c.get('passed') else '未通过'}"
            f"（{c.get('note')}）")
    return where, said


def _block_b06(entry: dict) -> tuple[list[str], list[str]]:
    """模型证据（B06）。"""
    res = entry.get("result") or {}
    ext = entry.get("ext") or {}
    where = [
        f"证据编号 {entry.get('evidence_id')}",
        f"来源 source = {entry.get('source')}",
        f"分析时刻 time = {entry.get('time')}",
        f"model_version = {res.get('model_version') or ext.get('model_version')}",
        f"窗口 window_id = {res.get('window_id') or ext.get('window_id')}",
    ]
    said = []
    if res.get("predicted_attention"):
        said.append(f"模型输出 predicted_attention = "
                    f"{res.get('predicted_attention')}")
    if res.get("confidence") is not None:
        said.append(f"置信度 confidence = {res.get('confidence')}")
    if res.get("feature_count") is not None:
        said.append(f"特征数 feature_count = {res.get('feature_count')}"
                    f"（{res.get('feature_contribution_method')}）")
    for c in (res.get("feature_contributions_top5") or [])[:5]:
        if isinstance(c, dict):
            said.append(f"主要贡献特征 {c.get('feature')}："
                        f"coefficient={c.get('coefficient')}"
                        f"（{c.get('direction')}）")
    metrics = ext.get("metrics") or {}
    if metrics:
        said.append("模型评估指标（存档自带）："
                    + "，".join(f"{k}={metrics[k]}" for k in
                                ("accuracy", "precision", "recall", "f1")
                                if k in metrics))
    return where, said


def _block_b03(entry: dict) -> tuple[list[str], list[str]]:
    """天气证据（B03）。"""
    res = entry.get("result") or {}
    ext = entry.get("ext") or {}
    where = [
        f"证据编号 {entry.get('evidence_id')}",
        f"来源 source = {entry.get('source')}",
        f"取得时间 retrieved_at = {ext.get('retrieved_at')}",
        f"provider = {ext.get('provider')}",
        f"预报点 forecast_time = {ext.get('forecast_time')}"
        f"（窗口 {ext.get('forecast_window')}）",
        f"地点 = {ext.get('location')}",
    ]
    if ext.get("label"):
        where.append(f"数据性质标签 = {ext.get('label')}"
                     f"（is_constructed={ext.get('is_constructed')}）")
    said = []
    if res:
        said.append(f"外部信号 signal = {res.get('signal')}")
        said.append(f"判定方法 judge_method = {res.get('judge_method')}")
        if res.get("basis"):
            said.append(f"判定依据 basis：{res['basis']}")
        if res.get("baseline_point"):
            said.append(f"基线点(now)：{_fmt_pt(res['baseline_point'])}")
        if res.get("forecast_point"):
            said.append(f"预报点：{_fmt_pt(res['forecast_point'])}")
        for k, label in (("precip_delta", "降水概率差"), ("temp_delta", "温度差"),
                         ("cloud_delta", "云量差")):
            if res.get(k) is not None:
                said.append(f"{label} {k} = {res.get(k)}")
    else:
        said.append("（没有内容 —— 这次取数失败了）")
    return where, said


def _block_b04(entry: dict) -> tuple[list[str], list[str]]:
    """知识证据（B04）。"""
    res = entry.get("result") or {}
    ext = entry.get("ext") or {}
    where = [
        f"证据编号 {entry.get('evidence_id')}",
        f"来源 source = {entry.get('source')}",
        f"检索时刻 time = {entry.get('time')}（★ 这是知识检索时刻，"
        f"与分析时刻可能差好几天）",
        f"检索式 query = {res.get('query')}",
        f"top_k = {res.get('top_k')}",
    ]
    said = []
    for c in (res.get("citations") or [])[:5]:
        if not isinstance(c, dict):
            continue
        said.append(
            f"命中 {c.get('chunk_id')}（{c.get('source_id')}，"
            f"score={c.get('score')}）章节「{c.get('section')}」"
            f"页 {c.get('page')}")
        if c.get("content"):
            said.append(f"    摘录：{str(c['content'])[:120]}")
    return where, said


_BLOCK_BUILDERS = {
    "B02": _block_b02,
    "B06": _block_b06,
    "B03": _block_b03,
    "B04": _block_b04,
}


def _unavailable_impact(entry: dict, bundle: dict) -> tuple[list[str], list[str]]:
    """不可用证据的「为什么不可用」和「它影响了什么判断」。

    ★ 内容全部来自存档：
      · 为什么不可用 → entry.error + ext.unavailable_context.warnings
      · 它影响了什么判断 → ext.unavailable_context（judge_method / signal /
        future_ok / basis）+ combined_attention.reason
      **不写死通用文案。**
    """
    ext = entry.get("ext") or {}
    ec = bundle.get("external_conditions") or {}
    ca = bundle.get("combined_attention") or {}
    uc = (ext.get("unavailable_context")
          or ec.get("unavailable_context") or {})

    why = []
    if entry.get("error"):
        why.append(f"失败原因（存档 error 原文）：{entry['error']}")
    else:
        why.append(f"失败原因：status = {entry.get('status')}"
                   f"（存档未记录 error 字段）")
    for w in (uc.get("warnings") or [])[:3]:
        why.append(f"警告原文：{w}")

    impact = []
    if uc:
        impact.append(
            f"外部判定未做出：judge_method = {uc.get('judge_method')}"
            f"，signal = {uc.get('signal')}"
            f"，future_ok = {uc.get('future_ok')}"
            f"，basis：{uc.get('basis')}")
    if ca.get("value"):
        impact.append(f"因此合并关注等级仍为 {ca.get('value')}"
                      f"（internal={ca.get('internal_predicted_attention')}"
                      f"，external_signal={ca.get('external_signal')}"
                      f"，has_conflict={ca.get('has_conflict')}）")
        if ca.get("reason"):
            impact.append(f"存档里的判定理由：{ca['reason']}")
    if not impact:
        impact.append("（存档未记录该项证据对最终判断的影响）")
    return why, impact


def view_evidence_data(run_id: str) -> dict:
    """装配视图三（证据空间）要用的全部数据（只读存档，不重算）。"""
    bundle = load_run(run_id)
    sj = bundle.get("structured_judgement") or {}
    divergence = sj.get("evidence_divergence") or {}
    each_source = divergence.get("each_source") or {}
    ledger = bundle.get("evidence_ledger") or {}
    entries = list(ledger.get("entries") or [])
    ca = bundle.get("combined_attention") or {}
    llm_meta = bundle.get("llm_meta") or {}

    # ---- 按 source_type 归位（一条 entry 只归一块）----
    by_type: dict[str, dict] = {}
    for e in entries:
        by_type.setdefault(e.get("source_type"), e)

    blocks = []
    for stype, title in EVIDENCE_BLOCKS:
        entry = by_type.get(stype)
        if entry is None:
            blocks.append({
                "source_type": stype,
                "title": title,
                "absent": True,
                "exists": False,
                "status": "absent",
                "status_label": "不存在（本次运行没有这条证据）",
                "error": None,
                "direction": "undetermined",
                "direction_label": "未定",
                "direction_color": DIRECTION_COLOR["undetermined"],
                "evidence_id": None,
                "source": None,
                "time": None,
                "where": ["（本次运行没有这条证据 —— 是真的不存在，"
                          "不是「没显示」）"],
                "said": [],
                "valid": ["（没有这条证据，本块没有内容）"],
                "why_unavailable": [],
                "impact": [],
                "impact_items": [],
            })
            continue

        status = entry.get("status")
        direction = _direction_of(entry, each_source)
        where, said = _BLOCK_BUILDERS[stype](entry)
        valid = [
            f"status = {status}",
            f"方向徽章 = {DIRECTION_LABEL[direction]}",
        ]
        why_unavailable = []
        impact = []
        if status == "ok":
            valid.append("这一条现在有效（取数成功，判定已完成）")
            for it in _div_items(divergence, stype):
                impact.append(
                    f"[{it['bucket_label']}] {it.get('summary')}"
                    + (f" —— {it.get('basis')}" if it.get("basis") else ""))
            if not impact:
                impact.append("（存档未记录该来源对应的判断项）")
        else:
            why_unavailable, impact = _unavailable_impact(entry, bundle)
            valid.append("这一条现在**无效**：取数失败，没有参与判定")
            valid.append("★ 它是「证据存在但不可用」—— 照常显示，"
                         "不许当作没有这条证据")

        blocks.append({
            "source_type": stype,
            "title": title,
            "absent": False,
            "exists": True,
            "status": status,
            "status_label": ("有效" if status == "ok"
                             else f"不可用（status={status}）"),
            "error": entry.get("error"),
            "direction": direction,
            "direction_label": DIRECTION_LABEL[direction],
            "direction_color": DIRECTION_COLOR[direction],
            "evidence_id": entry.get("evidence_id"),
            "source": entry.get("source"),
            "time": entry.get("time"),
            "where": where,
            "said": said,
            "valid": valid,
            "why_unavailable": why_unavailable,
            "impact": impact,
            "impact_items": _div_items(divergence, stype),
        })

    # ---- 可用条数：★ 按 status == "ok" 统计，绝不用 counts.adopted_entries ----
    ok_entries = [e for e in entries if e.get("status") == "ok"]
    not_ok_entries = [e for e in entries if e.get("status") != "ok"]
    counts = {
        "entries": len(entries),
        "usable": len(ok_entries),            # ★ 有效条数（status == ok）
        "unusable": len(not_ok_entries),
        "ledger_adopted_entries": (ledger.get("counts") or {}).get(
            "adopted_entries"),              # ★ 采用数 —— 不是有效数
        "summary": (
            f"{len(ok_entries)}/{len(entries)} 条证据可用"
            f"（按 status==ok 统计）；"
            f"台账 counts.adopted_entries="
            f"{(ledger.get('counts') or {}).get('adopted_entries')}"
            f"（那是「被采用为证据条目」的数，**不是**有效条数）"
        ) if entries else EVIDENCE_NO_LEDGER_NOTE,
    }

    tool_calls = ledger.get("tool_calls") or {}
    adoption = ledger.get("adoption") or {}
    not_adopted_reasons = ledger.get("not_adopted_reasons") or {}

    data = {
        "run_id": run_id,
        "run_path": f"B07/runs/{run_id}/run.json",
        "scenario": bundle.get("scenario"),
        "created_at": bundle.get("created_at"),
        "analysis_time": bundle.get("analysis_time"),
        "fingerprint": {
            "model": llm_meta.get("model"),
            "system_prompt_sha256": llm_meta.get("system_prompt_sha256"),
            "question_sha256": llm_meta.get("question_sha256"),
        },
        "has_ledger": bool(entries),
        "ledger_note": ("" if entries else EVIDENCE_NO_LEDGER_NOTE),
        "blocks": blocks,
        "counts": counts,
        "not_ok_entries": [
            {
                "evidence_id": e.get("evidence_id"),
                "source_type": e.get("source_type"),
                "status": e.get("status"),
                "error": e.get("error"),
            } for e in not_ok_entries
        ],
        "divergence": {
            "has_conflict": bool(divergence.get("has_conflict")),
            "each_source": each_source,
            "resolution": divergence.get("resolution"),
            "note": divergence.get("note"),
            "supporting": divergence.get("supporting") or [],
            "opposing": divergence.get("opposing") or [],
            "undetermined": divergence.get("undetermined") or [],
        },
        "combined_attention": {
            "value": ca.get("value"),
            "internal_predicted_attention": ca.get("internal_predicted_attention"),
            "external_signal": ca.get("external_signal"),
            "has_conflict": bool(ca.get("has_conflict")),
            "reason": ca.get("reason"),
        },
        "external_conditions": {
            "available": (bundle.get("external_conditions") or {}).get("available"),
            "signal": (bundle.get("external_conditions") or {}).get("signal"),
            "judge_method": (bundle.get("external_conditions") or {}).get(
                "judge_method"),
            "basis": (bundle.get("external_conditions") or {}).get("basis"),
        },
        "tool_calls": {
            "records": tool_calls.get("records") or [],
            "counts": tool_calls.get("counts") or {},
            "adoption_text": adoption.get("text"),
            "adopted_calls": adoption.get("adopted_calls"),
            "not_adopted_calls": adoption.get("not_adopted_calls") or [],
            "not_adopted_reasons": not_adopted_reasons,
        },
    }
    data["data_sha256"] = _data_sha256(data)
    return data


# --------------------------------------------------------------------------
# 终端渲染 —— 视图三
# --------------------------------------------------------------------------

def _wrap(text: str, indent: str, width: int = WIDTH) -> list[str]:
    """按显示宽度折行（中文算两格）。"""
    out = []
    cur = ""
    cur_w = 0
    for ch in str(text):
        w = 2 if ord(ch) > 0x2E80 else 1
        if cur_w + w > width - len(indent):
            out.append(indent + cur)
            cur, cur_w = "", 0
        cur += ch
        cur_w += w
    if cur:
        out.append(indent + cur)
    return out


def render_evidence(run_id: str) -> str:
    """渲染视图三（证据空间，终端版）。只读存档，不重算。"""
    d = view_evidence_data(run_id)
    L: list[str] = []
    L.append(SEP)
    L.append(" B07 · 视图三 | 证据空间")
    L.append(f" 本次分析 = {d['run_id']}   scenario={d['scenario']}  "
             f"analysis_time={d['analysis_time']}")
    L.append(f" model={d['fingerprint']['model']}   "
             f"提示词指纹={d['fingerprint']['system_prompt_sha256']}  "
             f"问题指纹={d['fingerprint']['question_sha256']}")
    L.append(f" 数据 sha256（本次渲染）= {d['data_sha256'][:12]}")
    L.append(SEP2 + " 【0】证据可用度（★ 按 status==ok 统计，"
                    "不用 counts.adopted_entries）")
    L.append(f"     {d['counts']['summary']}")

    if not d["has_ledger"]:
        L.append(SEP2)
        L.append(f"     ★ {EVIDENCE_NO_LEDGER_NOTE}")
        L.append("     下面四块一律显示为「不存在」—— "
                 "是真的没有这条证据，不是「没显示」。")
        L.append(SEP2 + " 【1~4】四类证据分区（本次运行没有台账，"
                        "四块都显示为「不存在」）")
        for b in d["blocks"]:
            L.append(f"     · {b['title']}：{b['status_label']}"
                     f"　证据编号 —　方向徽章 = [{b['direction_label']}]")
        L.append(SEP2 + " 【5】工具 / 运行状态")
        L.append("     （本次运行没有台账，工具调用记录不存在）")
        L.append(SEP2 + " 【6】分歧与缺失能不能被看见")
        L.append("     无法计算 —— 没有证据台账")
        L.append(SEP)
        return "\n".join(L)

    for i, b in enumerate(d["blocks"]):
        L.append(SEP2)
        L.append(f" 【{i + 1}】{b['title']}"
                 f"   方向徽章 = [{b['direction_label']}]"
                 f"   status = {b['status']}")
        if b["absent"]:
            L.append("     ★ 这条证据不存在（不是「没显示」）")
            for line in b["where"]:
                L.append(f"     · {line}")
            L.append(SEP)
            continue
        L.append(f"     它从哪里来？")
        for line in b["where"]:
            L.extend(_wrap(f"· {line}", "       "))
        L.append(f"     它说了什么？")
        if b["said"]:
            for line in b["said"]:
                L.extend(_wrap(f"· {line}", "       "))
        else:
            L.append("       （没有内容 —— 这次取数失败了）")
        L.append(f"     它现在是否有效？")
        for line in b["valid"]:
            L.extend(_wrap(f"· {line}", "       "))
        if b["why_unavailable"]:
            L.append(f"     为什么不可用？")
            for line in b["why_unavailable"]:
                L.extend(_wrap(f"· {line}", "       "))
        L.append(f"     它影响了什么判断？")
        if b["impact"]:
            for line in b["impact"]:
                L.extend(_wrap(f"· {line}", "       "))
        else:
            L.append("       （存档未记录该项证据对判断的影响）")

    # ---- 第五块：工具 / 运行状态 ----
    tc = d["tool_calls"]
    L.append(SEP2)
    L.append(" 【5】工具 / 运行状态")
    L.append(f"     {tc.get('adoption_text') or '（存档未记录 adoption.text）'}")
    counts = tc.get("counts") or {}
    L.append(f"     调用总数 = {counts.get('total')}   "
             f"被采用 = {counts.get('adopted')}   "
             f"调用了但未采用 = {counts.get('called_but_not_adopted')}")
    for r in tc.get("records") or []:
        mark = "✔ 已采用" if r.get("adopted") else "✘ 未采用"
        L.append(f"       · {r.get('tool_name')}  [{mark}]"
                 f"  status={r.get('status')}")
        L.append(f"         目的：{r.get('purpose')}")
        if r.get("arguments"):
            L.append(f"         参数：{json.dumps(r['arguments'], ensure_ascii=False)}")
        if not r.get("adopted"):
            reason = (tc.get("not_adopted_reasons") or {}).get(r.get("tool_name"))
            L.append(f"         未采用的理由：{reason or '（存档未记录理由）'}")
        if r.get("error"):
            L.append(f"         错误：{r.get('error')}")

    # ---- 收尾：分歧与缺失能不能被看见 ----
    dv = d["divergence"]
    L.append(SEP2 + " 【6】分歧与缺失能不能被看见")
    L.append(f"     方向不一致？has_conflict = {dv['has_conflict']}"
             f"（系统{'保留了分歧' if dv['has_conflict'] else '未发现方向冲突'}）")
    L.append(f"     存档里的处理：{dv.get('resolution') or '—'}")
    L.append(f"     每源方向：{json.dumps(dv.get('each_source') or {}, ensure_ascii=False)}")
    ca = d["combined_attention"]
    L.append(f"     合并关注等级 = {ca.get('value')}"
             f"（internal={ca.get('internal_predicted_attention')}"
             f"，external={ca.get('external_signal')}）")
    if ca.get("reason"):
        L.append(f"     判定理由：{ca['reason']}")
    if d["not_ok_entries"]:
        L.append(f"     ★ 现在缺失的证据（{len(d['not_ok_entries'])} 条）：")
        for m in d["not_ok_entries"]:
            L.append(f"       · {m['evidence_id']}（{m['source_type']}，"
                     f"status={m['status']}）")
            L.append(f"         {m.get('error') or '（存档未记录 error 字段）'}")
    else:
        L.append("     缺失清单：（无）")
    L.append(SEP)
    return "\n".join(L)



# ==========================================================================
# 视图四 | 场景回放与对比（文档 p.66–68）
#
# ★ 这里**只做对比与归因**，只读存档，绝不重新计算：
#   · 两个 run.json 读进来，比三层（inputs / evidence / state）；
#   · 归因规则 R1~R5 全部由字段差异推出，每条文本都引用具体字段值；
#   · 不调 LLM、不跑 B06、不取天气、不检索知识库、不重查 B02、不重跑判据。
#
# ★ 视图四里**唯一**允许发起新运行的是回放器 `B07.replay`：
#   回放 → 产生新 run_id → 落盘 → 之后由四个视图读取同一次分析。
# ==========================================================================


def view_compare_data(run_a: str, run_b: str) -> dict:
    """装配视图四（对比 + 归因）要用的数据。

    与视图一/二/三 **共用同一个 load_run()**；三层对比与归因交给
    `B07.views_diff.diff_runs()`（同样只读存档）。
    """
    from B07.views_diff import diff_runs

    d = diff_runs(run_a, run_b)
    d["view"] = "compare"
    return d


def _cmp_mark(flag: bool) -> str:
    return "✔" if flag else "✘"


def _cmp_item_line(it: dict, width: int = 62) -> list[str]:
    if it["key"] == "history（序列逐点）":
        # 序列不逐点铺开，只报"几/几点不同"
        return [f"history（序列逐点）：{it['diff_points']}/"
                f"{it['total_points']} 点不同"
                f"（{'不同' if it['changed'] else '逐点相同'}）"]
    a = str(it["a"])
    b = str(it["b"])
    if len(a) > width:
        a = a[: width - 1] + "…"
    if len(b) > width:
        b = b[: width - 1] + "…"
    return [f"{it['key']}：{a} → {b}"]


def render_compare(run_a: str, run_b: str) -> str:
    """渲染视图四（终端版）：一次只比两个 run。"""
    d = view_compare_data(run_a, run_b)
    a, b = d["a"], d["b"]
    inputs, evidence, state = d["inputs"], d["evidence"], d["state"]
    attr = d["attribution"]

    L: list[str] = []
    L.append(SEP)
    L.append(" B07 · 视图四 | 场景回放与对比")
    L.append(f" 对比 A = {run_a} ({a['scenario']})      "
             f"对比 B = {run_b} ({b['scenario']})")
    L.append(f" A 提示词指纹={a['fingerprint']['system_prompt_sha256']}   "
             f"B 提示词指纹={b['fingerprint']['system_prompt_sha256']}")
    L.append(f" A 问题指纹={a['fingerprint']['question_sha256']}     "
             f"B 问题指纹={b['fingerprint']['question_sha256']}")
    L.append(f" A 数据 sha256={d['data_sha256'][:12]}（本次渲染）"
             f"   只读存档，不重算")

    # ---- 【一】五项并排 ----
    L.append(SEP2 + " 【一】五项并排（当前状态 / 近期趋势 / 预测 / 证据 / 最终判断）")
    labels = [("当前状态", "current_state"), ("近期趋势", "recent_trend"),
              ("预测", "prediction"), ("证据", "evidence"),
              ("最终判断", "final")]
    for label, key in labels:
        va = a["five"][key]
        vb = b["five"][key]
        head = "   " + _pad(label, 12)
        L.append(f"{head}A: {va}")
        L.append(f"{head}B: {vb}")

    # ---- 【二】差异归因 ----
    L.append(SEP2 + " 【二】差异归因")

    def layer(title: str, lay: dict, skip_keys: tuple = ()) -> None:
        L.append(f"   {title}")
        chg = [it for it in lay["changed_items"] if it["key"] not in skip_keys]
        unchg = [it for it in lay["items"]
                 if not it["changed"] and it["key"] not in skip_keys]
        if chg:
            L.append(f"     {_cmp_mark(True)} 变了：")
            for it in chg:
                for line in _cmp_item_line(it):
                    L.append(f"          · {line}")
        else:
            L.append(f"     {_cmp_mark(False)} 变了：（无）")
        if unchg:
            L.append(f"     {_cmp_mark(False)} 没变：")
            for it in unchg:
                for line in _cmp_item_line(it, width=48):
                    L.append(f"          · {line}")

    layer("输入变了：", inputs)

    layer("证据变了：", evidence)
    for n in evidence["notes"]:
        L.append(f"          ★ {n}")

    layer("状态变了：", state)

    L.append(f"   归因链  ：{attr['chain']}")
    for n in attr["notes"]:
        L.append(f"   {n}")
    if attr["refs"]:
        L.append("   变了的具体字段：")
        for r in attr["refs"]:
            L.append(f"     · {r}")
    L.append(f"   归因规则  ：{attr['rule']} —— {attr['rule_text']}")
    L.append(f"   attribution_complete = {attr['attribution_complete']}")
    L.append(f"   instability_detected = {attr['instability_detected']}"
             f"（同一条件下重跑结果不一致）")
    L.append(SEP)
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="B07 视图：视图一 状态总览 / 视图二 状态演化 / "
                    "视图三 证据空间 / 视图四 场景回放与对比"
                    "（只读 run 存档，绝不重新计算）")
    ap.add_argument("command", nargs="?", default="overview",
                    help="子命令：overview（视图一）/ evolution（视图二）/"
                         "evidence（视图三）/ compare（视图四）/ check（统一检查）")
    ap.add_argument("run_id_pos", nargs="?", default=None,
                    help="run_id（位置参数）")
    ap.add_argument("run_b_pos", nargs="?", default=None,
                    help="第二个 run_id（compare 子命令用）")
    ap.add_argument("--run-id", default=None,
                    help="run_id（与位置参数等价）")
    ap.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"),
                    default=None,
                    help="evolution 子命令：对比两个 run（相对时间轴）；"
                         "compare 子命令：也可用它传两个 run_id")
    args = ap.parse_args(argv)

    if args.command == "evolution":
        try:
            if args.compare:
                print(render_evolution_compare(args.compare[0],
                                               args.compare[1]))
            else:
                run_id = args.run_id or args.run_id_pos
                if not run_id:
                    print("[FAIL] 缺少 run_id。用法："
                          "python -m B07.views evolution run_015")
                    return 2
                print(render_evolution(run_id))
        except FileNotFoundError as exc:
            print(f"[FAIL] {exc}")
            return 2
        except Exception as exc:          # 失败显式暴露，不伪装成功
            print(f"[FAIL] {type(exc).__name__}: {exc}")
            return 2
        return 0

    if args.command == "compare":
        run_a = args.run_id_pos or (args.compare[0] if args.compare else None)
        run_b = args.run_b_pos or (args.compare[1] if args.compare else None)
        if not run_a or not run_b:
            print("[FAIL] 缺少 run_id。用法："
                  "python -m B07.views compare run_012 run_015")
            return 2
        try:
            print(render_compare(run_a, run_b))
        except FileNotFoundError as exc:
            print(f"[FAIL] {exc}")
            return 2
        except Exception as exc:          # 失败显式暴露，不伪装成功
            print(f"[FAIL] {type(exc).__name__}: {exc}")
            return 2
        return 0

    if args.command == "check":
        run_id = args.run_id or args.run_id_pos
        if not run_id:
            print("[FAIL] 缺少 run_id。用法："
                  "python -m B07.views check run_082")
            return 2
        from B07.views_check import main as check_main
        return check_main([run_id])

    if args.command == "evidence":
        run_id = args.run_id or args.run_id_pos or DEFAULT_EVIDENCE_RUN
        try:
            print(render_evidence(run_id))
        except FileNotFoundError as exc:
            print(f"[FAIL] {exc}")
            return 2
        except Exception as exc:          # 失败显式暴露，不伪装成功
            print(f"[FAIL] {run_id}: {type(exc).__name__}: {exc}")
            return 2
        return 0

    if args.command != "overview":
        print(f"[FAIL] 未知子命令：{args.command}"
              f"（可用：overview / evolution / evidence / compare）")
        return 2
    run_id = args.run_id or args.run_id_pos
    if not run_id:
        print("[FAIL] 缺少 run_id。用法：python -m B07.views overview run_082")
        return 2
    try:
        print(render_overview(run_id))
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        return 2
    except Exception as exc:              # 失败显式暴露，不伪装成功
        print(f"[FAIL] {run_id}: {type(exc).__name__}: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
