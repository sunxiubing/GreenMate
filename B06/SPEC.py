"""B06 Step 1 — 时间窗口构造 + DTW 波形相似度

数据来源：Dataset，SIMULATED / PUBLIC
数据读取：B02 environment_samples，按 timestamp 升序排序

窗口规则：
  - 输入历史窗口：连续 6 个采样点（3 小时，采样间隔 30min）
  - 预测目标窗口：紧接着未来 1～2 小时（2～4 个采样点）
  - 跨边界窗口直接丢弃（不保留跨扰动边界的窗口）
  - window_size 可配置，默认 6

DTW 计算：
  - 当前窗口与历史保存窗口做时序波形对齐
  - 计算序列相似度（DTW distance）
  - 相似度作为新增特征送入模型

输出结构：
  {
    "window_id": str,
    "source": "SIMULATED" | "PUBLIC",
    "history": [...6点...],   # timestamp, temperature, humidity, lux
    "target": [...2-4点...],  # timestamp, temperature, humidity, lux
    "dtw_temperature": float,
    "dtw_humidity": float,
    "dtw_lux": float,
  }
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
WINDOW_SIZE: int = 6          # 输入历史窗口点数（可配置，默认 6 点 = 3 小时）
TARGET_SIZE: int = 2         # 预测目标窗口点数（默认 2 点 = 1 小时）
DTW_COMPUTE: bool = True    # 是否计算 DTW（False 时跳过，节省时间）
DTW_WINDOW_LIMIT: int = 50   # DTW 比对的历史窗口上限（避免 O(n²) 爆炸）

# 数据来源标注
SourceType = Literal["SIMULATED", "PUBLIC"]

# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class Sample:
    """单个环境采样点"""
    timestamp: datetime
    temperature: float   # °C
    humidity: float      # %
    lux: float           # lux
    source_type: SourceType
    sim_scenario: str = ""


@dataclass
class TimeWindow:
    """一个时间窗口"""
    window_id: str
    source: SourceType
    history: list[Sample]      # WINDOW_SIZE 个点
    target: list[Sample]       # TARGET_SIZE 个点
    dtw_temperature: float = -1.0
    dtw_humidity: float = -1.0
    dtw_lux: float = -1.0

    def to_dict(self) -> dict:
        return {
            "window_id": self.window_id,
            "source": self.source,
            "history": [
                {"timestamp": s.timestamp.isoformat(), "temperature": s.temperature,
                 "humidity": s.humidity, "lux": s.lux, "sim_scenario": s.sim_scenario}
                for s in self.history
            ],
            "target": [
                {"timestamp": s.timestamp.isoformat(), "temperature": s.temperature,
                 "humidity": s.humidity, "lux": s.lux}
                for s in self.target
            ],
            "dtw_temperature": round(self.dtw_temperature, 4),
            "dtw_humidity": round(self.dtw_humidity, 4),
            "dtw_lux": round(self.dtw_lux, 4),
        }


# --------------------------------------------------------------------------
# DTW 算法（纯 Python 实现，不依赖 dtaidistance）
# --------------------------------------------------------------------------

def dtw_distance(seq_a: list[float], seq_b: list[float], window: int | None = None) -> float:
    """计算两条时序的 DTW 距离。

    使用 Sakoe-Chiba 带约束（window = max shift）避免全量 O(n²) 膨胀。
    window=None 时使用全局比对。

    返回：DTW distance（越小越相似）
    """
    n, m = len(seq_a), len(seq_b)
    if n == 0 or m == 0:
        return float("inf")

    # Sakoe-Chiba band width
    w = window if window is not None else max(n, m)

    # 初始化距离矩阵（仅保留两行以节省内存）
    INF = float("inf")
    dtw_prev = [INF] * (m + 1)
    dtw_curr = [INF] * (m + 1)
    dtw_prev[0] = 0.0

    for i in range(1, n + 1):
        dtw_curr[0] = INF
        j_start = max(1, i - w)
        j_end = min(m, i + w)
        for j in range(j_start, j_end + 1):
            cost = abs(seq_a[i - 1] - seq_b[j - 1])
            dtw_curr[j] = cost + min(dtw_prev[j], dtw_curr[j - 1], dtw_prev[j - 1])
        dtw_prev, dtw_curr = dtw_curr, dtw_prev

    return dtw_prev[m]


def compute_dtw(window: TimeWindow, history_windows: list[TimeWindow]) -> TimeWindow:
    """将当前窗口与历史窗口做 DTW 比对，更新 dtw_temperature/humidity/lux。"""
    if not history_windows:
        return window

    def avg_dtw(attr: str) -> float:
        """当前窗口与所有历史窗口 attr 字段的平均 DTW 距离"""
        vals = []
        for hw in history_windows[:DTW_WINDOW_LIMIT]:
            seq_a = [getattr(s, attr) for s in window.history]
            seq_b = [getattr(s, attr) for s in hw.history]
            vals.append(dtw_distance(seq_a, seq_b))
        return sum(vals) / len(vals) if vals else -1.0

    window.dtw_temperature = avg_dtw("temperature")
    window.dtw_humidity = avg_dtw("humidity")
    window.dtw_lux = avg_dtw("lux")
    return window
