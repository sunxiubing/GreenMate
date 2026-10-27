# -*- coding: utf-8 -*-
"""B07 创新点 2 —— 独立复核脚本（只读，不修改任何文件）

不信任 innovation2.py 自报的 checks，全部自己重算。

用法：python B07/verify_innovation2.py

- 终端可能乱码（Windows GBK），所以同时写一份 UTF-8 文件：
  B07/artifacts/verify_innovation2.txt   ← 用 VS Code 打开这个
"""
import json
import pathlib
import re
import sys

ART_DIR = pathlib.Path("B07/artifacts")
RUNS = pathlib.Path("B07/runs")
OUT = ART_DIR / "verify_innovation2.txt"

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
NUM_RE = re.compile(r"\d")

_lines = []


def say(s=""):
    _lines.append(str(s))
    try:
        print(s)
    except UnicodeEncodeError:
        print(str(s).encode("ascii", "replace").decode("ascii"))


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


def find_artifact():
    """自动找创新点2 的产物 json（名字可能不固定）"""
    cands = sorted(ART_DIR.glob("innovation2*.json"))
    cands += sorted(ART_DIR.glob("*innovation*2*.json"))
    cands = [c for c in cands if c.name != "verify_innovation2.txt"]
    return cands


def main():
    cands = find_artifact()
    if not cands:
        say(f"!! {ART_DIR} 里找不到 innovation2*.json")
        say("   先跑 python -m B07.innovation2")
        return 1
    say("候选产物文件：")
    for c in cands:
        say(f"   {c}  ({c.stat().st_size} 字节)")

    data = None
    used = None
    for c in cands:
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
        except Exception as e:
            say(f"   跳过 {c.name}（解析失败：{e}）")
            continue
        recs = d if isinstance(d, list) else (d.get("runs") or d.get("records")
                                              or d.get("claims") or [])
        if recs:
            data, used = d, c
            break
    if data is None:
        say("!! 所有候选文件里都没解析出记录")
        return 1

    say(f"\n使用文件：{used}")
    recs = data if isinstance(data, list) else (data.get("runs") or data.get("records")
                                                 or data.get("claims") or [])
    src_id = (data.get("source_run_id") if isinstance(data, dict) else None) \
        or (recs[0].get("source_run_id") if isinstance(recs[0], dict) else None)
    src_path = RUNS / str(src_id) / "run.json"
    if not src_path.exists():
        say(f"!! 找不到源 run：{src_path}")
        say(f"   产物里写的 source_run_id = {src_id!r}")
        return 1
    src = json.loads(src_path.read_text(encoding="utf-8"))
    answer = src.get("llm_explanation") or ""
    tool_names = walk_tool_names(src)

    say("=" * 78)
    say(f"[1] 源 run：{src_id}    回答长度 {len(answer)} 字")
    say("=" * 78)
    say(f"  run.json 里出现过的工具名：{sorted(tool_names) or '（无）'}")
    has_weather = any(any(h in t.lower() for h in WEATHER_TOOL_HINTS)
                      for t in tool_names)
    has_knowledge = any(any(h in t.lower() for h in KNOWLEDGE_TOOL_HINTS)
                        for t in tool_names)
    say(f"  天气工具调用：{'有 ✅' if has_weather else '无 <<<'}")
    say(f"  知识检索调用：{'有 ✅' if has_knowledge else '无 <<<'}")

    claims = []
    for r in recs:
        if isinstance(r, dict) and "evidence" in r:
            claims.append(r)
    if not claims and isinstance(data, dict) and data.get("claims"):
        claims = data["claims"]

    say(f"\n  溯源结论句 {len(claims)} 条")
    for i, c in enumerate(claims):
        ev = c.get("evidence") or []
        ok_ev = [e for e in ev if str(e.get("status")) == "ok"]
        with_tc = [e for e in ev if (e.get("tool_call") or {}).get("tool_name")]
        roles = sorted({e.get("role") for e in ev if e.get("role")})
        say(f"\n  --- claim #{c.get('claim_index', i)} ---")
        say(f"      {str(c.get('claim_text'))[:110]}")
        say(f"      证据 {len(ev)} 条 | status=ok {len(ok_ev)} 条 | "
            f"带工具调用 {len(with_tc)} 条")
        say(f"      角色：{roles} | 缺失角色：{c.get('missing_roles')}")

    say("")
    say("=" * 78)
    say("[2] 独立复核 V1–V6")
    say("=" * 78)

    v1 = bool(claims) and all(
        any(str(e.get("status")) == "ok" for e in (c.get("evidence") or []))
        for c in claims)
    v2 = bool(claims) and all(
        all((e.get("tool_call") or {}).get("tool_name")
            for e in (c.get("evidence") or []))
        for c in claims)
    used_tools = set()
    for c in claims:
        for e in (c.get("evidence") or []):
            tn = (e.get("tool_call") or {}).get("tool_name")
            if tn:
                used_tools.add(tn)
    v2b = bool(used_tools) and used_tools.issubset(tool_names)

    aw = asserts_about(answer, WEATHER_CLAIM)
    ak = asserts_about(answer, KNOWLEDGE_CLAIM)
    v3 = (not aw) or has_weather
    v4 = (not ak) or has_knowledge

    all_roles = set()
    for c in claims:
        for e in (c.get("evidence") or []):
            if e.get("role"):
                all_roles.add(e["role"])
    v5 = {"history", "weather", "knowledge", "model"}.issubset(all_roles)

    led = src.get("evidence_ledger") or {}
    ad = led.get("adoption") or {}
    v6, v6note = True, "（源 run 无 adoption 记账，跳过）"
    if isinstance(ad, dict) and "total_calls" in ad:
        tot = ad.get("total_calls")
        adopt = ad.get("adopted_calls")
        notad = len(ad.get("not_adopted_calls") or [])
        cnt = led.get("counts") or {}
        v6 = (adopt + notad == tot) and (
            cnt.get("adopted_entries") == adopt if "adopted_entries" in cnt else True)
        v6note = f"total={tot} adopted={adopt} not_adopted={notad} counts={cnt}"

    checks = [
        ("V1 溯源链闭合（每条结论 → ≥1 条 status=ok 证据）", v1),
        ("V2 每条证据都带工具调用记录", v2),
        ("V2b 引用的工具名在源 run 里真实出现过", v2b),
        ("V3 回答断言天气内容 → 源 run 必须有天气工具调用", v3),
        ("V4 回答断言知识内容 → 源 run 必须有知识检索调用", v4),
        ("V5 四类角色齐全（history/weather/knowledge/model）", v5),
        ("V6 台账计数自洽", v6),
    ]
    for name, ok in checks:
        say(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    say("")
    say(f"  V3 细节：回答断言天气={aw} / 源 run 有天气调用={has_weather}")
    say(f"  V4 细节：回答断言知识={ak} / 源 run 有知识调用={has_knowledge}")
    say(f"  V6 细节：{v6note}")
    say(f"  引用的工具名：{sorted(used_tools)}")
    say(f"  源 run 全部工具名：{sorted(tool_names)}")

    say("")
    say("=" * 78)
    say(f"[3] 汇总：{sum(1 for _, o in checks if o)}/{len(checks)} 通过")
    say("=" * 78)
    say("  ★ V3 / V4 是文档第四步点名的红线：")
    say("    「如果最终报告写着\"根据天气信息……\"，但运行记录里根本没有天气工具调用，这一轮不通过。」")
    return 0


def _flush():
    try:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(_lines), encoding="utf-8")
        print(f"\n[已写入 UTF-8 文件] {OUT}   ← 用 VS Code 打开这个，中文不乱码")
    except Exception as e:
        print(f"[写文件失败] {e}")


if __name__ == "__main__":
    rc = main()
    _flush()
    sys.exit(rc)