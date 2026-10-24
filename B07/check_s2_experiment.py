"""B07 S2 实验有效性校验脚本（独立可跑）

用途：拿两份真实存档（S1 / S2）当面核对"S2 到底证明没证明"，
把每条判定依据摊开打印，**不产生任何新的 run，不写任何文件**。

用法：
  python B07/check_s2_experiment.py --s1 run_012 --s2 run_015
  python B07/check_s2_experiment.py --s1 run_012 --s2 run_015 --json

检查项（每条给出 True/False 与实测值）：
  1. 两次的 question 字符串逐字相同
  2. 两次的 question_sha256 相同
  3. 两次的 system_prompt_sha256 相同，且等于现场实算 sha256(SYSTEM_PROMPT)[:12]
  4. 两次的当前状态温度差 ≤ 0.8℃（当前值可比）
  5. S1 稳定判定 passed=True、S2 passed=False
  6. S2 的 ΔT 超阈值，且超出量写进了判据
  7. S2 的结论句写清了"不稳定 / 超过阈值 / 超出多少"
  8. 两份存档的 scenario 字段正确
  9. ★对抗性自检：污染 sim_scenario 后选窗结果不变
 10. warnings 分类（gap 警告单列）

退出码：全部通过 0；有未通过项 1。

硬约束：只读存档，不改任何东西。
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from B07.explain import FIXED_QUESTION, SYSTEM_PROMPT, _sha12
from B07.runs import load_run

# 温度可比性容差（来自 S2 选窗条件 (a)）
TEMP_TOLERANCE = 0.8


def _get(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _temps(archive: dict) -> list:
    return [h["temperature"] for h in (archive.get("history") or [])]


def _delta_t(archive: dict):
    ts = _temps(archive)
    return round(max(ts) - min(ts), 2) if ts else None


def _temp_criterion(archive: dict) -> dict:
    stab = _get(archive, "history_window", "stability", default={}) or {}
    for c in stab.get("criteria") or []:
        if "温度" in c.get("name", "") and "波动" in c.get("name", ""):
            return c
    return {}


def _gap_warnings(archive: dict) -> tuple[list, list]:
    gap, other = [], []
    for w in archive.get("warnings") or []:
        (gap if "相隔" in w else other).append(w)
    return gap, other


# --------------------------------------------------------------------------
# 对抗性自检：污染 sim_scenario 后选窗是否不变
# --------------------------------------------------------------------------

def adversarial_check() -> dict:
    """把每条样本的 sim_scenario 全改成 "XXXXXXXX"，重跑 S2 选窗。"""
    from B07.stability import load_all_samples, pick_drifting_window

    samples = load_all_samples()
    clean = pick_drifting_window(samples)["selected"]
    polluted_samples = [replace(s, sim_scenario="XXXXXXXX") for s in samples]
    polluted = pick_drifting_window(polluted_samples)["selected"]

    def _view(w: dict) -> dict:
        return {
            "start_time": w["start_time"],
            "end_time": w["end_time"],
            "temp_range": w.get("temp_range", w.get("delta_t")),
        }

    c, p = _view(clean), _view(polluted)
    return {"clean": c, "polluted": p, "same": c == p}


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------

def build_checks(s1: dict, s2: dict, adversarial: dict) -> list[dict]:
    checks: list[dict] = []

    def add(no, name, passed, detail):
        checks.append({"no": no, "name": name, "passed": bool(passed), "detail": detail})

    s1_lm = s1.get("llm_meta") or {}
    s2_lm = s2.get("llm_meta") or {}
    s1_cs = s1.get("current_state") or {}
    s2_cs = s2.get("current_state") or {}

    # 1 问题字符串
    q1, q2 = s1.get("question"), s2.get("question")
    add(1, "两次 question 字符串逐字相同", q1 and q1 == q2,
        f"S1={q1!r}；S2={q2!r}；相同={q1 == q2}")

    # 2 question 指纹
    qf1, qf2 = s1_lm.get("question_sha256"), s2_lm.get("question_sha256")
    add(2, "两次 question_sha256 相同", qf1 and qf1 == qf2,
        f"S1={qf1 or '(无)'}；S2={qf2 or '(无)'}；现场实算={_sha12(FIXED_QUESTION)}")

    # 3 system prompt 指纹
    fp1, fp2 = s1_lm.get("system_prompt_sha256"), s2_lm.get("system_prompt_sha256")
    live = _sha12(SYSTEM_PROMPT)
    add(3, "两次 system_prompt_sha256 相同且等于现场实算",
        fp1 and fp1 == fp2 and fp1 == live,
        f"S1={fp1 or '(旧存档无此字段)'}；S2={fp2 or '(无)'}；"
        f"现场实算 sha256(SYSTEM_PROMPT)[:12]={live}")

    # 4 当前温度差
    t1, t2 = s1_cs.get("temperature"), s2_cs.get("temperature")
    gap = round(abs(t2 - t1), 2) if (t1 is not None and t2 is not None) else None
    add(4, f"两次当前状态温度差 ≤ {TEMP_TOLERANCE}℃",
        gap is not None and gap <= TEMP_TOLERANCE,
        f"S1={t1}℃（{s1_cs.get('timestamp')}）；S2={t2}℃（{s2_cs.get('timestamp')}）；"
        f"差值={gap}℃")

    # 5 稳定判定
    p1 = _get(s1, "history_window", "stability", "passed")
    p2 = _get(s2, "history_window", "stability", "passed")
    add(5, "S1 判稳定、S2 判不稳定", p1 is True and p2 is False,
        f"S1 passed={p1}；S2 passed={p2}")

    # 6 ΔT 超阈值
    c2 = _temp_criterion(s2)
    add(6, "S2 的 ΔT 超过温度阈值且写明超出量",
        c2 and c2.get("passed") is False and c2.get("excess", 0) > 0,
        f"S2 ΔT={_delta_t(s2)}℃，阈值={c2.get('threshold')}℃，"
        f"超出={c2.get('excess')}；S1 ΔT={_delta_t(s1)}℃，阈值="
        f"{_temp_criterion(s1).get('threshold')}℃")

    # 7 结论句
    concl = _get(s2, "history_window", "stability", "conclusion", default="") or ""
    need = ["不稳定", "超过阈值", "超出"]
    missing = [k for k in need if k not in concl]
    add(7, 'S2 结论句写清"不稳定 / 超过阈值 / 超出多少"', not missing,
        f"结论：{concl}" + (f"；缺：{missing}" if missing else ""))

    # 8 scenario 字段
    add(8, "两份存档的 scenario 字段正确",
        s1.get("scenario") == "S1" and s2.get("scenario") == "S2",
        f"S1 scenario={s1.get('scenario')!r}；S2 scenario={s2.get('scenario')!r}")

    # 9 对抗性自检
    c = adversarial["clean"]
    p = adversarial["polluted"]
    add(9, "★对抗性自检：污染 sim_scenario 后选窗不变", adversarial["same"],
        f"干净：{c['start_time']} → {c['end_time']} / temp_range={c['temp_range']}；"
        f"污染后：{p['start_time']} → {p['end_time']} / temp_range={p['temp_range']}；"
        f"一致={adversarial['same']}")

    return checks


# --------------------------------------------------------------------------
# 打印
# --------------------------------------------------------------------------

def print_report(s1: dict, s2: dict, s1_id: str, s2_id: str,
                 checks: list[dict], adversarial: dict) -> None:
    print("=" * 78)
    print("B07 S2 实验有效性校验（只读存档，不产生新 run）")
    print("=" * 78)
    print(f"\n  S1 存档：{s1_id}   scenario={s1.get('scenario') or '(旧存档未记录)'}   "
          f"锚点={s1.get('analysis_time')}")
    print(f"  S2 存档：{s2_id}   scenario={s2.get('scenario')}   "
          f"锚点={s2.get('analysis_time')}")

    # ---- 并排对比 ----
    print("\n◆ S1 / S2 并排对比")
    print("-" * 78)
    rows = [
        ("当前温度℃", _get(s1, "current_state", "temperature"),
         _get(s2, "current_state", "temperature")),
        ("当前湿度%", _get(s1, "current_state", "humidity"),
         _get(s2, "current_state", "humidity")),
        ("当前光照 lux", _get(s1, "current_state", "lux"),
         _get(s2, "current_state", "lux")),
        ("稳定判定", _get(s1, "history_window", "stability", "passed"),
         _get(s2, "history_window", "stability", "passed")),
        ("ΔT℃", _delta_t(s1), _delta_t(s2)),
        ("predicted_attention", _get(s1, "model_output", "predicted_attention"),
         _get(s2, "model_output", "predicted_attention")),
        ("model_version", _get(s1, "model_output", "model_version"),
         _get(s2, "model_output", "model_version")),
        ("confidence", _get(s1, "model_output", "confidence"),
         _get(s2, "model_output", "confidence")),
        ("hours_gap", s1.get("hours_gap"), s2.get("hours_gap")),
    ]
    print(f"  {'维度':<22}{'S1':<26}{'S2'}")
    for name, a, b in rows:
        print(f"  {name:<22}{str(a):<26}{b}")

    # ---- 历史 6 点并排 ----
    h1 = s1.get("history") or []
    h2 = s2.get("history") or []
    print("\n◆ 两次的历史 6 点并排")
    print("-" * 78)
    print(f"  {'#':<4}{'S1 时间':<24}{'S1 温度/湿度/光照':<34}"
          f"{'S2 时间':<24}{'S2 温度/湿度/光照'}")
    for i in range(max(len(h1), len(h2))):
        a = h1[i] if i < len(h1) else None
        b = h2[i] if i < len(h2) else None
        a_t = a["timestamp"] if a else "—"
        a_v = (f"{a['temperature']}℃ / {a['humidity']}% / {a['lux']:.0f} lux") if a else "—"
        b_t = b["timestamp"] if b else "—"
        b_v = (f"{b['temperature']}℃ / {b['humidity']}% / {b['lux']:.0f} lux") if b else "—"
        print(f"  {i + 1:<4}{a_t:<24}{a_v:<34}{b_t:<24}{b_v}")

    # ---- 结构化判断 ----
    print("\n◆ structured_judgement.recent_trend")
    print("-" * 78)
    for label, arch in (("S1", s1), ("S2", s2)):
        rt = _get(arch, "structured_judgement", "recent_trend", default={}) or {}
        print(f"  {label}: {rt.get('stability_verdict')}；{rt.get('conclusion')}")

    # ---- warnings ----
    print("\n◆ warnings")
    print("-" * 78)
    for label, arch in (("S1", s1), ("S2", s2)):
        gap, other = _gap_warnings(arch)
        if not gap and not other:
            print(f"  {label}: 无")
            continue
        print(f"  {label}:")
        for w in gap:
            print(f"    ⚠️  [时间锚点 gap，预期] {w}")
        for w in other:
            print(f"    ⚠️  {w}")

    # ---- 自检 ----
    print("\n◆ 实验有效性自检")
    print("-" * 78)
    for c in checks:
        print(f"  {c['no']}. {'✅ True' if c['passed'] else '❌ False'}  {c['name']}")
        print(f"     实测：{c['detail']}")

    # ---- 结论 ----
    print("\n◆ 结论")
    print("-" * 78)
    failed = [c for c in checks if not c["passed"]]
    if failed:
        print("  本次比对**不成立**，未通过项（不给结论，避免拿不成立的证据说话）：")
        for c in failed:
            print(f"    ❌ [{c['no']}] {c['name']}")
            print(f"       {c['detail']}")
        print()
        return
    t1 = _get(s1, "current_state", "temperature")
    t2 = _get(s2, "current_state", "temperature")
    print(f"  两次的当前温度都是 {t1}℃ / {t2}℃，提示词指纹也完全相同，")
    print("  唯一的差别是喂进去的那 6 个历史点：")
    print("  → 相同的当前值处在不同的状态演化过程中，")
    print("    所以 GreenMate 的理解不一样。这正是 S2 要证明的事。")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="B07 S2 实验有效性校验（只读存档，不产生新 run）")
    parser.add_argument("--s1", type=str, required=True, help="S1 存档 run_id")
    parser.add_argument("--s2", type=str, required=True, help="S2 存档 run_id")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    try:
        s1 = load_run(args.s1)
        s2 = load_run(args.s2)
    except Exception as e:
        print(f"  ❌ 读取存档失败：{type(e).__name__}: {e}")
        sys.exit(2)

    adversarial = adversarial_check()
    checks = build_checks(s1, s2, adversarial)
    ok = all(c["passed"] for c in checks)

    if args.json:
        print(json.dumps({
            "s1": args.s1, "s2": args.s2,
            "all_passed": ok,
            "checks": checks,
            "adversarial": adversarial,
        }, ensure_ascii=False, indent=2))
    else:
        print_report(s1, s2, args.s1, args.s2, checks, adversarial)
        print("=" * 78)
        print(f"  全部通过 ✅（{sum(c['passed'] for c in checks)}/{len(checks)}）"
              if ok else
              f"  存在未通过项 ❌（{sum(c['passed'] for c in checks)}/{len(checks)}）")
        print("=" * 78)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
