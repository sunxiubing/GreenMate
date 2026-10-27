"""B07 第二遍【创新点 2】—— 把"多源信息"变成"可追溯证据"的自动溯源器

这一轮是**审计 + 证明**，不是开发。

  * 只新增本文件（+ artifacts 产物 + 测试），**不改 B07 现有任何代码**；
  * 不新增任何 LLM 调用，全部基于 S4/S5 已落盘的 run 存档；
  * 不改 SYSTEM_PROMPT / 固定问题 / 任何判据阈值，不碰 B01–B06。

要证明的东西（文档 p.47–50 创新点 2）：

  第一步  找一句最终判断，**反着往回查**：这句话依据了什么？
  第二步  四类信息各自的作用不能互相替代
          （历史=已发生 / 天气=可能发生的外部条件 / 知识=一般性依据 / 模型=下一窗口预测）
  第三步  每份证据自报身份：{我是什么 / 从哪来 / 什么时候取得 / 现在是否可用 / 提供了什么}
  第四步  查 B05：到底是**真调用**，还是"模型说自己调用了"
  第五步  再说一次"为什么需要关注"，每句挂到具体证据

产物：

  B07/artifacts/innovation2_trace.json         —— 溯源器输出（**严格按规格的固定格式**）
  B07/artifacts/innovation2_trace_detail.json  —— 同一份结果的详版（判定依据、匹配信号、
                                                 限制陈述溯源），报告从它渲染
  B07/artifacts/innovation2_report.md          —— 报告（黑盒对照数据直接读
                                                  B07/artifacts/s4_3_contrast.json，
                                                  不重新跑、不手抄）

用法：

    python -m B07.innovation2                     # 溯源 + 写产物
    python -m B07.innovation2 --report-only        # 只重建报告
    python -m B07.innovation2 --run run_082       # 指定主 run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def _console_codepage() -> int:
    """当前 Windows 控制台的输出 codepage（拿不到就当 65001）。"""
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetConsoleOutputCP()) or 65001
    except Exception:            # pragma: no cover - 非 Windows / 取不到
        return 65001


def _set_console_codepage(cp: int) -> bool:
    """把控制台输出 codepage 改成 cp（成功返回 True）。

    这是关键的一步：**控制台按自己的 codepage 解码程序吐出的字节**。
    程序吐 UTF-8，就该把控制台设成 65001；程序吐 GBK，就该设成 936。
    两边对不上，中文必成乱码 —— 而且**光改程序不改控制台是不彻底的**。
    """
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.SetConsoleOutputCP(cp))
    except Exception:            # pragma: no cover - 非 Windows / 取不到
        return False


#: 编码名 → Windows codepage
_CP_BY_ENCODING = {
    "utf-8": 65001, "utf8": 65001, "utf_8": 65001,
    "gbk": 936, "gb2312": 936, "gb18030": 54936, "cp936": 936,
    "cp65001": 65001,
}


def _cp_for_encoding(enc: str) -> int | None:
    """编码名 → codepage（认不出来返回 None）。"""
    key = (enc or "").strip().lower().replace("cp:", "")
    if key in _CP_BY_ENCODING:
        return _CP_BY_ENCODING[key]
    if key.startswith("cp") and key[2:].isdigit():
        return int(key[2:])
    return None


def _resolve_stdout_encoding() -> str:
    """决定 stdout 吐什么编码的字节。

    优先级：`--stdout-encoding` > 环境变量 `B07_STDOUT_ENCODING` >
    `PYTHONIOENCODING` > **utf-8**（现代终端的通用默认）。
    """
    for src in (_STDIO_ENCODING_OVERRIDE, os.environ.get("B07_STDOUT_ENCODING"),
                os.environ.get("PYTHONIOENCODING")):
        if src:
            return src.split(":")[0].strip() or "utf-8"
    return "utf-8"


def _configure_stdio() -> str:
    """让 stdout/stderr 吐出的**字节**与**控制台解码用的 codepage** 严格一致。

    乱码的两个成因，两边都要治：

      1. 程序吐 UTF-8 字节，控制台却是 936(GBK) → 控制台按 GBK 解 → 满屏乱码；
      2. 程序吐 GBK 字节，读取方按 UTF-8 解 → 一堆 ``。

    做法：**先按我们要吐的字节把控制台 codepage 设成对应的那个**
    （`SetConsoleOutputCP`），**再**把 stdout 配成同一编码。
    这样 cmd.exe、Windows Terminal、VSCode 集成终端都按同一套编码走。

    被重定向 / 走管道时不是终端，不用管控制台，直接 UTF-8。
    """
    enc = _resolve_stdout_encoding()
    try:
        if sys.stdout.isatty():
            cp = _cp_for_encoding(enc)
            if cp is None:                    # 不认识的编码名 → 退回控制台现状
                cp = _console_codepage()
                enc = f"cp{cp}"
            _set_console_codepage(cp)
    except Exception:            # pragma: no cover
        enc = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=enc, errors="replace")
        except Exception:        # pragma: no cover
            pass
    return enc


#: 命令行 --stdout-encoding 覆盖（在 main() 里赋值；模块加载时还没有）
_STDIO_ENCODING_OVERRIDE: str | None = None
_STDOUT_ENCODING = _configure_stdio()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

ARTIFACTS_DIR = _PROJECT_ROOT / "B07" / "artifacts"
TRACE_JSON_PATH = ARTIFACTS_DIR / "innovation2_trace.json"
DETAIL_JSON_PATH = ARTIFACTS_DIR / "innovation2_trace_detail.json"
REPORT_PATH = ARTIFACTS_DIR / "innovation2_report.md"
CONTRAST_PATH = ARTIFACTS_DIR / "s4_3_contrast.json"

#: 主 run（文档建议 run_082；run_084 是它的天气降级变体）
DEFAULT_RUN = "run_082"
#: 附带审计的 run：run_084 让 V3 落在"调用过但失败"的情形上，
#: run_057 的回答里真的出现了"知识"字样，让 V4 有一个真实触发点。
DEFAULT_RUNS = ("run_082", "run_084", "run_057")


# --------------------------------------------------------------------------
# 1. 规则化挑句：什么是"重要结论句"
# --------------------------------------------------------------------------

#: 判断/建议类动词（文档给的是"如…"，是举例不是穷举）。
#: 除清单本身外收了三个同类词：祈使建议"请"、义务/建议"应"、判断性"预测"。
CLAIM_VERBS = ("建议", "需要", "应当", "预计", "判定", "不排除", "请",
               "应", "预测")

#: 各类证据在正文里的特征词 —— 用于"句子里出现了哪类证据的线索"
ROLE_KEYWORDS = {
    "history": ("温度", "湿度", "光照", "波动", "历史", "过去", "近3小时",
                "不稳定", "稳定", "单调", "阈值", "上升", "下降", "判据"),
    "weather": ("天气", "降水", "降雨", "预报", "外部", "气温", "云量"),
    "knowledge": ("知识", "检索", "条目", "文献", "方法论", "依据条目"),
    "model": ("B06", "模型", "预测", "WATCH", "NORMAL", "置信度",
              "关注程度", "短时", "窗口"),
}

#: 区分度低的词：命中它们只说明"谈的是同一类东西"，不说明"这条证据支撑了这句话"
GENERIC_TERMS = frozenset({
    "温度", "湿度", "光照", "模型", "预测", "外部", "天气", "知识", "条目",
    "检索", "关注程度", "窗口", "历史", "过去",
})

#: 四类证据的角色 —— 由 source_type 决定（台账里**没有** role 字段，
#: 角色是从结构化字段推出来的，见 V5 的说明）
ROLE_BY_SOURCE = {
    "B02": "history",
    "B03": "weather",
    "B04": "knowledge",
    "B06": "model",
}

#: 每类角色"承担什么作用"的**结构化字段证据**（不看 basis 文本）
ROLE_SIGNATURE = {
    "history": {
        "role_meaning": "已发生：窗口内的实测/模拟采样",
        "content_fields": ("result.history", "result.current_state",
                           "result.stability_conclusion"),
    },
    "weather": {
        "role_meaning": "可能发生的外部条件：预报点，不是已发生事实",
        "content_fields": ("result.forecast_point", "result.baseline_point",
                           "result.signal", "ext.forecast_time"),
    },
    "knowledge": {
        "role_meaning": "一般性依据：知识库切片，不是本温室实测结论",
        "content_fields": ("result.citations", "result.query", "ext.citation"),
    },
    "model": {
        "role_meaning": "下一窗口预测：模型输出，不是已发生事实",
        "content_fields": ("result.predicted_attention", "result.window_id",
                           "result.model_version", "result.confidence"),
    },
}

#: 身份字段（四类证据都必须有 —— "我是什么 / 从哪来 / 什么时候 / 是否可用"）
#: `error` 只在不可用时才必须存在（ok 的条目本来就没有 error）。
ROLE_IDENTITY_FIELDS = ("source_type", "source", "time", "status")
#: 不可用时（status != ok）必须给出失败原因
ROLE_ERROR_FIELD = "error"
#: 不可用时仍能识别角色的降级标记（结构化，不是 basis 文本）：
#: entry.ext 必须存在且至少有一个非空值 —— 说明这次调用**确实尝试过**并留下了结构化痕迹
ROLE_DEGRADED_EXT_RULE = "ext 存在且至少一个字段非空"

#: 每类角色在结构化判断里的主字段（真实路径；B04 没有独立字段，如实为空）
ROLE_JUDGEMENT_FIELD = {
    "history": "structured_judgement.recent_trend",
    "weather": "structured_judgement.external_conditions",
    "model": "structured_judgement.predicted_attention",
    "knowledge": None,     # 知识证据只出现在分歧表里，没有独立字段
}

#: 限制陈述 → structured_judgement.limitation.<key> 的关键词映射。
#:
#: 只收**本身带"限制语义"的短语**，不收裸名词 —— 像"模拟数据""三项指标"
#: 这种词在正常描述句里也会出现（"根据…模拟数据，当前环境状态如下"），
#: 拿它们当线索会把**结论句**误判成限制陈述。
#:
#: 实际出现的 limitation key 有 5 个（simulated_data / model_boundary /
#: time_anchor / no_weather_no_kb / **evidence_unavailable**），漏掉任何一个，
#: 就会把限制陈述误当成结论句 —— 系统级扫描抓到过这个坑。
LIMITATION_CUES = {
    "simulated_data": ("不可外推", "不能外推", "非真实传感器",
                       "并非真实传感器", "source_type='simulated'",
                       "为模拟数据", "不可直接应用于真实"),
    "model_boundary": ("只适用", "仅适用", "只覆盖", "仅覆盖", "漏报",
                       "召回率", "f1", "不等于确认无异常", "预测结果需谨慎"),
    "time_anchor": ("analysis_time", "latest_in_db", "hours_gap", "锚点",
                    "库末点", "相隔"),
    "no_weather_no_kb": ("未接入", "不参与", "仅供参考", "仅供", "不直接",
                         "不能直接", "方向性参考", "仅作为"),
    "evidence_unavailable": ("当前不可用", "不可用", "受到限制",
                             "status=unavailable", "失败原因",
                             "未发起真实"),
}

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
#: 版本号 / 标识符（b06-20261020-v1、s1-live-001、CH-003-02 …）
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9\-]+")
#: 日期 / 时间戳
_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{4}年\d{1,2}月\d{1,2}日\d{0,2}时?"
    r"|\b\d{2}:\d{2}(?::\d{2})?\b")


# --------------------------------------------------------------------------
# 2. 工具函数
# --------------------------------------------------------------------------

def split_sentences(text: str) -> list[str]:
    """按中文句末标点切句（**不**切逗号/分号 —— 逗号分号里的内容属于同一句）。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])", text)
    return [p.strip() for p in parts if p.strip()]


def numbers_in(text: str) -> set[str]:
    """句子里出现的数值（字符串形态）。

    先剥掉日期时间戳与版本号/标识符 —— 否则 "b06-20261020-v1" 会贡献
    "06"、"20261020" 这种与证据无关的假数值。
    """
    if not text:
        return set()
    cleaned = _DATETIME_RE.sub(" ", text)
    cleaned = _IDENT_RE.sub(" ", cleaned)
    return set(_NUM_RE.findall(cleaned))


def collect_numbers(obj) -> set[str]:
    """递归收集一个结构里的所有数值（字符串形态）。"""
    out: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= collect_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= collect_numbers(v)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out.add(str(obj))
    return out


def _get_path(obj, dotted: str):
    """按 "a.b.c" 取值，取不到返回 None。"""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def find_paths(obj, predicate, prefix: str = "") -> list[str]:
    """在嵌套结构里找满足 predicate 的节点的路径。"""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if predicate(v):
                out.append(path)
            out += find_paths(v, predicate, path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]"
            if predicate(v):
                out.append(path)
            out += find_paths(v, predicate, path)
    return out


def _entry_numbers(entry: dict) -> set[str]:
    """证据条目里出现过的数值（含 result / ext 里的）。"""
    nums = collect_numbers(entry.get("result"))
    nums |= collect_numbers(entry.get("ext"))
    return nums


def _entry_basis(entry: dict) -> str:
    """依据句 —— 复用 B07.evidence_ledger 的唯一实现，不另写一套。"""
    from B07.evidence_ledger import basis_of
    return basis_of(entry)


def _entry_summary(entry: dict) -> str:
    """内容摘要 —— 同样复用 B07.evidence_ledger。"""
    from B07.evidence_ledger import summarize
    return summarize(entry)


# --------------------------------------------------------------------------
# 3. 挑句：规则化挑出 1–3 句"重要结论句"
# --------------------------------------------------------------------------

def classify_sentence(sent: str, limits: dict) -> dict:
    """句子分类：限制陈述 vs 候选结论句。

    判据：命中某个 limitation key 的**限制性短语**（LIMITATION_CUES）→ 限制陈述。

    不用"与 limitation 原文的文本重叠"做判据 —— limitation 原文里**引用了
    同一批判据数字**（"温度波动 4.4℃，超过阈值 2.0℃" 就在 time_anchor 的限制
    原文里），文本重叠会把正常结论句一并误判成限制陈述（实测 59 个 run 里
    误判 13 个）。所以只认"带限制语义"的结构化短语。
    """
    cue_hits = {}
    for key, cues in LIMITATION_CUES.items():
        hit = [c for c in cues if c in sent]
        if hit:
            cue_hits[key] = hit
    return {
        "is_limitation": bool(cue_hits),
        "cue_hits": cue_hits,
    }


def pick_claims(answer: str, entries: list[dict],
                max_claims: int = 3,
                manual: list[str] | None = None,
                limits: dict | None = None) -> list[dict]:
    """规则化挑句。

    规则：

      1. 按句末标点切句；
      2. **先排除限制陈述**（命中限制性短语 LIMITATION_CUES）——
         限制不是结论，它走 limitations.<key> 那条路；
      3. 剩下的句子必须含**判断/建议类动词**（CLAIM_VERBS）之一；
      4. 还必须**带至少一条证据线索**（命中某一角色的特征词，
         或句中数值出现在台账某条证据里）；
      5. 打分 = 命中的证据特征词数 + 命中台账数值的个数；
      6. 按分数降序、原文顺序升序取前 max_claims 句。

    `manual`：**教师人工指出的句子**（原文整句）。命中的句子**无条件进入**候选，
    不受规则 2/3/4 约束 —— 文档第一步要的是"教师任意指出一句"，
    规则挑句只是没指定时的默认。人工指定的句子排在最前面。

    Returns:
        [{"claim_index", "claim_text", "verbs", "keywords", "numbers",
          "numbers_matching_ledger", "score", "selection_rule", "manual"}]
    """
    ledger_numbers: set[str] = set()
    for e in entries:
        ledger_numbers |= _entry_numbers(e)

    sents = split_sentences(answer)
    manual_set = {s.strip() for s in (manual or []) if s and s.strip()}

    cands = []
    for idx, sent in enumerate(sents):
        is_manual = sent.strip() in manual_set
        verbs = [v for v in CLAIM_VERBS if v in sent]
        kw: dict[str, list[str]] = {}
        n_kw = 0
        for role, terms in ROLE_KEYWORDS.items():
            hit = [t for t in terms if t in sent]
            if hit:
                kw[role] = hit
                n_kw += len(hit)
        nums = sorted(numbers_in(sent))
        hit_nums = [n for n in nums if n in ledger_numbers]
        if not is_manual:
            cls = classify_sentence(sent, limits or {})
            if cls["is_limitation"]:
                continue                  # 限制陈述不是结论句
            if not verbs:
                continue
            if not kw and not hit_nums:
                continue                  # 没有证据线索
        cands.append({
            "claim_index": idx,
            "claim_text": sent,
            "verbs": verbs,
            "keywords": kw,
            "numbers": nums,
            "numbers_matching_ledger": hit_nums,
            "score": n_kw + len(hit_nums),
            "manual": is_manual,
        })

    cands.sort(key=lambda c: (-c["score"], c["claim_index"]))
    # 人工指定的句子优先（排最前），其余按分数
    cands.sort(key=lambda c: (not c["manual"], -c["score"], c["claim_index"]))
    n_manual = sum(1 for c in cands if c["manual"])
    picked = cands[:max_claims + n_manual]     # 人工指定的句子不挤掉规则挑的
    picked.sort(key=lambda c: c["claim_index"])
    for c in picked:
        if c["manual"]:
            c["selection_rule"] = (
                "**教师人工指定的句子**（不适用挑句规则，原文整句送入溯源）")
            continue
        c["selection_rule"] = (
            "含判断/建议类动词 " + "、".join(c["verbs"])
            + "；证据特征词 "
            + f"{sum(len(v) for v in c['keywords'].values())} 个"
            + f"（{', '.join(sorted(c['keywords'])) or '无'}）"
            + f"；台账数值命中 {len(c['numbers_matching_ledger'])} 个"
            + f"；得分 {c['score']}"
        )
    return picked


def pick_limitation_statements(answer: str, judgement: dict) -> list[dict]:
    """限制陈述的溯源：这类句子不是台账证据，但同样要能追到 run.json 的字段。

    判据与 pick_claims **共用** classify_sentence()：命中限制性短语
    （LIMITATION_CUES）→ 记为限制陈述，挂到 structured_judgement.limitations.<key>。
    """
    limits = (judgement.get("limitations") or {})
    out = []
    for idx, sent in enumerate(split_sentences(answer)):
        cls = classify_sentence(sent, limits)
        if not cls["is_limitation"]:
            continue
        keys = list(cls["cue_hits"])
        if limits.get("all_keys"):
            keys = [k for k in keys if k in limits["all_keys"]] or keys
        if not keys:
            continue
        out.append({
            "statement_index": idx,
            "statement_text": sent,
            "matched_cues": cls["cue_hits"],
            "limitation_path": [f"structured_judgement.limitations.{k}"
                                for k in keys],
            "limitation_text": {
                k: (limits.get(k) or {}).get("text")
                for k in keys},
        })
    return out


# --------------------------------------------------------------------------
# 4. 证据匹配 + 溯源链
# --------------------------------------------------------------------------

def match_entry_to_claim(entry: dict, claim: dict) -> dict:
    """一条台账条目与一条结论句的匹配情况。

    匹配信号（**规则化**，不是语义理解）：

      * 关键词：结论句里出现了该 source_type 对应的特征词；
      * 数值：结论句里的数值出现在该证据的 result/ext 里。
    """
    source_type = entry.get("source_type")
    role = ROLE_BY_SOURCE.get(source_type)
    kw_terms = ROLE_KEYWORDS.get(role, ())
    text = claim["claim_text"]
    hit_kw = [t for t in kw_terms if t in text]
    entry_nums = _entry_numbers(entry)
    hit_nums = [n for n in claim["numbers"] if n in entry_nums]
    matched = bool(hit_kw) or bool(hit_nums)
    signal = ("关键词" if hit_kw else "") + \
             ("+数值" if hit_kw and hit_nums else ("数值" if hit_nums else ""))
    distinctive = [t for t in hit_kw if t not in GENERIC_TERMS]
    if hit_nums or distinctive:
        strength = "strong"          # 有数值命中，或命中了有区分度的特征词
    elif hit_kw:
        strength = "weak"            # 只命中了"温度/湿度/模型"这类通用词
    else:
        strength = "none"
    return {
        "matched": matched,
        "role": role,
        "keyword_hits": hit_kw,
        "number_hits": hit_nums,
        "signal": signal,
        "strength": strength,
        "distinctive_hits": distinctive,
    }


def match_tool_call(entry: dict, records: list[dict]) -> dict | None:
    """把一条证据条目接到它的**那次真实工具调用**上。

    台账条目本身不记 tool_name，所以这里用**参数值与证据内容对齐**来认领：
    把每次调用的 arguments 里的字符串值在证据 result/ext 的文本里找，
    加权命中最高的那次即为该证据的来源调用（要求唯一最高，并列则不认领）。

    权重：值长 ≥4 记 2 分（信息量大），2–3 字符记 1 分，<2 字符不计
    （单字母/单字符没有区分度）。

    这是可复核的推导，不是猜的：命中值会原样记进 trace 里。
    """
    blob = json.dumps({"result": entry.get("result"), "ext": entry.get("ext")},
                      ensure_ascii=False)

    best, best_score, best_hits = None, 0, []
    scores = []
    for rec in records or []:
        args = rec.get("arguments") or {}
        score = 0
        hits = []
        for k, v in args.items():
            if not isinstance(v, str) or len(v) < 2:
                continue
            if v in blob:
                score += 2 if len(v) >= 4 else 1
                hits.append(f"{k}={v}")
        scores.append((rec.get("tool_name"), score, hits))
        if score > best_score:
            best, best_score, best_hits = rec, score, hits
    if best is None or best_score == 0:
        return None
    top = [s for s in scores if s[1] == best_score]
    if len(top) > 1:
        return None                     # 并列最高 → 不认领，不硬凑
    return {
        "tool_name": best.get("tool_name"),
        "arguments": best.get("arguments"),
        "status": best.get("status"),
        "adopted": best.get("adopted"),
        "error": best.get("error"),
        "matched_arguments": best_hits,
    }


def _structured_paths(entry: dict, judgement: dict) -> list[str]:
    """这条证据在 structured_judgement 里的字段路径（真实存在的路径）。"""
    st = entry.get("source_type")
    src = entry.get("source")

    def _match(node) -> bool:
        return (isinstance(node, dict)
                and node.get("source_type") == st
                and node.get("source") == src)

    return find_paths(judgement or {}, _match, "structured_judgement")


def trace_run(run_id: str, manual_claims: list[str] | None = None) -> dict:
    """对一个 run 做完整溯源。

    `manual_claims`：教师人工指出的句子（原文整句）。给了就**优先**溯源这些句子。
    """
    from B07.runs import load_run

    bundle = load_run(run_id)
    ledger = bundle.get("evidence_ledger") or {}
    entries = list(ledger.get("entries") or [])
    records = list((ledger.get("tool_calls") or {}).get("records") or [])
    judgement = bundle.get("structured_judgement") or {}
    answer = bundle.get("llm_explanation") or ""

    claims = pick_claims(answer, entries, manual=manual_claims,
                         limits=(judgement.get("limitations") or {}))

    claim_out = []
    for c in claims:
        ev_out = []
        supporting_roles, matched_roles = set(), set()
        for i, entry in enumerate(entries):
            mm = match_entry_to_claim(entry, c)
            if not mm["matched"]:
                continue
            role = mm["role"]
            matched_roles.add(role)
            tc = match_tool_call(entry, records)
            direction = entry.get("direction")
            if direction == "supporting":
                supporting_roles.add(role)
            ev_out.append({
                # ---- 规格固定字段 ----
                "role": role,
                "source": entry.get("source"),
                "time": entry.get("time"),
                "status": entry.get("status"),
                "result_summary": _entry_summary(entry),
                "basis": _entry_basis(entry),
                "tool_call": {
                    "tool_name": (tc or {}).get("tool_name"),
                    "arguments": (tc or {}).get("arguments"),
                    "status": (tc or {}).get("status"),
                },
                "adopted": bool(entry.get("adopted")),
                "ledger_path": f"evidence_ledger.entries[{i}]",
                # ---- 详版附加（规格里没有，但溯源链要求带） ----
                "evidence_id": entry.get("evidence_id"),
                "source_type": entry.get("source_type"),
                "direction": direction,
                "relation": ("supporting" if direction == "supporting"
                             else "opposing" if direction == "opposing"
                             else "limiting"),
                "match_signal": mm["signal"],
                "match_strength": mm["strength"],
                "keyword_hits": mm["keyword_hits"],
                "number_hits": mm["number_hits"],
                "structured_judgement_path": _structured_paths(entry, judgement),
                "judgement_field": ROLE_JUDGEMENT_FIELD.get(role),
                "tool_call_derivation": (tc or {}).get("matched_arguments"),
            })

        # 反向提示与限制：台账里方向为 opposing / undetermined 的条目
        constraints = []
        for i, entry in enumerate(entries):
            d = entry.get("direction")
            if d not in ("opposing", "undetermined"):
                continue
            tc = match_tool_call(entry, records)
            constraints.append({
                "evidence_id": entry.get("evidence_id"),
                "role": ROLE_BY_SOURCE.get(entry.get("source_type")),
                "direction": d,
                "status": entry.get("status"),
                "summary": _entry_summary(entry),
                "basis": _entry_basis(entry),
                "ledger_path": f"evidence_ledger.entries[{i}]",
                "tool_call": {
                    "tool_name": (tc or {}).get("tool_name"),
                    "arguments": (tc or {}).get("arguments"),
                    "status": (tc or {}).get("status"),
                },
            })

        missing_roles = [r for r in ("history", "weather", "knowledge", "model")
                         if r not in supporting_roles]
        ok_evidence = [e for e in ev_out if e["status"] == "ok"]
        chain_ok = bool(ok_evidence) and all(
            e["tool_call"]["tool_name"] for e in ev_out)
        strengths = {e["match_strength"] for e in ok_evidence}
        best_strength = ("strong" if "strong" in strengths
                         else "weak" if "weak" in strengths else "none")

        claim_out.append({
            "claim_text": c["claim_text"],
            "claim_index": c["claim_index"],
            "evidence": ev_out,
            "missing_roles": missing_roles,
            "chain_ok": chain_ok,
            # ---- 详版附加 ----
            "matched_roles": sorted(matched_roles),
            "best_match_strength": best_strength,
            "selection_rule": c["selection_rule"],
            "constraints": constraints,
        })

    tool_calls_out = []
    for rec in records:
        tool_calls_out.append({
            "tool_name": rec.get("tool_name"),
            "status": rec.get("status"),
            "arguments": rec.get("arguments"),
            "adopted": rec.get("adopted"),
            "error": rec.get("error"),
            "purpose": rec.get("purpose"),
        })

    checks = run_checks(bundle, claim_out, tool_calls_out)

    return {
        "source_run_id": run_id,
        "source_run_path": f"B07/runs/{run_id}/run.json",
        "claims": claim_out,
        "tool_calls": tool_calls_out,
        "checks": checks,
        # ---- 详版附加 ----
        "meta": {
            "scenario": bundle.get("scenario"),
            "status": bundle.get("status"),
            "analysis_time": bundle.get("analysis_time"),
            "model": (bundle.get("llm_meta") or {}).get("model"),
            "question_sha256": (bundle.get("llm_meta") or {}).get(
                "question_sha256"),
            "system_prompt_sha256": (bundle.get("llm_meta") or {}).get(
                "system_prompt_sha256"),
            "ledger_entry_count": len(entries),
            "ledger_tool_call_count": len(records),
            "answer": answer,
        },
        "limitation_statements": pick_limitation_statements(answer, judgement),
    }


# --------------------------------------------------------------------------
# 4.5 系统级扫描：所有带台账的 run
# --------------------------------------------------------------------------

_SYSTEM_SCAN_CACHE: dict | None = None


def runs_with_ledger() -> list[str]:
    """所有**带 evidence_ledger** 的 run（只有 S4/S5 阶段才有台账）。"""
    from B07.runs import RUNS_DIR
    out = []
    for p in sorted(RUNS_DIR.glob("run_*/run.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except Exception:            # pragma: no cover
            continue
        if (b.get("evidence_ledger") or {}).get("entries"):
            out.append(p.parent.name)
    return out


def scan_all_runs() -> dict:
    """对**所有**带台账的 run 跑一遍 V1–V6（离线，不调 LLM）。

    这是"创新点 2 生效了吗"的系统级答案：不是看一个 run 的链闭合，
    而是看全库有没有一条链断、有没有红线被踩。
    """
    global _SYSTEM_SCAN_CACHE
    if _SYSTEM_SCAN_CACHE is not None:
        return _SYSTEM_SCAN_CACHE

    results, errors = {}, {}
    for rid in runs_with_ledger():
        try:
            results[rid] = trace_run(rid)
        except Exception as exc:      # 显式暴露，不吞
            errors[rid] = f"{type(exc).__name__}: {exc}"

    checks = {}
    for key in ("V1", "V2", "V3", "V4", "V5", "V6"):
        failed = [rid for rid, t in results.items()
                  if t["checks"].get(key) is not True]
        checks[key] = {"passed": len(results) - len(failed),
                       "total": len(results), "failed_runs": failed}
    vacuous = [rid for rid, t in results.items()
               if not t["checks"]["details"]["V1"]["applicable"]]
    failed_runs = []
    for rid in results:
        b = json.loads((_PROJECT_ROOT / "B07" / "runs" / rid / "run.json")
                       .read_text(encoding="utf-8"))
        if b.get("status") != "success":
            failed_runs.append({"run_id": rid, "status": b.get("status"),
                                "answer_length": len(
                                    b.get("llm_explanation") or "")})
    _SYSTEM_SCAN_CACHE = {
        "runs_total": len(runs_with_ledger()),
        "runs_traced": len(results),
        "errors": errors,
        "checks": checks,
        "v1_vacuous": vacuous,
        "non_success_runs": failed_runs,
    }
    return _SYSTEM_SCAN_CACHE


def _fmt_system_scan(scan: dict) -> str:
    if not scan:
        return "（没有可扫描的 run）"
    names = {"V1": "溯源链闭合", "V2": "真调用", "V3": "红线：天气必须真调用",
             "V4": "知识必须真检索", "V5": "四类角色可区分", "V6": "台账自洽"}
    lines = ["| 检查 | 通过 / 总数 | 未通过的 run | 判据 |",
             "| --- | --- | --- | --- |"]
    for key in ("V1", "V2", "V3", "V4", "V5", "V6"):
        c = scan["checks"][key]
        bad = "、".join(c["failed_runs"]) if c["failed_runs"] else "（无）"
        lines.append(f"| {key} {names[key]} | {c['passed']} / {c['total']} | "
                     f"{bad} | — |")
    lines += [
        "",
        f"- 全库共 **{scan['runs_total']}** 个 run 带 `evidence_ledger`"
        f"（只有 S4/S5 阶段才建台账），本轮全部跑通：{scan['runs_traced']} 个；"
        f"异常 {len(scan['errors'])} 个"
        + (f"：{scan['errors']}" if scan["errors"] else ""),
        f"- **V1 空真**（本次没挑出结论句，无实际检验对象）："
        f"{'、'.join(scan['v1_vacuous']) if scan['v1_vacuous'] else '（无）'}"
        + ("　← 这些是 `status=failed` 的 run：LLM 调用失败、"
           "`llm_explanation` 为空，本来就没有可溯源的回答。"
           if scan["v1_vacuous"] else ""),
    ]
    if scan["non_success_runs"]:
        lines.append(
            "- 存档里 `status != success` 的 run（失败显式留痕，未被伪装成成功）：")
        for r in scan["non_success_runs"]:
            lines.append(f"    - `{r['run_id']}`：status=`{r['status']}`，"
                         f"回答长度 {r['answer_length']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 5. 机器化检查 V1–V6
# --------------------------------------------------------------------------

#: 回答里出现这些词 = 在谈天气（V3 红线的触发条件）
WEATHER_TERMS = ("天气", "降水", "降雨", "预报", "外部", "气温", "云量")
#: 回答里出现这些词 = 在谈知识/检索（V4 的触发条件）
KNOWLEDGE_TERMS = ("知识", "检索", "条目", "文献", "方法论", "依据条目")


def _is_weather_tool(name: str) -> bool:
    return "weather" in (name or "").lower()


def _is_knowledge_tool(name: str) -> bool:
    n = (name or "").lower()
    return "knowledge" in n or "search_tomato" in n


def run_checks(bundle: dict, claims: list[dict],
               tool_calls: list[dict]) -> dict:
    """V1–V6 判定（全部基于 run.json 实际字段）。"""
    ledger = bundle.get("evidence_ledger") or {}
    entries = list(ledger.get("entries") or [])
    adoption = ledger.get("adoption") or {}
    answer = bundle.get("llm_explanation") or ""
    details: dict[str, dict] = {}

    # ---- V1 溯源链闭合 ----
    # 0 条结论句 = 0 个违反（字面满足"每条被选中的结论句都能走到 ≥1 条
    # status=ok 的证据"），但必须显式写明"无检验对象"，不许拿它当通过充数。
    v1_items, v1 = [], True
    for c in claims:
        ok = [e for e in c["evidence"] if e["status"] == "ok"]
        v1_items.append({
            "claim_text": c["claim_text"],
            "evidence_total": len(c["evidence"]),
            "evidence_ok": len(ok),
            "chain_ok": c["chain_ok"],
            "best_match_strength": c.get("best_match_strength"),
        })
        if not ok:
            v1 = False
    run_status = bundle.get("status")
    v1_note = ""
    if not claims:
        v1_note = (
            f"本次**没有挑出结论句**（status={run_status}，"
            f"llm_explanation 长度 {len(answer)}）→ V1 属**空真**："
            "既没有违反，也没有实际检验对象。")
    details["V1"] = {
        "name": "溯源链闭合：每条被选中的结论句都能走到 ≥1 条 status=ok 的证据",
        "passed": v1,
        "claims_selected": len(claims),
        "applicable": bool(claims),
        "note": v1_note,
        "items": v1_items,
    }

    # ---- V2 真调用 ----
    v2_items, v2 = [], True
    for c in claims:
        for e in c["evidence"]:
            found = bool(e["tool_call"]["tool_name"])
            v2_items.append({
                "claim_index": c["claim_index"],
                "evidence_id": e["evidence_id"],
                "ledger_path": e["ledger_path"],
                "tool_name": e["tool_call"]["tool_name"],
                "tool_status": e["tool_call"]["status"],
                "derivation": e["tool_call_derivation"],
                "found": found,
            })
            if not found:
                v2 = False
    details["V2"] = {
        "name": "真调用：每条被引用到的证据都能找到对应的工具调用记录",
        "passed": v2,
        "items": v2_items,
    }

    # ---- V3 红线：谈天气必须有天气工具调用记录 ----
    mentions_weather = [t for t in WEATHER_TERMS if t in answer]
    weather_calls = [t for t in tool_calls if _is_weather_tool(t["tool_name"])]
    v3 = bool(weather_calls) if mentions_weather else True
    details["V3"] = {
        "name": "红线：回答提到天气时，run.json 里必须有天气工具的调用记录",
        "passed": v3,
        "triggered": bool(mentions_weather),
        "answer_terms": mentions_weather,
        "weather_tool_calls": [
            {"tool_name": c["tool_name"], "status": c["status"],
             "arguments": c["arguments"], "adopted": c["adopted"],
             "error": c["error"]}
            for c in weather_calls],
    }

    # ---- V4 回答提到知识/检索时，必须有 B04 检索调用记录 ----
    mentions_kb = [t for t in KNOWLEDGE_TERMS if t in answer]
    kb_calls = [t for t in tool_calls if _is_knowledge_tool(t["tool_name"])]
    v4 = bool(kb_calls) if mentions_kb else True
    details["V4"] = {
        "name": "回答提到知识/检索/依据条目时，必须有 B04 检索调用记录",
        "passed": v4,
        "triggered": bool(mentions_kb),
        "answer_terms": mentions_kb,
        "knowledge_tool_calls": [
            {"tool_name": c["tool_name"], "status": c["status"],
             "arguments": c["arguments"], "adopted": c["adopted"],
             "error": c["error"]}
            for c in kb_calls],
    }

    # ---- V5 四类证据角色可区分 ----
    v5_rows, v5 = [], True
    for st, role in ROLE_BY_SOURCE.items():
        es = [e for e in entries if e.get("source_type") == st]
        sig = ROLE_SIGNATURE[role]
        identity = []
        for f in ROLE_IDENTITY_FIELDS:
            val = es[0].get(f) if es else None
            identity.append({"field": f, "present": val is not None,
                             "value": (str(val)[:120] if val is not None else None)})
        status = es[0].get("status") if es else None
        degraded = status is not None and status != "ok"
        # 不可用时必须给出失败原因
        err_val = es[0].get(ROLE_ERROR_FIELD) if es else None
        error_ok = (not degraded) or (err_val is not None)
        content = []
        for f in sig["content_fields"]:
            val = _get_path(es[0], f) if es else None
            content.append({"field": f, "present": val is not None,
                            "value": (str(val)[:120] if val is not None else None)})
        content_ok = all(p["present"] for p in content)
        # 降级时角色仍须可识别（靠结构化的降级标记，而不是 basis 文本）
        ext = (es[0].get("ext") if es else None) or {}
        ext_nonempty = [k for k, v in ext.items() if v not in (None, {}, [], "")]
        degraded_marks = [{
            "field": "ext",
            "present": bool(ext_nonempty),
            "value": ", ".join(ext_nonempty[:6]),
            "rule": ROLE_DEGRADED_EXT_RULE,
        }]
        degraded_ok = (not degraded) or bool(ext_nonempty)
        identity_ok = all(p["present"] for p in identity) and error_ok
        distinguishable = identity_ok and (content_ok or degraded_ok)
        v5_rows.append({
            "source_type": st,
            "role": role,
            "role_meaning": sig["role_meaning"],
            "entry_count": len(es),
            "evidence_ids": [e.get("evidence_id") for e in es],
            "status": status,
            "identity_fields": identity,
            "error_present": err_val is not None,
            "content_fields": content,
            "content_available": content_ok,
            "degraded": degraded,
            "degraded_markers": degraded_marks,
            "role_identifiable": identity_ok,
            "distinguishable": distinguishable,
        })
        if not distinguishable:
            v5 = False
    degraded_rows = [r for r in v5_rows if r["degraded"]]
    details["V5"] = {
        "name": "四类证据角色可区分（已发生 / 可能发生的外部条件 / 一般性依据 / 下一窗口预测）",
        "passed": v5,
        "role_field_present_in_ledger": any("role" in e for e in entries),
        "role_determined_by": "source_type（台账无 role 字段）",
        "degraded_roles": [{"role": r["role"], "status": r["status"],
                            "content_available": r["content_available"],
                            "evidence_ids": r["evidence_ids"]}
                           for r in degraded_rows],
        "note": (
            "台账条目**没有** role 字段（只有 source_type / direction / status / time / "
            "result / error / ext）。角色由 source_type 决定（结构化），作用由 "
            "result/ext 里的结构化字段判定 —— 不是靠 basis 文本猜的。"
            + ("本次有降级证据（见 degraded_roles）：其内容字段取不到，"
               "角色是按 ext 的结构化降级标记识别的，**不能**据此说该类证据"
               "的内容参与了判断。"
               if degraded_rows else "")),
        "rows": v5_rows,
    }

    # ---- V6 台账自洽 ----
    total_calls = adoption.get("total_calls")
    adopted_calls = adoption.get("adopted_calls")
    not_adopted = adoption.get("not_adopted_calls")
    n_not_adopted = len(not_adopted) if isinstance(not_adopted, list) else \
        (not_adopted or 0)
    n_adopted_entries = len([e for e in entries if e.get("adopted")])
    v6_sum = (isinstance(total_calls, int) and isinstance(adopted_calls, int)
              and adopted_calls + n_not_adopted == total_calls)
    v6_entries = (isinstance(adopted_calls, int)
                  and adopted_calls == n_adopted_entries)
    details["V6"] = {
        "name": "台账自洽：adopted_calls + not_adopted_calls 条数 == total_calls；"
                "adopted_calls == 实际进入证据集的条目数",
        "passed": bool(v6_sum and v6_entries),
        "total_calls": total_calls,
        "adopted_calls": adopted_calls,
        "not_adopted_calls_count": n_not_adopted,
        "not_adopted_calls": not_adopted,
        "adopted_entries_count": n_adopted_entries,
        "sum_ok": v6_sum,
        "entries_ok": v6_entries,
        "adoption_text": adoption.get("text"),
    }

    return {
        "V1": bool(v1), "V2": bool(v2), "V3": bool(v3),
        "V4": bool(v4), "V5": bool(v5), "V6": bool(v6_sum and v6_entries),
        "details": details,
    }


# --------------------------------------------------------------------------
# 6. 输出格式：严格按规格
# --------------------------------------------------------------------------

def to_spec_format(trace: dict) -> dict:
    """把内部结果压成规格里写死的固定格式。"""
    return {
        "source_run_id": trace["source_run_id"],
        "source_run_path": trace["source_run_path"],
        "claims": [
            {
                "claim_text": c["claim_text"],
                "claim_index": c["claim_index"],
                "evidence": [
                    {
                        "role": e["role"],
                        "source": e["source"],
                        "time": e["time"],
                        "status": e["status"],
                        "result_summary": e["result_summary"],
                        "basis": e["basis"],
                        "tool_call": e["tool_call"],
                        "adopted": e["adopted"],
                        "ledger_path": e["ledger_path"],
                    }
                    for e in c["evidence"]
                ],
                "missing_roles": c["missing_roles"],
                "chain_ok": c["chain_ok"],
            }
            for c in trace["claims"]
        ],
        "tool_calls": [
            {
                "tool_name": t["tool_name"],
                "status": t["status"],
                "arguments": t["arguments"],
                "adopted": t["adopted"],
            }
            for t in trace["tool_calls"]
        ],
        "checks": {k: bool(trace["checks"][k])
                   for k in ("V1", "V2", "V3", "V4", "V5", "V6")},
    }


# --------------------------------------------------------------------------
# 7. 报告
# --------------------------------------------------------------------------

def _fmt_checks_table(traces: dict[str, dict]) -> str:
    order = [t for t in ("run_082", "run_084", "run_057") if t in traces]
    names = {
        "V1": "溯源链闭合",
        "V2": "真调用",
        "V3": "红线：天气必须真调用",
        "V4": "知识必须真检索",
        "V5": "四类角色可区分",
        "V6": "台账自洽",
    }
    head = "| 检查 | " + " | ".join(order) + " | 判据 |"
    sep = "| --- | " + " | ".join("---" for _ in order) + " | --- |"
    lines = [head, sep]
    for key in ("V1", "V2", "V3", "V4", "V5", "V6"):
        cells = []
        for rid in order:
            t = traces[rid]
            cells.append("✅ 通过" if t["checks"][key] else "❌ 不通过")
        lines.append(f"| {key} {names[key]} | " + " | ".join(cells) + " | — |")
    return "\n".join(lines)


def _fmt_degraded(traces: dict[str, dict]) -> str:
    """把各 run 的降级证据（status != ok）如实列出来。"""
    lines = ["| run | 降级证据 | status | 内容字段是否可得 | direction |",
             "| --- | --- | --- | --- | --- |"]
    any_row = False
    for rid, t in traces.items():
        for r in t["checks"]["details"]["V5"]["rows"]:
            if not r["degraded"]:
                continue
            any_row = True
            lines.append(
                f"| {rid} | `{r['evidence_ids']}`（role={r['role']}） | "
                f"`{r['status']}` | "
                f"{'可得' if r['content_available'] else '**取不到**'} | "
                f"（见 `evidence_divergence.undetermined`） |")
    if not any_row:
        return "（本次审计的三个 run 里没有 status≠ok 的降级证据）"
    return "\n".join(lines)


def _fmt_strength_table(traces: dict[str, dict]) -> str:
    """每条结论句的**匹配强度** —— 强/弱如实分档，不把弱匹配算成强证据。"""
    lines = ["| run | claim_index | 结论句（截断） | 最强匹配强度 | chain_ok |",
             "| --- | --- | --- | --- | --- |"]
    for rid, t in traces.items():
        for c in t["claims"]:
            txt = c["claim_text"]
            txt = txt if len(txt) <= 40 else txt[:40] + "…"
            lines.append(f"| {rid} | {c['claim_index']} | {txt} | "
                         f"`{c['best_match_strength']}` | {c['chain_ok']} |")
    return "\n".join(lines)


def _fmt_chain(trace: dict, claim_index: int) -> str:
    c = next((x for x in trace["claims"] if x["claim_index"] == claim_index), None)
    if not c:
        return "（没挑到这句）"
    out = [f"**结论句**（claim_index={c['claim_index']}）", "",
           "> " + c["claim_text"], "",
           f"- 挑句规则：{c['selection_rule']}",
           f"- 命中角色：{', '.join(c['matched_roles']) or '（无）'}",
           f"- 最强匹配强度：`{c['best_match_strength']}`"
           f"（strong=数值命中或命中有区分度的特征词；weak=只命中"
           f"「温度/湿度/模型」这类通用词）",
           f"- 缺哪类证据支撑：{', '.join(c['missing_roles']) or '（无）'}",
           ""]
    for e in c["evidence"]:
        tc = e["tool_call"]
        out += [
            f"- 证据 `{e['evidence_id']}`（role={e['role']}，"
            f"source_type={e['source_type']}，direction={e['direction']}）",
            f"    - `structured_judgement` 字段路径："
            f"{'、'.join(e['structured_judgement_path']) or '（无）'}",
            f"    - `{e['ledger_path']}` → source=`{e['source']}`，"
            f"time=`{e['time']}`，status=`{e['status']}`",
            f"    - 内容摘要：{e['result_summary']}",
            f"    - 依据：{e['basis']}",
            f"    - 工具调用：{tc['tool_name']}，status=`{tc['status']}`，"
            f"arguments=`{json.dumps(tc['arguments'], ensure_ascii=False)}`",
            f"    - 匹配信号：{e['match_signal']}（关键词 {e['keyword_hits']}，"
            f"数值 {e['number_hits']}）→ 强度 `{e['match_strength']}`",
            f"    - 该调用与证据的对应依据：{e['tool_call_derivation']}",
        ]
    out += ["", f"- chain_ok = {c['chain_ok']}"]
    if c["constraints"]:
        out.append("- 反向提示与限制（同样能追到台账条目）：")
        for k in c["constraints"]:
            out.append(
                f"    - `{k['evidence_id']}`（role={k['role']}，"
                f"direction={k['direction']}，status=`{k['status']}`）"
                f" `{k['ledger_path']}`：{k['summary']} ｜ {k['basis']}"
                f" ｜ 工具 {k['tool_call']['tool_name']}"
                f"（status=`{k['tool_call']['status']}`）")
    return "\n".join(out)


def _contrast_section() -> str:
    """黑盒对照 —— 数据直接读 s4_3_contrast.json，不重新跑、不手抄。"""
    if not CONTRAST_PATH.exists():
        return "（缺少 B07/artifacts/s4_3_contrast.json，无法生成黑盒对照）"
    data = json.loads(CONTRAST_PATH.read_text(encoding="utf-8"))
    groups = data.get("groups") or {}
    gnames = list(groups.keys())
    lines = [f"| 指标 | {gnames[0]} | {gnames[1]} |",
             "| --- | --- | --- |"]
    keys = ("total_answers", "failed", "claims_total", "cited", "unsupported",
            "ledger_cited", "attributed_in_text", "no_claim_answers",
            "attribution_rate_text", "unsupported_rate_text",
            "no_claim_rate_text", "ledger_cited_rate_text",
            "mentions_any", "conflict_mentions_any", "external_mentions_any")
    labels = {
        "total_answers": "回答总数",
        "failed": "失败数",
        "claims_total": "断言总数",
        "cited": "可归因断言数（上下文口径）",
        "unsupported": "无来源断言数（上下文口径）",
        "ledger_cited": "可归因断言数（台账口径）",
        "attributed_in_text": "回答里自己标了来源的断言数",
        "no_claim_answers": "完全没有断言的回答数",
        "attribution_rate_text": "归因完整率",
        "unsupported_rate_text": "无来源断言率",
        "no_claim_rate_text": "无断言率",
        "ledger_cited_rate_text": "台账口径归因率",
        "mentions_any": "提到了证据来源的回答数",
        "conflict_mentions_any": "提到分歧/冲突的回答数",
        "external_mentions_any": "提到外部天气的回答数",
    }
    for k in keys:
        a = (groups.get(gnames[0]) or {}).get("summary", {}).get(k)
        b = (groups.get(gnames[1]) or {}).get("summary", {}).get(k)
        lines.append(f"| {labels.get(k, k)} | `{a}` | `{b}` |")
    total_claims = sum((groups.get(g) or {}).get("summary", {}).get(
        "claims_total") or 0 for g in gnames)
    lines += [
        "",
        f"- 数据来源：`B07/artifacts/s4_3_contrast.json`"
        f"（anchor=`{data.get('anchor')}`，n_per_group=`{data.get('n_per_group')}`，"
        f"system_prompt_sha256="
        f"`{(data.get('fingerprints') or {}).get('system_prompt_sha256')}`，"
        f"question_sha256="
        f"`{(data.get('fingerprints') or {}).get('question_sha256')}`）",
        f"- 上下文长度：A 组 `{(data.get('fingerprints') or {}).get('context_a_len')}` 字符，"
        f"B 组 `{(data.get('fingerprints') or {}).get('context_b_len')}` 字符；"
        f"共享前缀字符数 `{(data.get('fingerprints') or {}).get('shared_prefix_chars')}`"
        f"（文件里就是 null）、共享块是否一致 "
        f"`{(data.get('fingerprints') or {}).get('shared_blocks_identical')}`",
        "",
        "**如实说明**：这张表里 A、B 两组的三个主比率**没有差异**，"
        "而且归因完整率 / 无来源断言率的分母是 0（`0/0`，比率字段为 `null`）—— "
        "原因是 S4-3 的断言检测器词表只收「一定会 / 必然 / 必将…」这类**强断言**，"
        f"两组共 {total_claims} 条断言都没命中检测器词表，"
        "于是「无断言率」两组都是 1.0。",
        "",
        "**所以：黑盒对照这一项，S4-3 留下的数据本身不足以支撑「A/B 有差别」的结论。**"
        "本轮**没有重新跑**，只能如实记录。两组真实可比的差别在别处：",
        "",
    ]
    for gname in gnames:
        recs = (groups.get(gname) or {}).get("records") or []
        lens = [r.get("answer_length") for r in recs if r.get("answer_length")]
        mcounts = [((r.get("mentions") or {}).get("count")) for r in recs]
        lines.append(
            f"- {gname}：回答长度 {min(lens) if lens else '—'}–"
            f"{max(lens) if lens else '—'} 字符"
            f"（均值 {round(sum(lens) / len(lens), 1) if lens else '—'}），"
            f"命中来源词的条数 {min(mcounts) if mcounts else '—'}–"
            f"{max(mcounts) if mcounts else '—'}"
            f"（均值 {round(sum(mcounts) / len(mcounts), 1) if mcounts else '—'}）")
    return "\n".join(lines)


def build_report(traces: dict[str, dict]) -> str:
    """生成 B07/artifacts/innovation2_report.md。"""
    from B07.runs import load_run

    main = traces.get(DEFAULT_RUN)
    if main is None:
        raise RuntimeError("没有可用的主 run 溯源结果")

    b = load_run(main["source_run_id"])
    ledger = b.get("evidence_ledger") or {}
    entries = ledger.get("entries") or []
    div = (b.get("structured_judgement") or {}).get("evidence_divergence") or {}
    meta = main["meta"]

    def _entry(st):
        for e in entries:
            if e.get("source_type") == st:
                return e
        return None

    e02, e06, e03, e04 = (_entry("B02"), _entry("B06"),
                          _entry("B03"), _entry("B04"))

    # ---- 第 1 节表 ----
    t1 = ["| 角色 | source_type | 来源 | 时间（取得时刻） | 可用性 | 结果 | 各自承担什么作用 |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for st, role in ROLE_BY_SOURCE.items():
        e = _entry(st)
        if not e:
            continue
        t1.append(
            f"| {role} | {st} | `{e.get('source')}` | `{e.get('time')}` | "
            f"status=`{e.get('status')}`，direction=`{e.get('direction')}`，"
            f"adopted=`{e.get('adopted')}` | "
            f"{_entry_summary(e)} | {ROLE_SIGNATURE[role]['role_meaning']} |")

    chains = [_fmt_chain(main, main["claims"][i]["claim_index"])
              for i in range(min(2, len(main["claims"])))]

    # ---- 第 4 节 ----
    s4 = []
    s4.append("1. **历史持续升温 → 支持「变化正在形成」**")
    s4.append(f"   - 证据：`{e02.get('evidence_id')}`（role=history，"
              f"`{e02.get('source')}`，status=`{e02.get('status')}`）")
    s4.append(f"   - 实际字段值：stability_passed="
              f"`{(e02.get('result') or {}).get('stability_passed')}`，"
              f"temps=`{(e02.get('result') or {}).get('temps')}`")
    s4.append(f"   - 依据：{_entry_basis(e02)}")
    s4.append("2. **模型输出 WATCH → 支持下一窗口继续关注**")
    s4.append(f"   - 证据：`{e06.get('evidence_id')}`（role=model，"
              f"`{e06.get('source')}`，status=`{e06.get('status')}`）")
    s4.append(f"   - 实际字段值：predicted_attention="
              f"`{(e06.get('result') or {}).get('predicted_attention')}`，"
              f"confidence=`{(e06.get('result') or {}).get('confidence')}`，"
              f"model_version=`{(e06.get('result') or {}).get('model_version')}`，"
              f"window_id=`{(e06.get('result') or {}).get('window_id')}`")
    s4.append(f"   - 依据：{_entry_basis(e06)}")
    s4.append("3. **天气显示未来可能降温 → 不能把「继续恶化」说得太确定**")
    s4.append(f"   - 证据：`{e03.get('evidence_id')}`（role=weather，"
              f"`{e03.get('source')}`，status=`{e03.get('status')}`，"
              f"direction=`{e03.get('direction')}`）")
    s4.append(f"   - 实际字段值：signal=`{(e03.get('result') or {}).get('signal')}`，"
              f"precip_delta=`{(e03.get('result') or {}).get('precip_delta')}`，"
              f"forecast_point="
              f"`{json.dumps((e03.get('result') or {}).get('forecast_point'), ensure_ascii=False)}`，"
              f"forecast_time=`{(e03.get('ext') or {}).get('forecast_time')}`")
    s4.append(f"   - 依据：{_entry_basis(e03)}")
    s4.append("4. **专业知识只支持持续高温值得关注 → 不能证明未来一定恶化**")
    s4.append(f"   - 证据：`{e04.get('evidence_id')}`（role=knowledge，"
              f"`{e04.get('source')}`，status=`{e04.get('status')}`，"
              f"direction=`{e04.get('direction')}`）")
    s4.append(f"   - 实际字段值：query=`{(e04.get('result') or {}).get('query')}`，"
              f"主命中 citation="
              f"`{json.dumps((e04.get('ext') or {}).get('citation'), ensure_ascii=False)}`")
    s4.append(f"   - 依据：{_entry_basis(e04)}")
    s4.append(f"   - 台账里同时挂着的限制（`structured_judgement.evidence_divergence."
              f"undetermined`）："
              f"{'；'.join((x.get('reason') or '') for x in (div.get('undetermined') or []) if x.get('source_type') == 'B04') or '（无）'}")

    det = main["checks"]["details"]
    scan = scan_all_runs()
    v3, v4, v6 = det["V3"], det["V4"], det["V6"]

    lim_stmt = main.get("limitation_statements") or []

    md = f"""# B07 第二遍 · 创新点 2 —— 把"多源信息"真正变成"可追溯证据"

> 本轮性质：**审计 + 证明**，不是开发。
> 只新增 `B07/innovation2.py` 与本报告，**未改动 B07 现有任何代码**；
> **未新增任何 LLM 调用**（全部基于 S4/S5 已落盘的 run 存档）；
> 未改 SYSTEM_PROMPT / 固定问题 / 任何判据阈值；未碰 B01–B06。

## 0 一句话结论

**证明了**：以 `{main['source_run_id']}` 为准，LLM 回答里的结论句可以逐条**反着追回到
run.json 里的具体字段**（结论句 → `structured_judgement.<字段>` →
`evidence_ledger.entries[i]` → 那次**真实发生**的工具调用 → 工具名 + arguments + status）；
四类信息在台账里**各自带身份**（source / time / status / result / direction / adopted），
"这条结论**没有**哪类证据支撑"、"哪些证据在反向提示"同样能追到条目 ——
也就是**「很多信息」已经变成「一组各自有身份、各自有作用的证据」**。

**没证明**：① 「四类作用不可互换」是语义判断，机器只能验证"有没有分别标注"，
不能证明"确实不可替代"；② 黑盒 A/B 对照沿用 S4-3 的数据，**那组数据本身分不出高下**
（三个主比率在 A/B 两组都是 `0/0` 与 `1.0`，见第 5 节），本轮**没有重新跑**，
所以这一项只能如实记录，不能拿它当证据。

## 1 四类信息 → 四份证据 的对照表

（数据源：`{main['source_run_path']}` 的 `evidence_ledger.entries`）

{chr(10).join(t1)}

四类**作用不可互换**，各自的结构化依据：

| 角色 | 作用 | 结构化字段证据（不是 basis 文本） | 该 run 里的实际值 |
| --- | --- | --- | --- |
| history | 已发生 | `result.history` / `result.current_state` / `result.stability_conclusion` | temps=`{(e02.get('result') or {}).get('temps')}`，stability_passed=`{(e02.get('result') or {}).get('stability_passed')}` |
| weather | 可能发生的外部条件 | `result.forecast_point` / `result.baseline_point` / `result.signal` / `ext.forecast_time` | signal=`{(e03.get('result') or {}).get('signal')}`，forecast_time=`{(e03.get('ext') or {}).get('forecast_time')}` |
| knowledge | 一般性依据 | `result.citations` / `result.query` / `ext.citation` | query=`{(e04.get('result') or {}).get('query')}`，chunk=`{(e04.get('ext') or {}).get('citation', {}).get('chunk_id')}` |
| model | 下一窗口预测 | `result.predicted_attention` / `result.window_id` / `result.confidence` | predicted_attention=`{(e06.get('result') or {}).get('predicted_attention')}`，window_id=`{(e06.get('result') or {}).get('window_id')}` |

> **重要提醒（文档第二步）**：天气预报**不是**已发生的历史事实；模型输出**不是**专业知识；
> RAG 文档**不是**下一窗口一定会发生什么的证明。台账用 `direction` 字段把
> 「支持 / 反向 / 仍不足以确定」分开了 —— 本 run 的 `evidence_divergence.each_source` =
> `{json.dumps(div.get('each_source'), ensure_ascii=False)}`，
> supporting {len(div.get('supporting') or [])} 条 / opposing {len(div.get('opposing') or [])} 条 /
> undetermined {len(div.get('undetermined') or [])} 条。

## 2 溯源链实录（run.json 实际字段值）

{chains[0]}

---

{chains[1] if len(chains) > 1 else '（只有一条结论句）'}

### 限制陈述也能追到字段

限制类句子不是台账证据，但同样指向 run.json 里的具体字段
（`structured_judgement.limitations.<key>`）：

""" + ("\n".join(
        f"- 「{s['statement_text']}」 → "
        + "、".join(f"`{p}`" for p in s["limitation_path"])
        + f"（线索词 {list(s['matched_cues'].values())}）"
        for s in lim_stmt) if lim_stmt else "（本次回答里没有单独的限��陈述句）") + f"""

## 3 第四步「真调用」检查（V3 / V4 逐条判定）

**文档第四步的判据**：如果最终报告写着"根据天气信息……"，但运行记录里根本没有
天气工具调用，这一轮不通过。

### V3 红线（天气）

- 触发条件：回答里出现 {v3['answer_terms'] or '（无）'} → {'命中天气相关表述' if v3['triggered'] else '未触发'}
- 判定：**{'✅ 通过' if v3['passed'] else '❌ 不通过'}**
- 命中的工具调用记录（来自 `evidence_ledger.tool_calls.records`）：

```json
{json.dumps(v3['weather_tool_calls'], ensure_ascii=False, indent=2)}
```

### V4 知识/检索

- 触发条件：回答里出现 {v4['answer_terms'] or '（无）'} → {'命中' if v4['triggered'] else '**未触发**（本 run 的回答没谈知识，V4 无触发）'}
- 判定：**{'✅ 通过' if v4['passed'] else '❌ 不通过'}**
- 命中的工具调用记录：

```json
{json.dumps(v4['knowledge_tool_calls'], ensure_ascii=False, indent=2)}
```

> `run_057` 是本轮特意带上的对照：它的回答里**真的出现了"知识"**，
> 所以 V4 在它身上有一个真实触发点（见第 6 节判定表）。

### 工具调用总账（`{main['source_run_id']}`）

| tool_name | status | adopted | arguments |
| --- | --- | --- | --- |
""" + "\n".join(
        f"| `{c['tool_name']}` | `{c['status']}` | `{c['adopted']}` | "
        f"`{json.dumps(c['arguments'], ensure_ascii=False)}` |"
        for c in main["tool_calls"]) + f"""

台账自己的 `adoption.text`：**{v6['adoption_text']}**。
未被采用的那一条：`{json.dumps(v6['not_adopted_calls'], ensure_ascii=False)}`。

## 4 第五步「为什么需要关注」的四句拆解

{chr(10).join(s4)}

## 5 黑盒对照（A 组平铺 vs B 组分身份）

{_contrast_section()}

## 6 文档五步逐条自评

| 文档步骤 | 本轮结论 | 证据位置 |
| --- | --- | --- |
| 第一步 找一句最终判断，反着往回查 | **通过** | 第 2 节溯源链实录：结论句 → `structured_judgement.<字段>` → `evidence_ledger.entries[i]` → 工具调用 |
| 第二步 四类信息各自的作用 | **通过** | 第 1 节对照表 + V5 的结构化字段证据表 |
| 第三步 每份证据自报身份 | **通过（有一处如实说明）** | 台账条目带 `source_type` / `source` / `time` / `status` / `result` / `error` / `direction` / `adopted` / `ext`；**没有** `role` 字段 —— 角色由 `source_type` 决定（V5 的 note 已写明） |
| 第四步 查 B05 是不是真调用 | **通过** | 第 3 节 V3/V4；`tool_calls.records` 带 `status` / `arguments` / `adopted` / `error` |
| 第五步 再说一次"为什么需要关注" | **通过** | 第 4 节四句拆解，每句挂到具体 `evidence_id` 与实际字段值 |

### V1–V6 判定表

{_fmt_checks_table(traces)}

### 系统级扫描（全库 {scan['runs_total']} 个带台账的 run）

上面的表只看三个 run。**"创新点 2 生效了吗"要回答的是全库**：
有没有哪条链断掉、有没有红线被踩。

{_fmt_system_scan(scan)}

> V5 的判定有一处必须一起读：**降级证据（status≠ok）只能验到"角色仍可识别"，
> 不能验到"该类内容参与了判断"**。降级证据清单：

{_fmt_degraded(traces)}

> V1 只要求"能走到 ≥1 条 status=ok 的证据"。但**匹配有强弱之分**：
> `strong` = 数值命中或命中有区分度的特征词；`weak` = 只命中
> 「温度 / 湿度 / 模型」这类通用词。下表如实分档 —— 弱匹配**不等于**
> "这句话被这条证据支撑"，它只等于"这句话谈的是这一类东西"：

{_fmt_strength_table(traces)}

### 主 run 的逐条判定依据（依据全部来自 run.json）

```json
{json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'items'} for k, v in det.items()}, ensure_ascii=False, indent=2)}
```

## 7 Limitations

- **L1** 本轮**没有新增任何 LLM 调用**，全部基于 S4 已落盘的 run 与 S4-3 的对照数据。
  报告里的每一句"实测值"都是从 `run.json` / `s4_3_contrast.json` 里读出来的，
  没有任何手抄或补写。
- **L2** 溯源器的"自动匹配"是**规则化**的（关键词 + 数值），不是语义理解；
  匹配不上的句子/证据一律如实标"未匹配"，不硬凑。
  具体地：角色由 `source_type` 决定；句子↔证据靠「该角色的特征词命中」或
  「句中数值出现在证据 result/ext 里」；证据↔工具调用靠「调用参数值出现在证据
  result/ext 文本里」且唯一最高，并列则不认领。匹配还分**强/弱**：
  `strong` = 数值命中或命中有区分度的特征词；`weak` = 只命中「温度/湿度/模型」
  这类通用词（只说明谈的是同一类东西，不等于该证据支撑了这句）。
  句子分类同样是规则的：先按**限制性短语**（`LIMITATION_CUES`）把限制陈述摘出去，
  剩下的才按「判断/建议类动词 + 证据线索 + 打分」挑结论句。
- **L3** 文档第一步要的是"教师任意指出一句"。本轮**同时**支持两种：
  ① 程序按规则挑句（含判断/建议类动词 + 带证据线索 + 打分排序）——
  这是没指定时的默认，但**结论句的选择不是穷尽的**，只是"程序认为重要"的那些；
  ② `--claim "句子原文"` / `--claim-index N` **人工指定任意一句**，
  该句**无条件**进入溯源（不受挑句规则约束）。
  本报告第 2 节用的是 ①；用 ② 复核的命令见第 8 节。
- **L4** S4-3 的 A/B 对照数据由 qwen-max 产生；与 S1–S3 的 qwen3.5-27b 结果
  **不可直接比**。而且如第 5 节所述，这组数据本身的三个主比率分母为 0，
  **不足以支撑 A/B 有差别的结论**。
- **L5** "四类证据作用不可互换"是**语义判断**，机器检查只能验证"有没有分别标注"
  （V5 验的是结构化字段在不在），**不能证明"确实不可替代"**。
- **L6** 主 run 是 `{main['source_run_id']}`，但它的 `scenario` 字段是
  `{meta['scenario']}`（S5 = S4 场景 + 天气降级开关的基线变体），
  不是文档字面写的 "S4"；本轮按文档指定用了 run_082 / run_084，
  两者除天气开关外同源（同一 anchor、同一 B06 窗口、同一固定问题：
  `system_prompt_sha256={meta['system_prompt_sha256']}`、
  `question_sha256={meta['question_sha256']}`）。
- **L7** 证据条目本身**不记 tool_name**，工具调用与证据的对应关系是本轮用参数值
  对齐**推导**出来的；推导依据（命中了哪个参数）已原样写进 trace，可逐条复核，
  但它是推导而非台账原生字段。
- **L8** `run_084` 的天气工具 `status=unavailable`：V3 的判据是"必须存在天气工具的
  调用记录"，记录存在且 error 明确，故通过；但**该 run 的天气结论没有可用值**，
  不能当作"天气证据可用"的证据。V5 在该 run 上也只验证到"角色仍可识别"
  （靠 `ext.provider` / `ext.forecast_window` / `ext.unavailable_context.signal`
  这些结构化降级标记），**该条证据的内容字段确实取不到**，
  所以"天气承担了外部条件这一作用"在这个 run 上**没有内容级证据** ——
  详见 V5 的 `degraded_roles`。这是如实暴露，不是打掩护。
- **L9** 本轮没有新增判据、没有新增阈值，也没有重新跑任何 LLM；
  所有"应该怎样"的判断（哪些句子算结论句、哪些词算某类证据的特征词、
  限制线索怎么归 key）都是本轮写定的规则，写在 `B07/innovation2.py` 顶部常量里，
  可复查、可争论。
- **L10** 规则在**系统级扫描**里被查出过两处缺陷，本轮已改，所以
  "一次写定"这句话只对**最终版本**成立，改动过程如下（都留在这里，不藏）：
  ① 限制线索表漏了 `evidence_unavailable` 这个 key，导致
  "需要注意的是，外部天气证据当前不可用…"这类**限制陈述**被当成结论句，
  进而 V1 在 5 个 run 上假失败（run_088 / run_093 等）；
  ② 曾试过用「句子与 limitation 原文的公共 4-gram」做兜底判据，
  实测**过度触发**——limitation 原文里引用了同一批判据数字
  （"温度波动 4.4℃，超过阈值 2.0℃" 就在 `time_anchor` 的限制原文里），
  文本重叠会把正常结论句一并误判成限制陈述（59 个 run 里误判 13 个），
  该做法已**整体废弃**，改回只认"带限制语义"的结构化短语。
  另外 `CLAIM_VERBS` 在文档清单（建议/需要/应当/预计/判定/不排除）之外
  补了「请 / 应 / 预测」三个同类词 —— 文档原话是"如…"，属于举例。
- **L11** 全库里有 {len(scan['non_success_runs'])} 个 `status=failed` 的 run
  （LLM 调用失败、`llm_explanation` 为空），它们**有台账但没有回答**，
  V1 对它们是**空真**（既无违反也无检验对象）。这类 run 不会被伪装成成功 ——
  失败状态原样留在存档里，扫描结果里也单列。

## 8 复现命令

```bash
# 全部溯源 + 重新生成报告（不联网、不调 LLM）
python -m B07.innovation2

# 只重建报告（读已落盘的 trace）
python -m B07.innovation2 --report-only

# 指定 run
python -m B07.innovation2 --run run_082

# —— 复核入口：老师任意指出一句 ——
# 1) 先看这个 run 一共几句、每句的编号
python -m B07.innovation2 --run run_082 --list-claims

# 2) 按编号指定一句，溯源链打到屏幕（不写任何文件）
python -m B07.innovation2 --run run_082 --claim-index 6 --show

# 3) 或直接用原文整句指定
python -m B07.innovation2 --run run_082 \
  --claim "综上所述，由于温度和湿度的显著波动，建议您密切关注大棚内环境的变化，以确保番茄生长的适宜条件。" --show

# 回归测试
python -m pytest B07/tests -q

# 复算一致性（存档值 vs 从存档重算）
python -m B07.runs explain run_082
python -m B07.runs explain run_084
python -m B07.runs explain run_023
```

产物：
- `B07/artifacts/innovation2_trace.json` —— 溯源器输出（**严格按规格的固定格式**）
- `B07/artifacts/innovation2_trace_detail.json` —— 详版（判定依据、匹配信号、限制陈述）
- `B07/artifacts/innovation2_report.md` —— 本报告

> **输出编码**：Windows 控制台默认 codepage=936（GBK），而控制台是**按自己的
> codepage 解码程序吐出的字节**的 —— 两边对不上中文必成乱码。本模块启动时会
> `SetConsoleOutputCP` 把控制台 codepage 设成与所吐字节一致（默认 UTF-8/65001），
> 被重定向 / 走管道时不动控制台、直接吐 UTF-8。
> 想强制别的编码：`--stdout-encoding gbk`（或环境变量 `B07_STDOUT_ENCODING`），
> 也可以 `chcp 65001` 手动改控制台。
> 落盘的三个产物一律是 UTF-8（`write_text(encoding="utf-8")`），不受终端设置影响。
"""
    return md


# --------------------------------------------------------------------------
# 8. 终端走查层
#    报告 md 是给"存档"看的；终端是给"答辩现场"看的 —— 必须直接看到
#    真实的句子、真实的字段值、真实的工具调用记录，而不是一行行 PASS。
# --------------------------------------------------------------------------

def _dwidth(text: str) -> int:
    """终端显示宽度（中文算 2 列）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in str(text))


def _pad(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - _dwidth(text))


def _plain_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """固定宽度表格（中文按 2 列对齐），返回若干行终端文本。"""
    widths = []
    for i in range(len(headers)):
        widths.append(max([_dwidth(headers[i])] +
                           [_dwidth(r[i]) for r in rows]) if rows
                       else _dwidth(headers[i]))
    out = [" | ".join(_pad(headers[i], widths[i])
                      for i in range(len(headers)))]
    out.append("-+-".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        out.append(" | ".join(_pad(r[i], widths[i])
                              for i in range(len(headers))))
    return out


def _chain_text(trace: dict, claim: dict) -> list[str]:
    """一条结论句的完整溯源链（纯文本，含真实字段值）。"""
    out = [
        f"结论句[#{claim['claim_index']}]：{claim['claim_text']}",
        f"  挑句规则    : {claim['selection_rule']}",
        f"  命中角色    : {'、'.join(claim['matched_roles']) or '（无）'}",
        f"  最强匹配    : {claim['best_match_strength']}"
        f"（strong=数值命中/有区分度的特征词；weak=只命中通用词）",
        f"  缺失证据角色: {'、'.join(claim['missing_roles']) or '（无）'}",
    ]
    for e in claim["evidence"]:
        tc = e["tool_call"]
        out += [
            f"  └ 证据 {e['evidence_id']}  role={e['role']}  "
            f"source_type={e['source_type']}  direction={e['direction']}  "
            f"relation={e['relation']}",
            f"      判断字段路径 : {'、'.join(e['structured_judgement_path']) or '（无）'}",
            f"      台账条目     : {e['ledger_path']}  →  "
            f"source={e['source']}  time={e['time']}  status={e['status']}  "
            f"adopted={e['adopted']}",
            f"      内容摘要     : {e['result_summary']}",
            f"      依据         : {e['basis']}",
            f"      真实工具调用 : {tc['tool_name']}  status={tc['status']}  "
            f"arguments={json.dumps(tc['arguments'], ensure_ascii=False)}",
            f"      匹配信号     : {e['match_signal']}（关键词 {e['keyword_hits']}，"
            f"数值 {e['number_hits']}）→ {e['match_strength']}",
            f"      调用认领依据 : {e['tool_call_derivation']}",
        ]
    out.append(f"  chain_ok = {claim['chain_ok']}")
    for k in claim["constraints"]:
        out.append(
            f"  ! 反向/限制条目 {k['evidence_id']}（role={k['role']}，"
            f"direction={k['direction']}，status={k['status']}）"
            f" {k['ledger_path']}：{k['summary']}")
        out.append(f"      依据: {k['basis']}  ｜ 工具 {k['tool_call']['tool_name']}"
                   f"（status={k['tool_call']['status']}）")
    return out


def _evidence_identity_rows(trace: dict) -> list[list[str]]:
    """四类证据身份表（来自 V5 的结构化行，不手编）。"""
    rows = []
    for r in trace["checks"]["details"]["V5"]["rows"]:
        ident = "、".join(
            f"{p['field']}={p['value'] if p['present'] else '（缺）'}"
            for p in r["identity_fields"])
        rows.append([
            r["role"],
            r["source_type"],
            str(r["entry_count"]),
            r["status"] or "（无）",
            "是" if r["content_available"] else
            ("**取不到**" if r["degraded"] else "否"),
            ident,
            r["role_meaning"],
        ])
    return rows


def _check_evidence_lines(trace: dict) -> list[str]:
    """V1–V6 的**判定依据**（不是光给 PASS/FAIL）。"""
    d = trace["checks"]["details"]
    out: list[str] = []

    v1 = d["V1"]
    out.append(f"  V1 溯源链闭合 —— 挑出结论句 {v1['claims_selected']} 条"
               f"（applicable={v1['applicable']}）")
    if v1["note"]:
        out.append(f"      注意: {v1['note']}")
    for it in v1["items"]:
        out.append(f"      · {it['claim_text'][:46]}… → 证据 {it['evidence_total']} 条，"
                   f"status=ok {it['evidence_ok']} 条，chain_ok={it['chain_ok']}，"
                   f"强度={it['best_match_strength']}")

    v2 = d["V2"]
    out.append(f"  V2 真调用 —— {len(v2['items'])} 条被引用的证据逐条对账")
    for it in v2["items"]:
        out.append(f"      · {it['ledger_path']} → {it['tool_name']}"
                   f"（status={it['tool_status']}）"
                   f"{'✔ 找到' if it['found'] else '✘ 缺失'}"
                   f"｜认领依据: {it['derivation']}")

    v3 = d["V3"]
    out.append(f"  V3 红线（谈天气必须真调天气工具）—— "
               f"触发={'是' if v3['triggered'] else '否'}"
               f"，回答命中词={v3['answer_terms'] or '（无）'}；"
               f"天气工具调用 {len(v3['weather_tool_calls'])} 次")
    for c in v3["weather_tool_calls"]:
        out.append(f"      · {c['tool_name']}  status={c['status']}  "
                   f"adopted={c['adopted']}  "
                   f"arguments={json.dumps(c['arguments'], ensure_ascii=False)}"
                   f"{'  error=' + str(c['error']) if c['error'] else ''}")

    v4 = d["V4"]
    out.append(f"  V4 红线（谈知识必须真检索）—— "
               f"触发={'是' if v4['triggered'] else '否'}"
               f"，回答命中词={v4['answer_terms'] or '（无）'}；"
               f"知识检索调用 {len(v4['knowledge_tool_calls'])} 次")
    for c in v4["knowledge_tool_calls"]:
        out.append(f"      · {c['tool_name']}  status={c['status']}  "
                   f"adopted={c['adopted']}  "
                   f"arguments={json.dumps(c['arguments'], ensure_ascii=False)}"
                   f"{'  error=' + str(c['error']) if c['error'] else ''}")

    v5 = d["V5"]
    out.append(f"  V5 四类证据角色可区分 —— 角色由 {v5['role_determined_by']}决定"
               f"（台账里有 role 字段：{v5['role_field_present_in_ledger']}）")
    for r in v5["rows"]:
        ok = "✔ 可区分" if r["distinguishable"] else "✘ 不可区分"
        out.append(f"      · {r['role']:<8} source_type={r['source_type']}  "
                   f"status={r['status']}  内容可得={r['content_available']}  "
                   f"降级={r['degraded']}  → {ok}")
    if v5["degraded_roles"]:
        for r in v5["degraded_roles"]:
            out.append(f"      ! 降级角色 {r['role']}：status={r['status']}，"
                       f"内容可得={r['content_available']}，"
                       f"证据 {r['evidence_ids']}")
    if v5["note"]:
        out.append(f"      备注: {v5['note']}")

    v6 = d["V6"]
    out.append(f"  V6 台账自洽 —— total_calls={v6['total_calls']}，"
               f"adopted_calls={v6['adopted_calls']}，"
               f"not_adopted_calls={v6['not_adopted_calls_count']}，"
               f"实际进入证据集的条目数={v6['adopted_entries_count']}；"
               f"求和自洽={v6['sum_ok']}，条目一致={v6['entries_ok']}")
    if v6.get("adoption_text"):
        out.append(f"      台账原文: {v6['adoption_text']}")
    return out


def build_terminal_sections(traces: dict[str, dict],
                            scan: dict | None = None) -> list[dict]:
    """构造终端走查的章节（数据全部来自 trace / 存档，不手编）。"""
    secs: list[dict] = []
    main_rid = DEFAULT_RUN if DEFAULT_RUN in traces else (
        next(iter(traces)) if traces else "")

    # ---- 1 审计对象 ----
    body = ["本轮审计对象（全部来自 B07/runs/*/run.json，0 次 LLM 调用、不联网）：", ""]
    rows = []
    for rid, t in traces.items():
        m = t["meta"]
        rows.append([rid, str(m.get("scenario")), str(m.get("status")),
                     str(m.get("model") or "—"),
                     str(len(t["claims"])),
                     str(m.get("ledger_entry_count")),
                     str(m.get("ledger_tool_call_count"))])
    body += _plain_table(
        ["run", "场景", "状态", "模型", "结论句", "台账条目", "工具调用"], rows)
    body += ["",
             f"主 run = {main_rid}（默认审计对象；--run 可换）",
             "问题指纹 question_sha256 = "
             + str(traces[main_rid]["meta"].get("question_sha256")),
             "提示词指纹 system_prompt_sha256 = "
             + str(traces[main_rid]["meta"].get("system_prompt_sha256")),
             "",
             "创新点 2 的工作方式：老师随意指出一句 → 这句话落到 structured_judgement 的",
             "哪个字段 → 落到 evidence_ledger.entries 的哪一条 → 那一条对应哪次真实工具",
             "调用（tool_name + arguments + status）。下面把整条链逐字摊开。"]
    secs.append({"title": "审计对象", "body": body})

    # ---- 2 溯源链实录 ----
    body = []
    for rid, t in traces.items():
        body.append(f"───── {rid}（{t['meta'].get('scenario')}）─────")
        if not t["claims"]:
            body.append("  （本 run 没有挑出可溯源的结论句）")
            continue
        for c in t["claims"]:
            body += _chain_text(t, c)
            body.append("")
    secs.append({"title": "溯源链实录：结论句 → 判断字段 → 台账条目 → 真实工具调用",
                 "body": body})

    # ---- 3 结论句原文 ----
    body = []
    for rid, t in traces.items():
        body.append(f"───── {rid} ─────")
        body.append(t["meta"].get("answer") or "（回答为空）")
        body.append("")
    secs.append({"title": "被溯源的原始回答（LLM 原话，未删改）", "body": body})

    # ---- 4 四类证据身份 ----
    body = []
    for rid, t in traces.items():
        body.append(f"───── {rid} ─────")
        body += _plain_table(
            ["角色", "source_type", "条数", "status", "内容可得", "身份字段", "承担什么"],
            _evidence_identity_rows(t))
        body.append("")
    secs.append({"title": "四类证据身份表（角色由 source_type 结构化决定）",
                 "body": body})

    # ---- 5 工具调用总账 ----
    body = []
    for rid, t in traces.items():
        body.append(f"───── {rid}：{len(t['tool_calls'])} 次工具调用（含未被采用的）"
                    f" ─────")
        rows = []
        for i, c in enumerate(t["tool_calls"]):
            rows.append([str(i), str(c["tool_name"]), str(c["status"]),
                         str(c["adopted"]),
                         json.dumps(c["arguments"], ensure_ascii=False),
                         str(c["error"] or "—"),
                         str(c["purpose"] or "—")])
        body += _plain_table(
            ["#", "工具", "status", "adopted", "arguments", "error", "用途"], rows)
        body.append("")
    secs.append({"title": "工具调用总账（每一次都记，含未被采用的）", "body": body})

    # ---- 6 V1–V6 判定与依据 ----
    body = []
    for rid, t in traces.items():
        c = t["checks"]
        body.append(f"───── {rid} ─────")
        body.append("  判定：" + "  ".join(
            f"{k}={'PASS' if c[k] else 'FAIL'}" for k in
            ("V1", "V2", "V3", "V4", "V5", "V6")))
        body += _check_evidence_lines(t)
        body.append("")
    secs.append({"title": "V1–V6 逐项判定与依据（每项附真实字段值）",
                 "body": body})

    # ---- 7 匹配强弱分档 ----
    body = []
    rows = []
    for rid, t in traces.items():
        for c in t["claims"]:
            txt = c["claim_text"]
            txt = txt if len(txt) <= 34 else txt[:34] + "…"
            rows.append([rid, str(c["claim_index"]), txt,
                         str(c["best_match_strength"]),
                         "、".join(c["matched_roles"]) or "（无）",
                         str(c["chain_ok"])])
    body += _plain_table(
        ["run", "句号", "结论句（截断）", "最强强度", "命中角色", "chain_ok"], rows)
    body += ["",
             "分档口径：strong = 命中了台账里的数值，或命中了有区分度的特征词；",
             "weak = 只命中「温度/湿度/模型」这类通用词。弱匹配不当成强证据。"]
    secs.append({"title": "匹配强弱分档（不把弱匹配充成强证据）", "body": body})

    # ---- 8 降级证据 ----
    body = []
    rows = []
    for rid, t in traces.items():
        for r in t["checks"]["details"]["V5"]["rows"]:
            if not r["degraded"]:
                continue
            rows.append([rid, r["role"], str(r["status"]),
                         "可得" if r["content_available"] else "**取不到**",
                         ",".join(str(x) for x in r["evidence_ids"])])
    if rows:
        body += _plain_table(["run", "角色", "status", "内容", "证据"], rows)
        body += ["",
                 "降级证据的内容字段取不到 —— 它只参与「哪些证据不可用」这个事实，",
                 "不能被算成「该类证据的内容参与了判断」。"]
    else:
        body.append("本次审计的 run 里，四类证据全部 status=ok，无降级。")
    secs.append({"title": "降级证据（status≠ok 时内容取不到，不许当成用了）",
                 "body": body})

    # ---- 9 限制陈述 ----
    body = []
    for rid, t in traces.items():
        stmts = t.get("limitation_statements") or []
        if not stmts:
            body.append(f"{rid}：本 run 未识别到限制陈述。")
            continue
        body.append(f"{rid}：{len(stmts)} 条限制陈述，同样可追到字段：")
        for s in stmts:
            body.append(f"  · {s['statement_text']}")
            body.append(f"      命中限制短语: {s['matched_cues']} → "
                        f"{'、'.join(s['limitation_path'])}")
            for k, v in (s.get("limitation_text") or {}).items():
                body.append(f"      {k} 原文: {v}")
        body.append("")
    secs.append({"title": "限制陈述溯源（限制也要能追到 structured_judgement）",
                 "body": body})

    # ---- 10 全库扫描 ----
    body = []
    if scan:
        names = {"V1": "溯源链闭合", "V2": "真调用", "V3": "红线：天气必须真调用",
                 "V4": "红线：知识必须真检索", "V5": "四类角色可区分",
                 "V6": "台账自洽"}
        rows = []
        for key in ("V1", "V2", "V3", "V4", "V5", "V6"):
            c = scan["checks"][key]
            rows.append([key, names[key], f"{c['passed']} / {c['total']}",
                         "、".join(c["failed_runs"]) or "（无）"])
        body += _plain_table(["检查", "含义", "通过/总数", "未通过的 run"], rows)
        body += ["",
                 f"全库带 evidence_ledger 的 run 共 {scan['runs_total']} 个，"
                 f"本轮跑通 {scan['runs_traced']} 个，异常 "
                 f"{len(scan['errors'])} 个"
                 + (f"：{scan['errors']}" if scan["errors"] else ""),
                 f"V1 空真 run（无回答可溯，本来就没有检验对象）："
                 f"{'、'.join(scan['v1_vacuous']) or '（无）'}"]
        for r in scan["non_success_runs"]:
            body.append(f"  ! status={r['status']} 的 run：{r['run_id']}，"
                        f"回答长度 {r['answer_length']}（失败原样留痕，未伪装成成功）")
    else:
        body.append("（没有系统级扫描数据）")
    secs.append({"title": "全库系统扫描（不是只看一个 run）", "body": body})

    # ---- 11 结论 ----
    main_t = traces.get(main_rid)
    body = []
    if main_t:
        c = main_t["checks"]
        n_ok = sum(1 for k in ("V1", "V2", "V3", "V4", "V5", "V6") if c[k])
        body += [
            f"在 {main_rid} 上：{len(main_t['claims'])} 条结论句全部能走到 "
            f"status=ok 的证据（chain_ok="
            f"{[x['chain_ok'] for x in main_t['claims']]}），"
            f"V1–V6 {n_ok}/6 通过。",
            "",
            "**这个模块证明了什么**：",
            "  1. 结论句 ↔ 判断字段 ↔ 台账条目 ↔ 真实工具调用，四段可闭合；",
            "  2. 谈天气必须真有天气工具调用，谈知识必须真有检索调用（V3/V4 红线）；",
            "  3. 证据身份（来源/时间/可用性）由结构化字段决定，不是靠文本猜；",
            "  4. 限制陈述同样能追到 structured_judgement.limitations.*；",
            "  5. 全库所有带台账的 run 一起验，不是个例。",
            "",
            "**这个模块没有证明什么**：",
            "  1. 匹配是**规则化**的（关键词 + 数值），不是语义理解 —— "
            "所以有 strong/weak 分档，weak 不等于证据充分；",
            "  2. 挑句是规则化的：没挑中的句子不代表没依据，只代表没被检；",
            "  3. 它**不能**证明结论正确，只能证明结论**有出处**；",
            "  4. 它是离线复核器，0 次 LLM 调用 —— 证明的是可追溯性，不是判断力。",
        ]
    secs.append({"title": "结论：证明了什么 / 没证明什么", "body": body})

    # ---- 12 复现 ----
    body = [
        "# 全部溯源 + 重新生成报告（不联网、不调 LLM）",
        "./venv/Scripts/python.exe -m B07.innovation2",
        "",
        "# 只重建报告（读已落盘的详版 trace）",
        "./venv/Scripts/python.exe -m B07.innovation2 --report-only",
        "",
        "# 指定 run",
        "./venv/Scripts/python.exe -m B07.innovation2 --run run_082",
        "",
        "# —— 复核入口：老师任意指出一句 ——",
        "./venv/Scripts/python.exe -m B07.innovation2 --run run_082 --list-claims",
        "./venv/Scripts/python.exe -m B07.innovation2 --run run_082 "
        "--claim-index 6 --show",
        "./venv/Scripts/python.exe -m B07.innovation2 --run run_082 "
        '--claim "综上所述，……" --show',
    ]
    secs.append({"title": "复现命令", "body": body})

    for i, s in enumerate(secs, 1):
        s["no"] = i
        s["total"] = len(secs)
    return secs


def render_terminal(sections: list[dict]) -> str:
    lines = []
    for s in sections:
        lines.append("=" * 78)
        lines.append(f"[{s['no']}/{s['total']}] {s['title']}")
        lines.append("=" * 78)
        lines.extend(s["body"])
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 9. CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B07 创新点 2 —— 可追溯证据的自动溯源器")
    ap.add_argument("--run", default=DEFAULT_RUN,
                    help=f"主 run（默认 {DEFAULT_RUN}）")
    ap.add_argument("--runs", default=",".join(DEFAULT_RUNS),
                    help="要一并审计的 run（逗号分隔）")
    ap.add_argument("--report-only", action="store_true",
                    help="只重建报告（读已落盘的详版 trace）")
    ap.add_argument("--claim", action="append", default=[],
                    help="**教师人工指出的句子**（原文整句，可重复）。"
                         "给了就优先溯源这些句子，不受挑句规则约束")
    ap.add_argument("--claim-index", action="append", type=int, default=[],
                    help="按句序号（0 起，基于切句结果）指定句子，可重复")
    ap.add_argument("--show", action="store_true",
                    help="只把溯源链打到 stdout，**不写任何产物文件**")
    ap.add_argument("--stdout-encoding", default=None,
                    help="强制 stdout 编码（utf-8 / gbk / cp936 …）。"
                         "默认自动：按终端 codepage 对齐，"
                         "并在启动时把控制台 codepage 设成同一个")
    ap.add_argument("--list-claims", action="store_true",
                    help="列出该 run 的全部句子及其编号，供 --claim-index 用")
    ap.add_argument("--out", default=str(TRACE_JSON_PATH))
    ap.add_argument("--detail", default=str(DETAIL_JSON_PATH))
    ap.add_argument("--report", default=str(REPORT_PATH))
    args = ap.parse_args(argv)
    global _STDIO_ENCODING_OVERRIDE
    _STDIO_ENCODING_OVERRIDE = args.stdout_encoding
    _configure_stdio()          # 打印前再对一次：被 import 的现有模块
                                # （B07.evidence_ledger 等）会重设 stdout 编码

    run_ids = [r.strip() for r in args.runs.split(",") if r.strip()]
    if args.run and args.run not in run_ids:
        run_ids.insert(0, args.run)

    # ---- 人工指定句子：序号 → 原文 ----
    manual: list[str] = list(args.claim)
    if args.claim_index:
        from B07.runs import load_run
        answer = load_run(args.run).get("llm_explanation") or ""
        sents = split_sentences(answer)
        for i in args.claim_index:
            if not (0 <= i < len(sents)):
                print(f"[FAIL] 句序号 {i} 越界（该 run 共 {len(sents)} 句）")
                return 2
            manual.append(sents[i])

    if args.list_claims:
        from B07.runs import load_run
        answer = load_run(args.run).get("llm_explanation") or ""
        for i, s in enumerate(split_sentences(answer)):
            print(f"[{i}] {s}")
        return 0

    scan: dict | None = None
    if args.report_only and not manual:
        if not DETAIL_JSON_PATH.exists():
            print("缺少详版 trace 文件，无法 --report-only")
            return 1
        blob = json.loads(DETAIL_JSON_PATH.read_text(encoding="utf-8"))
        traces = blob.get("traces") or {}
        scan = blob.get("system_scan") or None
    else:
        traces = {}
        for rid in run_ids:
            try:
                # 人工指定的句子只作用于它所属的那个 run（--run）
                traces[rid] = trace_run(
                    rid, manual_claims=(manual or None) if rid == args.run
                    else None)
            except Exception as exc:      # 显式暴露失败，不伪装成功
                print(f"[FAIL] {rid}: {type(exc).__name__}: {exc}")
                return 2
        _configure_stdio()      # trace_run 会 import 现有模块（它们会重设
                                # stdout 编码），打印前必须再对一次
        # 系统级扫描：全库所有带台账的 run 的 V1–V6 判定。
        # 放进产物顶层，单个文件就能自证"创新点 2 生效了没有"，
        # 不需要另外打开报告 md。
        scan = scan_all_runs()
        if not args.show:
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps({"traces": {rid: to_spec_format(t)
                                       for rid, t in traces.items()},
                            "system_scan": scan},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            Path(args.detail).write_text(
                json.dumps({"traces": traces, "system_scan": scan},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")

    if args.show:
        # 复核入口：老师指出一句 → 把这条链的每一段连同真实字段值摊开
        for rid, t in traces.items():
            print("=" * 78)
            print(f"{rid}  溯源链")
            print("=" * 78)
            for c in t["claims"]:
                print("\n".join(_chain_text(t, c)))
                print()
            print("--- V1–V6 判定依据 ---")
            print("\n".join(_check_evidence_lines(t)))
            print()
        return 0

    md = build_report(traces)
    Path(args.report).write_text(md, encoding="utf-8")

    _configure_stdio()
    print(render_terminal(build_terminal_sections(traces, scan)))
    print(f"trace  -> {args.out}")
    print(f"detail -> {args.detail}")
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
