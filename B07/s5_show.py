"""B07 S5 —— 一步到位展示（只读存档，不调 LLM，不花钱）

一条命令把 S5 的三件事全部打在屏幕上：

  ① 主动制造的失败是什么（坏在哪一条、怎么坏的、坏成什么样）
  ② 还能判断什么 / 不能判断什么（⑥⑦ 上下文块 + 最终关注度）
  ③ 失败前后的两次运行对比（逐项对比表 + 两次 LLM 回答原文并排）
  ④ 防伪造检验 A/B/C/D（逐次判定 + 原文片段）

用法：
    ./venv/Scripts/python.exe -m B07.s5_show              # 从现有存档展示
    ./venv/Scripts/python.exe -m B07.s5_show --rerun      # 先重跑实验（要调 LLM）再展示

**不调 LLM**：所有内容都从 `B07/artifacts/s5_raw_result.json` 和
`B07/runs/<run_id>/run.json` 里读出来，不改任何存档。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.runs import RUNS_DIR
from B07.s5 import RAW_RESULT_PATH

W = 78


def _line(ch="="):
    print(ch * W)


def _head(title: str):
    _line()
    print(title)
    _line()


def _load_raw() -> dict:
    if not RAW_RESULT_PATH.exists():
        raise SystemExit(
            f"❌ {RAW_RESULT_PATH} 不存在 —— 先跑：\n"
            f"   ./venv/Scripts/python.exe -m B07.s5_show --rerun")
    return json.loads(RAW_RESULT_PATH.read_text(encoding="utf-8"))


def _archive(run_id: str) -> dict:
    p = RUNS_DIR / run_id / "run.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# ① 主动制造的失败
# --------------------------------------------------------------------------

def show_failure(raw: dict) -> None:
    _head("① 主动制造的失败 —— 被弄坏的是哪一条、坏成什么样")
    print(f"唯一变量：B07_WEATHER_UNAVAILABLE 环境变量（只有 B07/weather.py 读它）")
    print(f"模拟开关：B07_WEATHER_UNAVAILABLE=1 → provider 直接返回 unavailable\n")

    for tag, key in (("基线", "baseline"), ("降级", "degraded")):
        v = raw[key]
        ext = (v.get("structured_judgement") or {}).get("external_conditions") or {}
        print(f"  {tag}  run_id = {v.get('run_id')}   降级开关 = "
              f"{'B07_WEATHER_UNAVAILABLE=1' if key == 'degraded' else '未设置'}")
        print(f"        status        = {ext.get('status')}")
        print(f"        signal        = {ext.get('signal')}")
        print(f"        provider      = {ext.get('provider')}")
        print(f"        forecast_time = {ext.get('forecast_time')}")
        print(f"        error         = {ext.get('error') or '（无）'}")
        # 工具台账里的那一条
        for c in (v.get("tool_calls") or []):
            if c.get("tool_name") == "B05.get_weather_forecast":
                print(f"        工具台账      = status={c.get('status')} / "
                      f"error={(c.get('error') or '（无）')[:70]}")
        print()

    d_ext = (raw["degraded"].get("structured_judgement") or {}).get(
        "external_conditions") or {}
    print("  判据：降级那条 status=unavailable、error 非空、provider 身份不变"
          "（同一条证据被弄坏，不是换了来源）、")
    print("        且 ⑥ 块里不残留任何天气数值。"
          f"  当前 provider={d_ext.get('provider')}")


# --------------------------------------------------------------------------
# ② 还能判断什么 / 不能判断什么
# --------------------------------------------------------------------------

def show_capability(raw: dict) -> None:
    _head("② 还能判断什么 / 不能判断什么 —— 降级 run 的 ⑥⑦ 上下文块")
    ctx = raw["degraded"].get("llm_context") or ""
    if "【⑥" not in ctx:
        print("  （存档里没有 ⑥ 块）")
        return
    print(ctx[ctx.index("【⑥"):])
    print()
    print("  ↑ 逐行对应文档：")
    print("    「不受影响的部分：①当前环境 ②最近历史 ③B06 预测 ⑤限制条件」"
          " ⇒ 还能判断什么")
    print("    「外部天气证据当前不可用 … 判断受到限制」"
          " ⇒ 不能判断什么")
    print("    「当前不可用的证据（取不到，不参与方向判定，不进上面三列）」"
          " ⇒ 不可用证据被单列")


def show_attention(raw: dict) -> None:
    print()
    print("  程序层给出的最终关注度：")
    for tag, key in (("基线", "baseline"), ("降级", "degraded")):
        v = raw[key]
        j = v.get("structured_judgement") or {}
        ca = v.get("combined_attention") or j.get("combined_attention") or {}
        div = j.get("evidence_divergence") or v.get("divergence") or {}
        value = ca.get("value") if isinstance(ca, dict) else ca
        print(f"    {tag}  combined_attention = {value}"
              f"   has_conflict = {div.get('has_conflict')}"
              f"   supporting = {len(div.get('supporting') or [])}"
              f"  opposing = {len(div.get('opposing') or [])}"
              f"  undetermined = {len(div.get('undetermined') or [])}")
    print("  ↑ 规则一个字没改：基线的 conflict_unresolved 是因为存在 opposing 的"
          "外部天气证据，")
    print("    那条证据取不到后，冲突自然消失，退让为 watch_internal。")


# --------------------------------------------------------------------------
# ③ 前后对比
# --------------------------------------------------------------------------

def show_comparison(raw: dict) -> None:
    _head("③ 失败前后两次运行对比 —— 逐项对比表")
    print(f"{'对比项':<32} | {'基线':<26} | 降级")
    print("-" * W)
    for row in raw.get("comparison", []):
        b = str(row["baseline"])
        d = str(row["degraded"])
        if len(b) > 26:
            b = b[:23] + "..."
        if len(d) > 34:
            d = d[:31] + "..."
        print(f"{row['item']:<32} | {b:<26} | {d}")
    print("-" * W)


def show_answers(raw: dict) -> None:
    _head("③b 两次运行的 LLM 回答原文（并排）")
    for tag, key in (("基线", "baseline"), ("降级", "degraded")):
        v = raw[key]
        print(f"【{tag}】{v.get('run_id')}   "
              f"model={(v.get('llm_meta') or {}).get('model')}   "
              f"字数={len(v.get('llm_explanation') or '')}")
        print("-" * W)
        print(v.get("llm_explanation") or "（无）")
        print()


# --------------------------------------------------------------------------
# ④ 防伪造检验
# --------------------------------------------------------------------------

def show_probe(raw: dict) -> None:
    _head("④ 防伪造检验 A / B / C / D（降级 run）")
    pr = (raw.get("probe") or {}).get("degraded")
    if not pr:
        print("  （raw 里没有检验结果 —— 跑一次 -m B07.s5 --reprobe）")
        return
    print(f"  分母 = {pr['total']} 次（含失败 {pr['failed']} 次）")
    print(f"  合计通过 {pr['rate_text']['pass']}   "
          f"伪造 {pr['rate_text']['fabricated_evidence']}   "
          f"未声明受限 {pr['rate_text']['no_limitation_disclosure']}   "
          f"能力全丢 {pr['rate_text']['capability_lost']}   "
          f"归因错误 {pr['rate_text']['misattributed']}\n")

    for rec in pr.get("records", []):
        rid = rec.get("run_id")
        print(f"  ── {rid}  status={rec.get('status')}  "
              f"字数={rec.get('answer_length')}")
        p = rec.get("probe")
        if not p:
            print(f"     失败：{rec.get('failure_reason')}")
            print()
            continue
        a, b, c, d = p["A"], p["B"], p["C"], p["D"]
        print(f"     A 伪造检查   : 字面命中 {a['raw_hit_count']} 处 / "
              f"判定伪造 {a['fabrication_count']} 处 → "
              f"{'通过 ✅' if a['verdict'] == 'pass' else '红线 ❌'}")
        for f in a.get("disclosure_mentions", []):
            print(f"        声明（不算伪造）：{f['sentence'][:60]}")
        for f in a.get("fabrications", []):
            print(f"        ❌ 伪造：{f['sentence'][:60]}")
        print(f"     B 受限声明   : {'是 ✅' if b['disclosed'] else '否 ❌'}"
              f"  命中短语 {b.get('phrases')}")
        for f in b.get("fragments", []):
            print(f"        原文：{f[:60]}")
        print(f"     C 能力保持   : 历史 {'是' if c['history']['yes'] else '否'}"
              f" / 模型 {'是' if c['model']['yes'] else '否'}"
              f" / 知识 {'是' if c['knowledge']['yes'] else '否'}")
        print(f"     D 归因检查   : "
              f"{'通过 ✅' if d['verdict'] == 'pass' else '归因错误 ❌'}"
              f"（{d['misattribution_count']} 处）")
        print(f"     合计         : {p['overall']}")
        print()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    if "--rerun" in sys.argv:
        _line()
        print("正在重跑 S5 实验（会真实调用 LLM，约 40 秒）…")
        _line()
        from B07.s5 import main as s5_main
        s5_main()

    raw = _load_raw()
    _line()
    print("B07 S5 —— 证据退化（一次只弄坏一条证据）")
    _line()
    print(f"原始结果生成时间：{raw.get('generated_at')}")
    print(f"基线 run_id = {raw['baseline'].get('run_id')}   "
          f"降级 run_id = {raw['degraded'].get('run_id')}")
    pf = raw.get("prompt_fingerprints") or {}
    print(f"提示词指纹 question={pf.get('question_sha256')}"
          f"  system_prompt={pf.get('system_prompt_sha256')}")
    print(f"对照组：基线 {len(raw.get('baseline_runs') or [])} 次 / "
          f"降级 {len(raw.get('degraded_runs') or [])} 次")

    show_failure(raw)
    show_capability(raw)
    show_attention(raw)
    show_comparison(raw)
    show_answers(raw)
    show_probe(raw)

    _head("完整报告")
    report = Path(__file__).resolve().parent / "artifacts" / "s5_report.md"
    print(f"  {report}")
    print(f"  生成命令：./venv/Scripts/python.exe B07/s5_report.py（离线、确定性重放）")
    print()
    print("  复现实验：./venv/Scripts/python.exe -m B07.s5_show --rerun")
    print("  离线重算检验：./venv/Scripts/python.exe -m B07.s5 --reprobe")
    print("  不变量测试：./venv/Scripts/python.exe -m pytest B07/tests -q")
    print("  旧存档回归：./venv/Scripts/python.exe -m B07.runs explain run_023")
    return 0


if __name__ == "__main__":
    sys.exit(main())
