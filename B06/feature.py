"""B06 Step 2 — 特征构造与标签生成

特征集（仅从 history 窗口提取）：
  基础特征（每字段 8 项）：current, mean, delta, max, min, std, median, var
  扩展特征（每字段 2 项）：slope, range
  DTW 相似度（Step 1 新增特征）

标签规则（基于 target 窗口）：
  NORMAL — 目标窗口无扰动（sim_scenario == normal）
  WATCH  — 目标窗口存在扰动（sim_scenario != normal）

禁止进入特征集的数据（强制检查）：
  - target 窗口原始值
  - sim_scenario 字段
  - label_source
  - B03 天气数据（temperature/humidity/precipitation 等）

用法：
  python B06/feature.py              # 读取 Step 1 窗口，重算特征
  python B06/feature.py --no-dtw    # 跳过 DTW 相关特征
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "D:/GreenMate")

import argparse
import statistics
from dataclasses import dataclass

from B06.SPEC import TimeWindow, Sample


# --------------------------------------------------------------------------
# 特征统计函数
# --------------------------------------------------------------------------

def _vals(samples: list[Sample], attr: str) -> list[float]:
    return [getattr(s, attr) for s in samples]


def current(samples: list[Sample], attr: str) -> float:
    """窗口最后一个点的值"""
    return getattr(samples[-1], attr)


def mean(samples: list[Sample], attr: str) -> float:
    v = _vals(samples, attr)
    return sum(v) / len(v) if v else 0.0


def delta(samples: list[Sample], attr: str) -> float:
    """窗口最大值 - 最小值"""
    v = _vals(samples, attr)
    return max(v) - min(v) if v else 0.0


def max_val(samples: list[Sample], attr: str) -> float:
    v = _vals(samples, attr)
    return max(v) if v else 0.0


def min_val(samples: list[Sample], attr: str) -> float:
    v = _vals(samples, attr)
    return min(v) if v else 0.0


def std(samples: list[Sample], attr: str) -> float:
    v = _vals(samples, attr)
    if len(v) < 2:
        return 0.0
    return statistics.stdev(v)


def median(samples: list[Sample], attr: str) -> float:
    v = _vals(samples, attr)
    if not v:
        return 0.0
    return statistics.median(v)


def var(samples: list[Sample], attr: str) -> float:
    v = _vals(samples, attr)
    if len(v) < 2:
        return 0.0
    return statistics.variance(v)


def slope(samples: list[Sample], attr: str) -> float:
    """线性回归斜率（度/点）"""
    v = _vals(samples, attr)
    n = len(v)
    if n < 2:
        return 0.0
    x = list(range(n))
    x_mean = sum(x) / n
    v_mean = sum(v) / n
    num = sum((x[i] - x_mean) * (v[i] - v_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def range_val(samples: list[Sample], attr: str) -> float:
    """max - min（与 delta 相同，但作为独立特征命名）"""
    return delta(samples, attr)


# 所有特征函数（按名称）
_FEATURE_FNS = {
    "current": current,
    "mean": mean,
    "delta": delta,
    "max": max_val,
    "min": min_val,
    "std": std,
    "median": median,
    "var": var,
    "slope": slope,
    "range": range_val,
}

BASE_FEATURES = ["current", "mean", "delta", "max", "min", "std", "median", "var"]
EXTRA_FEATURES = ["slope", "range"]
DTW_FEATURES = ["dtw_temperature", "dtw_humidity", "dtw_lux"]
FIELDS = ["temperature", "humidity", "lux"]


# --------------------------------------------------------------------------
# 特征提取
# --------------------------------------------------------------------------

@dataclass
class LabeledWindow:
    """带特征和标签的窗口"""
    window_id: str
    source: str
    features: dict[str, float]   # feature_name → value
    label: str                   # NORMAL / WATCH
    label_reason: str            # 标签原因（用于透明度）

    def to_dict(self) -> dict:
        return {
            "window_id": self.window_id,
            "source": self.source,
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "label": self.label,
            "label_reason": self.label_reason,
        }


def extract_features(window: TimeWindow) -> dict[str, float]:
    """从 history 窗口提取全部特征（不触碰 target）"""
    feats = {}
    for field in FIELDS:
        samples = window.history
        for feat_name, fn in _FEATURE_FNS.items():
            feats[f"{feat_name}_{field}"] = fn(samples, field)
    # DTW 相似度（来自 Step 1）
    feats["dtw_temperature"] = window.dtw_temperature if window.dtw_temperature >= 0 else -1.0
    feats["dtw_humidity"] = window.dtw_humidity if window.dtw_humidity >= 0 else -1.0
    feats["dtw_lux"] = window.dtw_lux if window.dtw_lux >= 0 else -1.0
    return feats


def assign_label(window: TimeWindow) -> tuple[str, str]:
    """基于 target 窗口分配标签（NORMAL / WATCH）。

    规则：透明可审计
      - WATCH：目标窗口存在非 normal 的 sim_scenario
      - NORMAL：目标窗口全部为 normal
    """
    scenarios = {s.sim_scenario for s in window.target}
    if scenarios == {"normal"} or not scenarios:
        return "NORMAL", "目标窗口全部为 normal，无扰动"
    else:
        扰动 = scenarios - {"normal"}
        return "WATCH", f"目标窗口存在扰动场景: {扰动}"


# --------------------------------------------------------------------------
# 禁止特征检查
# --------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [
    "target_",          # target 窗口原始值
    "sim_scenario",     # sim_scenario 字段
    "label_source",     # label_source 字段
    "precipitation",    # B03 天气数据
    "cloud_cover",
    "solar_radiation",
    "weather_code",
]

# 显式允许的特征（与禁止模式交叉验证）
ALLOWED_EXPLICIT = {
    # 历史窗口统计特征
    "current_temperature", "mean_temperature", "delta_temperature",
    "max_temperature", "min_temperature", "std_temperature",
    "median_temperature", "var_temperature",
    "slope_temperature", "range_temperature",
    "current_humidity", "mean_humidity", "delta_humidity",
    "max_humidity", "min_humidity", "std_humidity",
    "median_humidity", "var_humidity",
    "slope_humidity", "range_humidity",
    "current_lux", "mean_lux", "delta_lux",
    "max_lux", "min_lux", "std_lux",
    "median_lux", "var_lux",
    "slope_lux", "range_lux",
    # DTW 相似度（Step 1 新增）
    "dtw_temperature", "dtw_humidity", "dtw_lux",
}


def verify_feature_safety(features: dict[str, float]):
    """验证特征集中不包含禁止数据，打印检查报告。"""
    violations = []
    for feat_name in features.keys():
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in feat_name:
                violations.append(f"  ❌ {feat_name} 命中禁止模式 '{pattern}'")

    if violations:
        print("  警告：特征集中发现禁止特征！")
        for v in violations:
            print(v)
    else:
        print("  ✅ 特征安全检查通过：未发现禁止模式")

    # 显式白名单验证
    missing = set(features.keys()) - ALLOWED_EXPLICIT
    unknown = missing - set(FORBIDDEN_PATTERNS)
    if unknown:
        print(f"  ⚠️  未知特征 {unknown}，请确认不在禁止列表中")
    else:
        print("  ✅ 特征白名单验证通过")


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="B06 Step 2: 特征构造与标签生成")
    parser.add_argument("--no-dtw", action="store_true", help="跳过 DTW 特征")
    args = parser.parse_args()

    print("=" * 60)
    print("B06 Step 2 — 特征构造与标签生成")
    print("=" * 60)

    # 读取 Step 1 生成的窗口
    print("\n加载 Step 1 窗口...")
    from B06.generate import load_samples, generate_windows
    import B06.SPEC as spec

    samples = load_samples()
    windows = generate_windows(samples, window_size=spec.WINDOW_SIZE, target_size=spec.TARGET_SIZE)

    if spec.DTW_COMPUTE and windows:
        from B06.SPEC import compute_dtw
        for i in range(len(windows)):
            hw = windows[:i]
            if hw:
                windows[i] = compute_dtw(windows[i], hw[-spec.DTW_WINDOW_LIMIT:])
    print(f"  窗口数: {len(windows)}")

    # 提取特征 + 标签
    print("\n提取特征...")
    labeled: list[LabeledWindow] = []
    for w in windows:
        feats = extract_features(w)
        label, reason = assign_label(w)
        labeled.append(LabeledWindow(
            window_id=w.window_id,
            source=w.source,
            features=feats,
            label=label,
            label_reason=reason,
        ))

    # 打印特征清单
    print("\n" + "=" * 60)
    print("【特征清单】仅从 history 窗口提取，禁止数据不进入特征集")
    print("=" * 60)

    n_base = len(BASE_FEATURES)
    n_extra = len(EXTRA_FEATURES)
    n_dtw = len(DTW_FEATURES)

    print(f"\n  基础特征（{n_base} 项/字段 × {len(FIELDS)} 字段 = {n_base * len(FIELDS)} 个）:")
    for f in BASE_FEATURES:
        for field in FIELDS:
            print(f"    {f}_{field}")

    print(f"\n  扩展特征（{n_extra} 项/字段 × {len(FIELDS)} 字段 = {n_extra * len(FIELDS)} 个）:")
    for f in EXTRA_FEATURES:
        for field in FIELDS:
            print(f"    {f}_{field}")

    print(f"\n  DTW 相似度（Step 1 新增，{n_dtw} 个）:")
    for f in DTW_FEATURES:
        print(f"    {f}")

    total = n_base * len(FIELDS) + n_extra * len(FIELDS) + n_dtw
    print(f"\n  特征总数: {n_base * len(FIELDS)} + {n_extra * len(FIELDS)} + {n_dtw} = {total} 个")

    # 禁止数据检查
    print("\n" + "=" * 60)
    print("【禁止数据检查】确保禁止数据不在特征集中")
    print("=" * 60)
    if labeled:
        sample_feats = labeled[0].features
        print(f"\n  检查模式:")
        for p in FORBIDDEN_PATTERNS:
            print(f"    - {p}")
        print()
        verify_feature_safety(sample_feats)

    # 标签分布
    print("\n" + "=" * 60)
    print("【标签分布】")
    print("=" * 60)
    label_counts = {}
    for lw in labeled:
        label_counts[lw.label] = label_counts.get(lw.label, 0) + 1
    for lbl, cnt in sorted(label_counts.items()):
        pct = cnt / len(labeled) * 100
        print(f"  {lbl}: {cnt} 个窗口 ({pct:.1f}%)")

    # 打印前 3 个完整样本
    print("\n" + "=" * 60)
    print("【前 3 个样本详情】")
    print("=" * 60)
    for lw in labeled[:3]:
        print(f"\n  [{lw.window_id}]  label={lw.label}  reason={lw.label_reason}")
        print(f"  特征（{len(lw.features)} 个）:")
        for k, v in sorted(lw.features.items()):
            print(f"    {k:<22}: {v:>10.4f}")

    print("\n" + "=" * 60)
    print("B06 Step 2 完成 ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
