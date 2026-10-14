"""B02 创新点 — 变点监测算法

给定一段温度时序序列，自动识别状态突变的精确时间点。

算法：二元检测（CUSUM）+ 滑动窗口结合
- 计算相邻采样点的温度差分序列
- 差分的均值显著偏离零 → 存在突变
- 结合 sim_scenario 标注交叉验证

用途：
  - 自动发现扰动进入点（无需依赖 sim_scenario）
  - 为 B06 时间窗口预测提供状态切换边界
  - 替代人工标注，减少专家知识依赖

sim_scenario 使用限制（与 SPEC.py 保持一致）：
  sim_scenario 是模拟器元数据，不代表真实农情标签。
  变点监测结果是算法输出，也需要人工审核才能作为业务标签。
  两者均不能直接当作"正常/异常"标签用于决策。
"""

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

_self_path = Path(__file__).resolve().parent
_b02_root = _self_path.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from database.store import all_samples, get_connection
from simulator.engine import Sample


class ChangePoint(NamedTuple):
    """一个检测到的变点。"""
    timestamp: datetime
    index: int           # 样本在序列中的索引
    delta_temp: float    # 突变前后温度变化量（°C）
    new_scenario: str    # 突变后的场景名（参考值，来自 sim_scenario）


def _cusum(diffs: list[float], threshold: float) -> list[int]:
    """计算 CUSUM 序列，返回累积偏移超过阈值的时刻索引。

    CUSUM 能捕捉小幅度的持续偏移，比单纯看差分峰值更稳健。
    threshold 单位与 diffs 一致（此处为 °C/30min）。
    """
    cusum_pos = 0.0  # 正向累积
    cusum_neg = 0.0  # 负向累积
    signals = []
    k = 0.5  # 参考值，漂移检测灵敏度

    for i, d in enumerate(diffs):
        cusum_pos = max(0.0, cusum_pos + d - k)
        cusum_neg = min(0.0, cusum_neg + d + k)
        if cusum_pos > threshold or cusum_neg < -threshold:
            signals.append(i)
            cusum_pos = 0.0
            cusum_neg = 0.0
    return signals


def _rolling_mean(values: list[float], window: int) -> list[float]:
    result = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        result.append(sum(values[lo:i+1]) / (i - lo + 1))
    return result


def _rolling_std(values: list[float], window: int = 6) -> list[float]:
    """计算滚动标准差（窗口内）。"""
    result = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        chunk = values[lo:i+1]
        mean = sum(chunk) / len(chunk)
        variance = sum((x - mean) ** 2 for x in chunk) / len(chunk)
        result.append(variance ** 0.5)
    return result


def _fitted_temp(t: datetime) -> float:
    """给定时刻，返回拟合的昼夜温度基线值（无扰动情况下的期望值）。"""
    hour = t.hour + t.minute / 60.0
    phase = (hour - 14) * (math.pi / 12)
    return 18.0 + 0.5 * (34.0 - 18.0) * (1 + math.cos(phase))


def _rolling_stats(values: list[float], window: int) -> tuple[list[float], list[float]]:
    """返回滚动均值和滚动标准差（窗口内）。"""
    means, stds = [], []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        chunk = values[lo:i+1]
        m = sum(chunk) / len(chunk)
        variance = sum((x - m) ** 2 for x in chunk) / len(chunk)
        means.append(m)
        stds.append(variance ** 0.5)
    return means, stds


def detect_changepoints(samples: list[Sample] | None = None) -> list[ChangePoint]:
    """检测温度时序中的状态突变点。

    算法：局部自适应阈值（滚动标准差倍数）
      对每个点计算"当前温度偏离近期局部均值"的程度，
      用滚动标准差做自适应阈值（local_sigma 倍）。

    核心改进：
      - cold_snap 在日出进入，最大偏差 -5°，低于固定阈值 6°，
        但相对于局部基线的偏离量远大于局部波动 → 能检测到
      - heat_wave 进入时偏差 +4°，低于固定阈值 6°，
        但相对局部波动仍属显著 → 能检测到
      - 昼夜正常升降的波动会被局部标准差吸收，不会触发误报

    参数：
        samples: 样本列表，None 时从数据库读取全部

    返回：
        ChangePoint 列表，按时间顺序排列
    """
    if samples is None:
        samples = all_samples()

    if len(samples) < 12:
        return []

    temps = [s.temperature for s in samples]
    ROLL = 12        # 12 点窗口 ≈ 6 小时
    SIGMA = 2.0      # 偏离量超过 local_std × SIGMA 即触发
    GAP = 16         # 最小变点间隔 ≈ 8 小时
    MIN_DEV = 2.0    # 绝对偏差至少 2°C（防止极低波动时误触）

    rolling_means, rolling_stds = _rolling_stats(temps, ROLL)

    change_points: list[ChangePoint] = []
    prev_idx = -GAP
    in_transition = False

    for i in range(ROLL, len(temps)):
        deviation = temps[i] - rolling_means[i]
        threshold = rolling_stds[i] * SIGMA

        # 触发条件：绝对偏差够大 且 相对局部波动够显著
        if abs(deviation) > MIN_DEV and abs(deviation) > threshold and (i - prev_idx) >= GAP:
            delta = temps[i] - temps[i-1]
            change_points.append(ChangePoint(
                timestamp=samples[i].timestamp,
                index=i,
                delta_temp=round(delta, 1),
                new_scenario=samples[i].sim_scenario,
            ))
            prev_idx = i

    return change_points


def evaluate_detection(change_points: list[ChangePoint], samples: list[Sample]) -> dict:
    """用 sim_scenario 交叉验证变点检测质量。

    评估指标：
      - 检测覆盖率：检测到的变点中，有多少落在真实场景切换点 ±2 个样本内
      - 误报率：检测到变点但 sim_scenario 未切换的比例
      - 漏报率：sim_scenario 切换但未检测到变点的比例

    注意：sim_scenario 是模拟器内部标注，与真实农情状态无直接对应关系。
    此评估只用于验证算法行为，不应解读为"模型对真实异常的检测能力"。
    """
    if len(samples) < 2:
        return {}

    # 找出真实的场景切换点
    real_changes = []
    for i in range(1, len(samples)):
        if samples[i].sim_scenario != samples[i-1].sim_scenario:
            real_changes.append(i)

    detected = {(cp.index, cp.timestamp) for cp in change_points}

    # 覆盖率：检测点落在真实切换点 ±2 范围内
    covered = 0
    for rc in real_changes:
        if any(abs(rc - di[0]) <= 2 for di in detected):
            covered += 1

    # 误报：检测到变点但 sim_scenario 没切换
    false_alarms = 0
    for (idx, _) in detected:
        if not any(abs(idx - rc) <= 2 for rc in real_changes):
            false_alarms += 1

    recall = covered / len(real_changes) if real_changes else None
    precision = (len(detected) - false_alarms) / len(detected) if detected else None

    return {
        "total_real_changes": len(real_changes),
        "total_detected": len(detected),
        "covered": covered,
        "false_alarms": false_alarms,
        "recall": round(recall, 3) if recall is not None else None,
        "precision": round(precision, 3) if precision is not None else None,
        "real_change_indices": real_changes,
    }


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    samples = all_samples()
    print(f"输入样本: {len(samples)} 条")
    print(f"时间范围: {samples[0].timestamp} ~ {samples[-1].timestamp}")
    print()

    cps = detect_changepoints(samples)
    print(f"检测到 {len(cps)} 个变点:")
    for cp in cps:
        print(f"  [{cp.index:3d}] {cp.timestamp}  ΔT={cp.delta_temp:+.1f}°C  场景={cp.new_scenario}")

    print()
    eval_result = evaluate_detection(cps, samples)
    print("算法评估（与 sim_scenario 对照）:")
    print(f"  真实场景切换点: {eval_result['total_real_changes']}")
    print(f"  检测到变点数:   {eval_result['total_detected']}")
    print(f"  覆盖率 (recall):   {eval_result['recall']}")
    print(f"  精确度 (precision): {eval_result['precision']}")
    print(f"  误报数: {eval_result['false_alarms']}")
