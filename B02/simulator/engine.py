"""B02 Step 2 — 模拟器：7 天时序数据生成

设计原则：
- 基线（正常昼夜）使用确定性的三角函数，无逐行随机数
- 扰动片段有明确的【进入 → 持续 → 恢复】过渡，不允许突变
- sim_scenario 标记模拟器元数据，不做业务标签用
- 任意修改扰动规则后，重新调用 generate() 可重刷全套数据

使用示例：
    from simulator import GreenhouseSimulator
    sim = GreenhouseSimulator()
    samples = sim.generate()
"""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import sys as _sys
from pathlib import Path as _Path

# B02 是平级目录，运行脚本时 Python 找不到父包。用文件系统路径解析。
# 这段只影响直接 python -m simulator.engine 的情况，import B02.simulator 不受影响。
_self_path = _Path(__file__).resolve().parent
_b02_root = _self_path.parent
if str(_b02_root) not in _sys.path:
    _sys.path.insert(0, str(_b02_root))

from SPEC import (
    SAMPLE_COUNT,
    SAMPLE_DAYS,
    SAMPLE_INTERVAL_MINUTES,
    HUMIDITY_MAX,
    HUMIDITY_MIN,
    LUX_MAX,
    LUX_MIN,
    TEMP_MAX,
    TEMP_MIN,
    VALID_SCENARIOS,
)


# --------------------------------------------------------------------------
# 基础昼夜曲线参数（参考北京地区夏季大棚典型值）
# --------------------------------------------------------------------------
# 温度（°C）：午夜 22 → 凌晨 5 点最低 18 → 下午 14 点最高 34
TEMP_NIGHT      = 18.0   # 夜间基线
TEMP_DAY_PEAK   = 34.0   # 日间峰值
TEMP_PEAK_HOUR  = 14     # 温度峰值时刻（24h 制）

# 湿度（%）：与温度大致负相关
HUMIDITY_NIGHT  = 85.0   # 夜间基线（高湿）
HUMIDITY_DAY_LOW= 55.0   # 午后最低

# 光照（lux）：太阳升起后渐增，正午峰值，日落归零
LUX_DAY_PEAK    = 60000  # 正午峰值（lux），晴天露天约 80000-100000，大棚有遮挡
LUX_SUNRISE_HR  = 6      # 日出时刻
LUX_SUNSET_HR   = 19     # 日落时刻


# --------------------------------------------------------------------------
# 扰动片段定义
# --------------------------------------------------------------------------
@dataclass
class Disturbance:
    """一次扰动片段，描述一段非正常状态的完整生命周期。"""
    name: str                      # 场景名称，对应 sim_scenario 值
    start_minute: int              # 进入扰动的起始时间（相对于时间线起点，单位：分钟）
    duration_minutes: int          # 扰动总持续时长
    transition_minutes: int        # 进入 + 恢复的过渡时长（共两段，各占一半）

    # 数值偏移（相对于基线的差值）
    temp_delta: float = 0.0
    humidity_delta: float = 0.0
    lux_delta: float = 0.0

    # 数值上限保护（扰动叠加后不能超出物理范围）
    temp_floor: float | None = None
    temp_ceil: float | None = None
    humidity_floor: float | None = None
    humidity_ceil: float | None = None
    lux_floor: float | None = None
    lux_ceil: float | None = None

    noise_std: float = 0.0  # 叠加在基线上的高斯噪声标准差

    @property
    def persist_minutes(self) -> int:
        """进入和恢复之间的稳定持续时长。"""
        return max(0, self.duration_minutes - 2 * self.transition_minutes)


def _build_disturbances(seed: int) -> list[Disturbance]:
    """根据随机种子构建 5 个扰动片段，在 7 天内分散分布。

    种子决定片段数量和位置，保证同一 seed 产生同一序列（B08 回归验证要求）。
    扰动包含【进入 → 持续 → 恢复】三段式过渡。
    """
    rng = random.Random(seed)
    total_minutes = SAMPLE_DAYS * 24 * 60

    # 5 个场景类型，分散在 7 天里
    scenarios: list[dict] = [
        {
            "name": "heat_wave",
            "temp_delta": +12.0,
            "humidity_delta": -25.0,
            "duration_hours": 18,
            "transition_hours": 4,
            "temp_ceil": 48.0,
            "humidity_floor": 20.0,
        },
        {
            "name": "drought",
            "temp_delta": +6.0,
            "humidity_delta": -10.0,
            "duration_hours": 24,
            "transition_hours": 6,
            "temp_ceil": 44.0,
            "humidity_floor": 15.0,
        },
        {
            "name": "shade",
            "lux_delta": -40000.0,
            "duration_hours": 12,
            "transition_hours": 3,
            "lux_floor": 0.0,
        },
        {
            "name": "cold_snap",
            "temp_delta": -10.0,
            "humidity_delta": +15.0,
            "duration_hours": 20,
            "transition_hours": 5,
            "temp_floor": 5.0,
            "humidity_ceil": 95.0,
        },
        {
            "name": "flood",
            "humidity_delta": +20.0,
            "lux_delta": -25000.0,
            "duration_hours": 16,
            "transition_hours": 4,
            "humidity_ceil": 100.0,
            "lux_floor": 0.0,
        },
    ]

    disturbances: list[Disturbance] = []
    # 用固定间隔确保分散，但加一点随机偏移
    base_interval = total_minutes // (len(scenarios) + 1)

    for i, s in enumerate(scenarios):
        # 以固定间隔为基准，加一个小随机偏移（±12小时以内）
        offset = rng.randint(-720, 720)
        start = base_interval * (i + 1) + offset
        start = max(60, min(start, total_minutes - s["duration_hours"] * 60))

        disturbances.append(Disturbance(
            name=s["name"],
            start_minute=start,
            duration_minutes=s["duration_hours"] * 60,
            transition_minutes=s["transition_hours"] * 60,
            temp_delta=s.get("temp_delta", 0.0),
            humidity_delta=s.get("humidity_delta", 0.0),
            lux_delta=s.get("lux_delta", 0.0),
            temp_floor=s.get("temp_floor"),
            temp_ceil=s.get("temp_ceil"),
            humidity_floor=s.get("humidity_floor"),
            humidity_ceil=s.get("humidity_ceil"),
            lux_floor=s.get("lux_floor"),
            lux_ceil=s.get("lux_ceil"),
            noise_std=0.3,
        ))

    return disturbances


def _clamp(val: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and val < lo: return lo
    if hi is not None and val > hi: return hi
    return val


def _smoothstep(t: float) -> float:
    """平滑过渡函数，输入 [0,1]，输出 [0,1]，两端斜率为 0。

    t <= 0 → 0
    t >= 1 → 1
    0 < t < 1 → 三次贝塞尔曲线，中间导数最大
    """
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------
# 样本数据类
# --------------------------------------------------------------------------
@dataclass
class Sample:
    timestamp: datetime
    temperature: float
    humidity: float
    lux: float
    source_type: str = "simulated"
    sim_scenario: str = "normal"


# --------------------------------------------------------------------------
# 模拟器
# --------------------------------------------------------------------------
class GreenhouseSimulator:
    """7 天 × 336 点模拟数据生成器。

    参数：
        seed: 随机种子。同一 seed 产生同一序列，用于回归验证。
        start_time: 时间线起点。默认 None 则取当前时刻并对齐到 30 分钟整点。
    """

    def __init__(self, seed: int = 42, start_time: datetime | None = None):
        self.seed = seed
        self.start_time = start_time or self._align_now()
        self.disturbances = _build_disturbances(seed)
        self._noise_rng = random.Random(seed + 9999)  # 独立于主序列的噪声序列

    def _align_now(self) -> datetime:
        """把当前时间对齐到 30 分钟整点（向下取整）。"""
        now = datetime.now().replace(second=0, microsecond=0)
        minutes = (now.hour * 60 + now.minute) // SAMPLE_INTERVAL_MINUTES * SAMPLE_INTERVAL_MINUTES
        return now.replace(hour=minutes // 60, minute=minutes % 60)

    def _baseline_temp(self, t: datetime) -> float:
        """基线温度：三角函数，午夜低、下午 14 点高峰。"""
        hour = t.hour + t.minute / 60.0
        # 以 TEMP_PEAK_HOUR 为对称轴，余弦曲线
        phase = (hour - TEMP_PEAK_HOUR) * (math.pi / 12)
        return TEMP_NIGHT + 0.5 * (TEMP_DAY_PEAK - TEMP_NIGHT) * (1 + math.cos(phase))

    def _baseline_humidity(self, t: datetime) -> float:
        """基线湿度：与温度大致负相关。"""
        hour = t.hour + t.minute / 60.0
        phase = (hour - TEMP_PEAK_HOUR) * (math.pi / 12)
        base = HUMIDITY_NIGHT - 0.5 * (HUMIDITY_NIGHT - HUMIDITY_DAY_LOW) * (1 + math.cos(phase))
        return _clamp(base, HUMIDITY_MIN, HUMIDITY_MAX)

    def _baseline_lux(self, t: datetime) -> float:
        """基线光照：正弦曲线，夜间 0，日出渐增，正午峰值，日落后归零。"""
        hour = t.hour + t.minute / 60.0
        if hour < LUX_SUNRISE_HR or hour > LUX_SUNSET_HR:
            return 0.0
        # 半正弦，从日出到日落
        t_norm = (hour - LUX_SUNRISE_HR) / (LUX_SUNSET_HR - LUX_SUNRISE_HR)  # [0,1]
        return LUX_DAY_PEAK * math.sin(t_norm * math.pi)

    def _disturbance_factor(self, minute_of_day: float) -> tuple[str, float]:
        """返回当前时刻的场景名和扰动强度因子（0=正常，1=完全扰动）。"""
        best: tuple[str, float] = ("normal", 0.0)
        for d in self.disturbances:
            if d.start_minute <= minute_of_day < d.start_minute + d.duration_minutes:
                elapsed = minute_of_day - d.start_minute
                if elapsed < d.transition_minutes:
                    # 进入阶段
                    strength = _smoothstep(elapsed / d.transition_minutes)
                elif elapsed > d.duration_minutes - d.transition_minutes:
                    # 恢复阶段
                    t = (d.duration_minutes - elapsed) / d.transition_minutes
                    strength = _smoothstep(t)
                else:
                    # 稳定持续段
                    strength = 1.0

                if strength > best[1]:
                    best = (d.name, strength)
                break  # 同一时刻只取最强的那个扰动（不叠加多个场景）
        return best

    def _sample_at(self, t: datetime, minute_of_day: float) -> Sample:
        base_temp = self._baseline_temp(t)
        base_humidity = self._baseline_humidity(t)
        base_lux = self._baseline_lux(t)

        scenario, strength = self._disturbance_factor(minute_of_day)

        temp = base_temp + strength * (self._disturbance_delta(scenario, "temp") or 0)
        humidity = base_humidity + strength * (self._disturbance_delta(scenario, "humidity") or 0)
        lux = base_lux + strength * (self._disturbance_delta(scenario, "lux") or 0)

        # 物理范围保护
        temp = _clamp(temp, TEMP_MIN, TEMP_MAX)
        humidity = _clamp(humidity, HUMIDITY_MIN, HUMIDITY_MAX)
        lux = _clamp(lux, LUX_MIN, LUX_MAX)

        # 叠加噪声
        if strength == 0.0:
            noise_std = 0.2
        else:
            noise_std = 0.5
        temp += self._noise_rng.gauss(0, noise_std)
        humidity += self._noise_rng.gauss(0, noise_std)
        if lux > 100 and strength > 0.1:
            lux += self._noise_rng.gauss(0, 500)
        elif lux > 100:
            lux += self._noise_rng.gauss(0, 100)

        temp = _clamp(temp, TEMP_MIN, TEMP_MAX)
        humidity = _clamp(humidity, HUMIDITY_MIN, HUMIDITY_MAX)
        lux = _clamp(lux, LUX_MIN, LUX_MAX)

        return Sample(
            timestamp=t,
            temperature=round(temp, 1),
            humidity=round(humidity, 1),
            lux=round(lux, 0),
            source_type="simulated",
            sim_scenario=scenario,
        )

    def _disturbance_delta(self, scenario: str, field: Literal["temp", "humidity", "lux"]) -> float | None:
        for d in self.disturbances:
            if d.name == scenario:
                return getattr(d, f"{field}_delta", 0.0) or 0.0
        return None

    def generate(self) -> list[Sample]:
        """生成 336 个样本，返回按时间顺序排列的 Sample 列表。"""
        samples: list[Sample] = []
        current = self.start_time
        minute_offset = 0

        for i in range(SAMPLE_COUNT):
            sample = self._sample_at(current, minute_offset)
            samples.append(sample)
            current += timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
            minute_offset += SAMPLE_INTERVAL_MINUTES

        return samples

    def summary(self) -> dict:
        """生成数据摘要，用于报告和校验。"""
        samples = self.generate()
        temps = [s.temperature for s in samples]
        humidities = [s.humidity for s in samples]
        luxes = [s.lux for s in samples]

        scenario_counts: dict[str, int] = {}
        for s in samples:
            scenario_counts[s.sim_scenario] = scenario_counts.get(s.sim_scenario, 0) + 1

        return {
            "start_time": samples[0].timestamp.isoformat(),
            "end_time": samples[-1].timestamp.isoformat(),
            "count": len(samples),
            "temperature": {"min": min(temps), "max": max(temps), "mean": round(sum(temps) / len(temps), 2)},
            "humidity": {"min": min(humidities), "max": max(humidities), "mean": round(sum(humidities) / len(humidities), 2)},
            "lux": {"min": min(luxes), "max": max(luxes), "mean": round(sum(luxes) / len(luxes), 2)},
            "scenarios": scenario_counts,
            "disturbance_count": len(self.disturbances),
        }


if __name__ == "__main__":
    sim = GreenhouseSimulator(seed=42)
    result = sim.summary()
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, indent=2, ensure_ascii=False))
