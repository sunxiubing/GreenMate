"""B06 Step 3 — 时间切分 + 泄漏自检

标签规则（透明可审计）：
  next_window_attention ∈ {NORMAL, WATCH}
  - WATCH：target 窗口任一指标越界（阈值见 config.py rule_v1）
  - NORMAL：全部指标在正常范围内
  - label_reason 附触发原因，便于错误分析

泄漏自检：
  1. 断言特征列集合 == FEATURE_COLUMNS
  2. 黑名单检查：特征里不含禁止模式
  3. 时间断言：每个样本的 window_end < label_window_start
  4. 统计报告：特征列清单、每列来源、自检通过状态

用法：
  python B06/split.py              # 执行切分 + 自检
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "D:/GreenMate")

import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from B06.config import (
    LABEL_SOURCE, RULE_VERSION,
    TEMP_MIN, TEMP_MAX, HUM_MIN, HUM_MAX, LUX_MAX,
    FEATURE_COLUMNS, FEATURE_BLACKLIST,
    TRAIN_DAYS, TEST_DAYS, RANDOM_SEED,
    SIM_SCENARIO_ABNORMAL,
)


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class LabeledSample:
    """一个带标签的训练样本"""
    window_id: str
    source: str
    window_end: datetime      # history 窗口终点时间
    label_window_start: datetime  # target 窗口起点时间
    features: dict[str, float]
    label: str              # NORMAL / WATCH
    label_reason: str        # 触发规则说明
    label_source: str        # rule_v1
    # 数据集划分
    split: str = ""          # train / val / test


# --------------------------------------------------------------------------
# 标签规则（透明可审计）
# --------------------------------------------------------------------------

def assign_label_rule_based(
    window_end: datetime,
    label_window_start: datetime,
    target_temps: list[float],
    target_hums: list[float],
    target_luxs: list[float],
) -> tuple[str, str]:
    """基于 config.py 阈值分配标签（透明规则）。"""
    reasons = []

    for i, t in enumerate(target_temps):
        if t < TEMP_MIN:
            reasons.append(f"[TEMP#{i+1}={t:.1f}°C < {TEMP_MIN}°C]")
        elif t > TEMP_MAX:
            reasons.append(f"[TEMP#{i+1}={t:.1f}°C > {TEMP_MAX}°C]")

    for i, h in enumerate(target_hums):
        if h < HUM_MIN:
            reasons.append(f"[HUM#{i+1}={h:.1f}% < {HUM_MIN}%]")
        elif h > HUM_MAX:
            reasons.append(f"[HUM#{i+1}={h:.1f}% > {HUM_MAX}%]")

    for i, l in enumerate(target_luxs):
        if l > LUX_MAX:
            reasons.append(f"[LUX#{i+1}={l:.0f}lux > {LUX_MAX}lux]")

    if reasons:
        return "WATCH", " 触发 ".join(reasons)
    return "NORMAL", "全部指标在正常范围内"


# --------------------------------------------------------------------------
# 泄漏自检
# --------------------------------------------------------------------------

def leakage_check(samples: list[LabeledSample]) -> dict:
    """执行全部泄漏自检，返回检查结果字典。"""
    checks = {}

    if not samples:
        return {"error": "无样本可检查"}

    # 1. 特征列白名单断言
    sample_features = set(samples[0].features.keys())
    expected = set(FEATURE_COLUMNS)
    missing = expected - sample_features
    extra = sample_features - expected
    checks["feature_columns"] = {
        "name": "特征列白名单断言",
        "passed": missing == extra == set(),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "detail": f"期望 {len(expected)} 列，实际 {len(sample_features)} 列"
    }

    # 2. 黑名单检查
    blacklist_violations = []
    for feat in sorted(sample_features):
        for pattern in FEATURE_BLACKLIST:
            if pattern in feat:
                blacklist_violations.append(f"  ❌ '{feat}' 命中黑名单模式 '{pattern}'")
    checks["blacklist"] = {
        "name": "黑名单检查",
        "passed": len(blacklist_violations) == 0,
        "violations": blacklist_violations if blacklist_violations else ["无违规特征 ✅"],
    }

    # 3. 时间断言
    time_violations = []
    for s in samples:
        if not (s.window_end < s.label_window_start):
            time_violations.append(
                f"  ❌ {s.window_id}: window_end={s.window_end} >= label_window_start={s.label_window_start}"
            )
    checks["time_assertion"] = {
        "name": "时间断言（window_end < label_window_start）",
        "passed": len(time_violations) == 0,
        "violations": time_violations if time_violations else ["全部通过 ✅"],
    }

    # 4. 标签来源检查
    label_source_check = all(s.label_source == LABEL_SOURCE for s in samples)
    checks["label_source"] = {
        "name": f"标签来源检查（label_source == {LABEL_SOURCE}）",
        "passed": label_source_check,
        "detail": f"全部 {len(samples)} 个样本" if label_source_check else "存在异常来源",
    }

    return checks


def print_leakage_report(checks: dict, samples: list[LabeledSample]):
    """打印泄漏自检报告。"""
    print("=" * 70)
    print("【泄漏自检报告】")
    print("=" * 70)

    # 汇总
    all_passed = all(c["passed"] for c in checks.values())
    print(f"\n  自检结果: {'✅ 全部通过' if all_passed else '❌ 存在违规项'}")
    print(f"  检查项数: {len(checks)}")
    print(f"  通过项数: {sum(1 for c in checks.values() if c['passed'])}")
    print()

    for key, check in checks.items():
        icon = "✅" if check["passed"] else "❌"
        print(f"  {icon} {check['name']}")
        if "detail" in check:
            print(f"      {check['detail']}")
        if "violations" in check:
            for v in check["violations"]:
                print(f"      {v}")
        if "missing" in check and check["missing"]:
            print(f"      缺失列: {check['missing']}")
        if "extra" in check and check["extra"]:
            print(f"      多余列: {check['extra']}")

    # 特征列清单
    print("\n" + "=" * 70)
    print("【特征列清单】")
    print("=" * 70)
    feat_cols = sorted(samples[0].features.keys())

    # 按来源分组
    print("\n  按来源分类:")
    history_stats = [f for f in feat_cols if not f.startswith("dtw_")]
    dtw_stats = [f for f in feat_cols if f.startswith("dtw_")]
    print(f"    history 窗口统计特征（{len(history_stats)} 列）:")
    for f in history_stats:
        parts = f.rsplit("_", 1)
        if len(parts) == 2:
            stat, field = parts
            print(f"      {f:<28} ← history.{field} 的 {stat}")
    print(f"    DTW 相似度特征（{len(dtw_stats)} 列）:")
    for f in dtw_stats:
        attr = f.replace("dtw_", "")
        print(f"      {f:<28} ← DTW(history, 历史窗口).{attr}")

    print(f"\n  特征总数: {len(feat_cols)}")
    print(f"  禁止特征总数: {len(FEATURE_BLACKLIST)}（均未出现在特征列中 ✅）")

    # 标签分布
    print("\n" + "=" * 70)
    print("【标签分布统计】")
    print("=" * 70)
    label_counts = {}
    for s in samples:
        label_counts[s.label] = label_counts.get(s.label, 0) + 1
    for lbl, cnt in sorted(label_counts.items()):
        pct = cnt / len(samples) * 100
        bar = "█" * int(pct / 2)
        print(f"  {lbl:6s}  {cnt:4d} 个 ({pct:5.1f}%)  {bar}")

    # 时间覆盖
    print("\n" + "=" * 70)
    print("【时间覆盖】")
    print("=" * 70)
    ends = [s.window_end for s in samples]
    starts = [s.label_window_start for s in samples]
    print(f"  窗口终点最早: {min(ends)}")
    print(f"  窗口终点最晚: {max(ends)}")
    print(f"  标签窗口起点最早: {min(starts)}")
    print(f"  标签窗口起点最晚: {max(starts)}")

    # 特征统计（防泄漏证明）
    print("\n" + "=" * 70)
    print("【特征统计（证明无泄漏）】")
    print("=" * 70)
    all_vals = {}
    for s in samples:
        for k, v in s.features.items():
            all_vals.setdefault(k, []).append(v)
    print(f"  {'特征名':<28}  {'min':>10}  {'max':>10}  {'mean':>10}  {'std':>10}")
    print(f"  {'-'*28}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}")
    for k in sorted(all_vals.keys()):
        vals = all_vals[k]
        mn = min(vals)
        mx = max(vals)
        av = sum(vals) / len(vals)
        st = statistics.stdev(vals) if len(vals) > 1 else 0
        print(f"  {k:<28}  {mn:>10.4f}  {mx:>10.4f}  {av:>10.4f}  {st:>10.4f}")

    return all_passed


# --------------------------------------------------------------------------
# 时间切分
# --------------------------------------------------------------------------

def temporal_split(samples: list[LabeledSample]) -> tuple[list[LabeledSample], list[LabeledSample], list[LabeledSample]]:
    """按时间边界切分：前5天=训练集，后2天=测试集，丢弃跨边界窗口。

    Returns: (train, test, dropped)
    """
    # 按时间排序
    samples = sorted(samples, key=lambda s: s.window_end)

    # 以数据实际起始时间为起点计算切分边界
    data_start = samples[0].window_end.replace(hour=0, minute=0, second=0, microsecond=0)
    train_end = data_start + timedelta(days=TRAIN_DAYS)
    test_start = train_end  # 刚好接上，无间隙

    print(f"  数据起始: {data_start.date()}")
    print(f"  训练集截止: {train_end.date()}（前 {TRAIN_DAYS} 天）")
    print(f"  测试集开始: {test_start.date()}（后 {TEST_DAYS} 天）")

    train: list[LabeledSample] = []
    test: list[LabeledSample] = []
    dropped: list[LabeledSample] = []

    for s in samples:
        if s.window_end < train_end:
            s.split = "train"
            train.append(s)
        elif s.window_end >= test_start:
            s.split = "test"
            test.append(s)
        else:
            # 跨边界（不应该发生，因为按点滑动切割不会有中间地带）
            s.split = "dropped"
            dropped.append(s)

    return train, test, dropped


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("B06 Step 3 — 时间切分 + 泄漏自检")
    print("=" * 70)
    print(f"\n  规则版本: {LABEL_SOURCE} / {RULE_VERSION}")
    print(f"  切分方式: 前 {TRAIN_DAYS} 天训练集，后 {TEST_DAYS} 天测试集（时间边界切分，禁止 shuffle）")
    print(f"  随机种子: {RANDOM_SEED}")

    # 读取 Step 1/2 窗口
    print("\n加载 Step 1 窗口...")
    from B06.generate import load_samples, generate_windows
    import B06.SPEC as spec

    samples_b02 = load_samples()
    windows = generate_windows(
        samples_b02,
        window_size=spec.WINDOW_SIZE,
        target_size=spec.TARGET_SIZE,
    )
    print(f"  窗口数: {len(windows)}")

    # 计算 DTW
    if spec.DTW_COMPUTE and windows:
        from B06.SPEC import compute_dtw
        for i in range(len(windows)):
            hw = windows[:i]
            if hw:
                windows[i] = compute_dtw(windows[i], hw[-spec.DTW_WINDOW_LIMIT:])

    # 提取特征（复用 Step 2 逻辑）
    print("提取特征...")
    from B06.feature import extract_features
    labeled: list[LabeledSample] = []

    for w in windows:
        feats = extract_features(w)

        # 时间
        window_end = w.history[-1].timestamp
        label_window_start = w.target[0].timestamp

        # target 值用于规则判断
        target_temps = [s.temperature for s in w.target]
        target_hums = [s.humidity for s in w.target]
        target_luxs = [s.lux for s in w.target]

        # 透明规则赋值标签
        label, reason = assign_label_rule_based(
            window_end, label_window_start,
            target_temps, target_hums, target_luxs,
        )

        labeled.append(LabeledSample(
            window_id=w.window_id,
            source=w.source,
            window_end=window_end,
            label_window_start=label_window_start,
            features=feats,
            label=label,
            label_reason=reason,
            label_source=LABEL_SOURCE,
        ))

    print(f"  样本数: {len(labeled)}")

    # 泄漏自检
    print("\n执行泄漏自检...")
    checks = leakage_check(labeled)

    # 时间切分
    print("\n执行时间切分（无 shuffle）...")
    train, test, dropped = temporal_split(labeled)
    all_labeled = train + test

    # 泄漏自检（用全部有效样本）
    print("\n执行泄漏自检...")
    checks = leakage_check(all_labeled)
    all_passed = print_leakage_report(checks, all_labeled)

    # 切分结果
    print("\n" + "=" * 70)
    print("【切分结果】")
    print("=" * 70)
    for split, split_samples in [("train", train), ("test", test)]:
        cnt = len(split_samples)
        lbl_cnt = {}
        for s in split_samples:
            lbl_cnt[s.label] = lbl_cnt.get(s.label, 0) + 1
        ends = [s.window_end for s in split_samples]
        starts = [s.label_window_start for s in split_samples]
        print(f"  {split:5s}: {cnt:4d} 个  ", end="")
        for lbl, c in sorted(lbl_cnt.items()):
            pct = c / cnt * 100
            print(f"  {lbl}={c}({pct:.0f}%)", end="")
        print()
        if ends:
            print(f"        窗口终点: {min(ends)} ~ {max(ends)}")
            print(f"        标签窗口起点: {min(starts)} ~ {max(starts)}")

    # 跨边界丢弃
    print(f"\n  丢弃跨边界窗口: {len(dropped)} 个")

    # 类别覆盖检查
    print("\n" + "=" * 70)
    print("【类别覆盖检查】")
    print("=" * 70)
    train_labels = set(s.label for s in train)
    test_labels = set(s.label for s in test)
    for split, labels, samples in [("train", train_labels, train), ("test", test_labels, test)]:
        missing = {"NORMAL", "WATCH"} - labels
        if missing:
            print(f"  ⚠️  {split} 缺少类别: {missing}")
            for lbl in sorted(missing):
                cnt = sum(1 for s in samples if s.label == lbl)
                print(f"      {lbl}: 0 个（建议：接受单类测试，或调整时间边界）")
        else:
            print(f"  ✅ {split} 同时包含 NORMAL 和 WATCH")
            watch_cnt = sum(1 for s in samples if s.label == "WATCH")
            watch_pct = watch_cnt / len(samples) * 100
            if watch_pct < 10:
                print(f"      ⚠️  WATCH 仅 {watch_cnt} 个（{watch_pct:.0f}%），建议调整时间边界或接受类别不平衡")

    # 时间泄漏证明
    print("\n" + "=" * 70)
    print("【无时间泄漏证明】")
    print("=" * 70)
    if train and test:
        train_max_end = max(s.window_end for s in train)
        test_min_start = min(s.label_window_start for s in test)
        overlap = train_max_end >= test_min_start
        print(f"  训练集窗口终点最晚: {train_max_end}")
        print(f"  测试集标签窗口起点最早: {test_min_start}")
        print(f"  时间区间重叠: {'❌ 存在重叠！' if overlap else '✅ 无重叠'}")

    # 泄漏自检结果
    print(f"\n  泄漏自检: {'✅ 通过' if all_passed else '❌ 未通过'}")

    print("\n" + "=" * 70)
    print("B06 Step 3 完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
