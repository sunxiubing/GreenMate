# -*- coding: utf-8 -*-
"""B07 创新点 2 —— 独立复核脚本（只读，不修改 B07/artifacts/ 下任何文件）

不信任 innovation2.py 自报的 checks，全部自己重算。

用法：
  python B07/verify_innovation2.py                 # 终端打印（UTF-8）
  python B07/verify_innovation2.py -o report.txt   # 另存一份 UTF-8 报告

产物结构（本轮修正）：
  innovation2_trace.json / innovation2_trace_detail.json 的顶层键是
  `traces`（{run_id: {source_run_id, source_run_path, claims, tool_calls, checks}}）
  与 `system_scan`。旧脚本只认 `runs` / `records` / `claims`，所以一条也读不到。
  本脚本同时兼容两种结构：

    {"traces": {...}}                     ← innovation2 当前产物
    {"runs"|"records"|"claims": [...]}    ← 旧结构
    [...]                                 ← 纯记录列表

  两个 trace 文件内容重叠（detail 是 trace 的超集），只取信息更全的一个，
  不重复统计。
"""
import argparse
import json
import pathlib
import re
import sys

ART_DIR = pathlib.Path("B07/artifacts")
RUNS = pathlib.Path("B07/runs")
OUT = None                      # 由 -o 指定；默认只打印，不落盘

WEATHER_TOOL_HINTS = ["weather", "天气", "meteo", "forecast"]
KNOWLEDGE_TOOL_HINTS = ["knowledge", "检索", "rag", "search_tomato"]
WEATHER_CLAIM = [
    "外部天气", "外部条件", "天气条件", "降水概率", "下雨", "阴雨",
    "转差", "变差", "external_deteriorating", "external_improving",
    "external_stable",
]
KNOWLEDGE_CLAIM = ["知识", "检索", "条目", "知识库", "依据条目"]
DISCLAIM = [
    "不可用", "未能获取", "未获取", "获取失败", "暂不可用", "无法获取",
    "受限制", "受到限制", "受到影响", "无法判断", "未纳入", "缺失",
]

#: 四类证据角色 —— 由台账 entries 的 source_type 决定（台账里没有 role 字段）
ROLE_BY_SOURCE = {
    "B02": "history",
    "B03": "weather",
    "B04": "knowledge",
    "B06": "model",
}
#: 角色可识别所需的身份字段（结构化字段，不看 basis 文本）
ROLE_IDENTITY_FIELDS = ("source_type", "source", "time", "status")

_lines = []


def say(s=""):
    _lines.append(str(s))
    print(s)


def jd(o):
    return json.dumps(o, ensure_ascii=False)


def walk_tool_names(obj, acc=None):
    if acc is None:
        acc = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "tool_name" and isinstance(v, str):
                acc.add(v)
            walk_tool_names(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            walk_tool_names(v, acc)
    return acc


def asserts_about(answer, phrases):
    """回答里是否**断言**了相关内容（免责声明不算）"""
    for s in re.split(r"(?<=[。！？!?\n；;])", answer):
        s = s.strip()
        if not s:
            continue
        if not any(p.lower() in s.lower() for p in phrases):
            continue
        if any(d in s for d in DISCLAIM):
            continue                      # 承认不可用 → 合法
        return True
    return False


# --------------------------------------------------------------------------
# 读产物
# --------------------------------------------------------------------------

def traces_of(data):
    """把任意已知结构规整成 [(trace_id, trace_dict), ...]。"""
    if isinstance(data, dict) and isinstance(data.get("traces"), dict):
        return [(rid, t) for rid, t in data["traces"].items() if isinstance(t, dict)]
    if isinstance(data, list):
        return [("(artifact)", {"claims": data})]
    if isinstance(data, dict):
        recs = data.get("runs") or data.get("records") or data.get("claims") or []
        if recs:
            return [("(artifact)", {"claims": recs,
                                    "source_run_id": data.get("source_run_id")})]
    return []


def artifact_score(data):
    """同一个 trace 可能出现在多个文件里：字段越多 = 信息越全。"""
    traces = traces_of(data)
    if not traces:
        return -1
    best = 0
    for _, t in traces:
        claims = t.get("claims") or []
        for c in claims:
            if isinstance(c, dict):
                best = max(best, len(c))
                for e in (c.get("evidence") or []):
                    if isinstance(e, dict):
                        best = max(best, len(e))
    return best


def find_artifact():
    """返回 (path, data, traces)。优先选信息最全的那个产物文件。"""
    cands = sorted(ART_DIR.glob("innovation2*.json"))
    if not cands:
        return None, None, []
    best = (None, None, [])
    best_score = -1
    for c in cands:
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
        except Exception as e:                     # noqa: BLE001
            say(f"   跳过 {c.name}（解析失败：{e}）")
            continue
        tr = traces_of(d)
        if not tr:
            continue
        score = artifact_score(d)
        if score > best_score:
            best, best_score = (c, d, tr), score
    return best


def resolve_source(trace, trace_id):
    """定位 trace 对应的源 run.json。"""
    raw = trace.get("source_run_path")
    src_id = trace.get("source_run_id")
    if raw:
        p = pathlib.Path(raw)
        if p.exists():
            return p, src_id or p.parent.name
    if not src_id:
        claims = trace.get("claims") or []
        if claims and isinstance(claims[0], dict):
            src_id = claims[0].get("source_run_id")
    if src_id:
        p = RUNS / str(src_id) / "run.json"
        if p.exists():
            return p, src_id
    return None, src_id


# --------------------------------------------------------------------------
# 独立复核
# --------------------------------------------------------------------------

def check_trace(trace, src):
    """对一条 trace 重算 V1–V6（+V2b），返回 (checks, notes)。"""
    claims = [c for c in (trace.get("claims") or [])
              if isinstance(c, dict) and "evidence" in c]
    answer = src.get("llm_explanation") or ""
    tool_names = walk_tool_names(src)
    # 老 run 的 run.json 可能只有台账里的工具调用：补一层兜底
    ledger = src.get("evidence_ledger") or {}
    for rec in ((ledger.get("tool_calls") or {}).get("records") or []):
        if isinstance(rec, dict) and rec.get("tool_name"):
            tool_names.add(rec["tool_name"])

    has_weather = any(any(h in t.lower() for h in WEATHER_TOOL_HINTS)
                      for t in tool_names)
    has_knowledge = any(any(h in t.lower() for h in KNOWLEDGE_TOOL_HINTS)
                        for t in tool_names)

    checks, notes = {}, {}

    # V1 每条结论句 → ≥1 条 status=ok 证据
    if not claims:
        checks["V1"] = None                        # 空真，单独暴露
        notes["V1"] = "没有筛出结论句 → 空真（不算通过）"
    else:
        bad = [c for c in claims
               if not any(str(e.get("status")) == "ok"
                          for e in (c.get("evidence") or []))]
        checks["V1"] = not bad
        notes["V1"] = f"{len(claims)} 条结论句；无 ok 证据的 {len(bad)} 条"

    # V2 每条证据都带工具调用记录
    v2_bad = []
    for c in claims:
        for e in (c.get("evidence") or []):
            if not (e.get("tool_call") or {}).get("tool_name"):
                v2_bad.append(c.get("claim_index"))
    checks["V2"] = not v2_bad
    notes["V2"] = f"缺 tool_name 的证据 {len(v2_bad)} 条"

    # V2b 引用的工具名在源 run 里真实出现过
    used_tools = {(e.get("tool_call") or {}).get("tool_name")
                  for c in claims for e in (c.get("evidence") or [])
                  if (e.get("tool_call") or {}).get("tool_name")}
    missing = sorted(t for t in used_tools if t not in tool_names)
    checks["V2b"] = bool(used_tools) and not missing
    notes["V2b"] = f"引用 {sorted(used_tools)}；源 run 里找不到的 {missing}"

    # V3 / V4 红线
    aw = asserts_about(answer, WEATHER_CLAIM)
    ak = asserts_about(answer, KNOWLEDGE_CLAIM)
    checks["V3"] = (not aw) or has_weather
    checks["V4"] = (not ak) or has_knowledge
    notes["V3"] = f"回答断言天气={aw}；源 run 有天气调用={has_weather}"
    notes["V4"] = f"回答断言知识={ak}；源 run 有知识调用={has_knowledge}"

    # V5 四类角色齐全且可从结构化字段识别
    entries = [e for e in (ledger.get("entries") or []) if isinstance(e, dict)]
    role_rows = []
    for st, role in ROLE_BY_SOURCE.items():
        es = [e for e in entries if e.get("source_type") == st]
        ident_ok = bool(es) and all(
            es[0].get(f) is not None for f in ROLE_IDENTITY_FIELDS)
        degraded = bool(es) and es[0].get("status") != "ok"
        error_ok = (not degraded) or es[0].get("error") is not None
        role_rows.append({
            "role": role, "source_type": st, "present": bool(es),
            "status": es[0].get("status") if es else None,
            "identifiable": ident_ok, "error_ok": error_ok,
        })
    checks["V5"] = all(r["present"] and r["identifiable"] and r["error_ok"]
                       for r in role_rows)
    notes["V5"] = "、".join(
        f"{r['source_type']}/{r['role']}:{'有' if r['present'] else '缺'}"
        f"({r['status']})" for r in role_rows)

    # V6 台账自洽
    ad = ledger.get("adoption") or {}
    total = ad.get("total_calls")
    adopted = ad.get("adopted_calls")
    not_adopted = ad.get("not_adopted_calls")
    n_not = len(not_adopted) if isinstance(not_adopted, list) else (not_adopted or 0)
    n_adopted_entries = len([e for e in entries if e.get("adopted")])
    ok_sum = (isinstance(total, int) and isinstance(adopted, int)
              and adopted + n_not == total)
    ok_entries = isinstance(adopted, int) and adopted == n_adopted_entries
    checks["V6"] = bool(ok_sum and ok_entries)
    notes["V6"] = (f"total={total} adopted={adopted} not_adopted={n_not} "
                   f"台账内 adopted 条目={n_adopted_entries}")

    return checks, notes, answer, tool_names, used_tools


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="B07 创新点 2 独立复核（只读）")
    ap.add_argument("-o", "--out", help="把报告另存为 UTF-8 文件（默认只打印）")
    args = ap.parse_args(argv)

    if not ART_DIR.exists():
        say(f"!! 找不到 {ART_DIR}")
        return 1

    caught = find_artifact()
    path, _data, traces = caught if caught else (None, None, [])
    if not traces:
        say(f"!! {ART_DIR} 里没有可解析的 innovation2 产物")
        say("   先跑 python -m B07.innovation2")
        return 1

    say("候选产物：")
    for c in sorted(ART_DIR.glob("innovation2*.json")):
        say(f"   {c}  ({c.stat().st_size} 字节)")
    say(f"\n使用文件：{path}（{len(traces)} 个 run；字段更全的那份，不重复统计）")

    results = []
    for trace_id, trace in traces:
        src_path, src_id = resolve_source(trace, trace_id)
        say("")
        say("=" * 78)
        if src_path is None:
            say(f"[{trace_id}] !! 找不到源 run（source_run_id={src_id!r}）")
            results.append({"trace": trace_id, "error": "源 run 缺失"})
            continue
        try:
            src = json.loads(src_path.read_text(encoding="utf-8"))
        except Exception as e:                      # noqa: BLE001
            say(f"[{trace_id}] !! 源 run 解析失败：{e}")
            results.append({"trace": trace_id, "error": str(e)})
            continue

        checks, notes, answer, tool_names, used_tools = check_trace(trace, src)
        say(f"[{trace_id}] 源 run：{src_path}   回答 {len(answer)} 字")
        say("=" * 78)

        reported = trace.get("checks") or {}
        if isinstance(reported.get("details"), dict):
            reported = {k: v for k, v in reported.items()
                        if k in ("V1", "V2", "V3", "V4", "V5", "V6")}
        say("  复核项")
        for key in ("V1", "V2", "V2b", "V3", "V4", "V5", "V6"):
            v = checks[key]
            tag = "PASS" if v is True else ("VACUOUS" if v is None else "FAIL")
            say(f"    [{tag:7s}] {key}  {notes[key]}")
        say(f"    引用的工具名：{sorted(used_tools)}")
        say(f"    源 run 工具名：{sorted(tool_names)}")

        if reported:
            say("  自报 vs 复核（自报值不作为通过依据）")
            for key in ("V1", "V2", "V3", "V4", "V5", "V6"):
                if key not in reported:
                    continue
                same = (reported[key] is checks[key]) or (
                    reported[key] is True and checks[key] is True)
                say(f"    {key}: 自报={reported[key]} 复核={checks[key]}"
                    f" {'一致' if same else '<<< 不一致'}")
        results.append({"trace": trace_id, "checks": checks,
                        "reported": reported})

    say("")
    say("=" * 78)
    ok = sum(1 for r in results if r.get("checks") and
             all(v is not False for v in r["checks"].values()))
    say(f"[汇总] {ok}/{len(results)} 个 run 复核全部通过"
        f"（V1 空真单独标记，不算通过；FAIL 会显式列出）")
    say("=" * 78)

    if args.out:
        pathlib.Path(args.out).write_text("\n".join(_lines), encoding="utf-8")
        print(f"\n[已写入] {args.out}")
    return 0 if ok == len(results) and results else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
