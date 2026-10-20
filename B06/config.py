"""B06 配置常量

阈值说明（rule_v1）：
  阈值基于 B02 336 样本的实际分布确定初值，
  后续由 B06 Step 5 农业专家调整，禁止在验收前修改。
"""

from pathlib import Path

# --------------------------------------------------------------------------
# 标签规则版本
# --------------------------------------------------------------------------
LABEL_SOURCE = "rule_v1"   # 规则版本，透明可审计
RULE_VERSION = "1.0"

# --------------------------------------------------------------------------
# Step 3: 时间切分（以实际数据起始时间为界）
# --------------------------------------------------------------------------
# 数据范围：2026-10-15 02:00:00 → 2026-10-22 01:30:00（约 7 天）
# 前 5 天 = 训练集，后 2 天 = 测试集（不含验证集）
TRAIN_DAYS = 5          # 训练集天数
TEST_DAYS = 2           # 测试集天数
SPLIT_FROM_DATA_START = True   # 从数据实际起始时间算起
RANDOM_SEED = 42        # 随机种子，保证可复现（模型用到时）

# --------------------------------------------------------------------------
# Step 2: 标签阈值（rule_v1）
# --------------------------------------------------------------------------
# WATCH 触发条件：target 窗口内任一指标越界

# 温度越界阈值（°C）
TEMP_MIN = 10.0   # 低于此值触发 WATCH
TEMP_MAX = 40.0   # 高于此值触发 WATCH

# 湿度越界阈值（%）
HUM_MIN = 20.0    # 低于此值触发 WATCH（干旱）
HUM_MAX = 95.0    # 高于此值触发 WATCH（过湿）

# 光照越界阈值（lux）
LUX_MIN = 0.0     # 夜间 0 为正常，不作为 WATCH 条件
LUX_MAX = 80000.0 # 超过此值触发 WATCH（光抑制）

# sim_scenario 扰动覆盖（rule_v1 仍参考 sim_scenario 做标注，但特征中已排除）
SIM_SCENARIO_ABNORMAL = {
    "heat_wave",
    "drought",
    "shade",
    "cold_snap",
    "flood",
}

# --------------------------------------------------------------------------
# 特征列定义（白名单）
# --------------------------------------------------------------------------
FEATURE_COLUMNS: list[str] = [
    # 基础特征（8 项 × 3 字段）
    "current_temperature", "mean_temperature", "delta_temperature",
    "max_temperature", "min_temperature", "std_temperature",
    "median_temperature", "var_temperature",
    "current_humidity", "mean_humidity", "delta_humidity",
    "max_humidity", "min_humidity", "std_humidity",
    "median_humidity", "var_humidity",
    "current_lux", "mean_lux", "delta_lux",
    "max_lux", "min_lux", "std_lux",
    "median_lux", "var_lux",
    # 扩展特征（2 项 × 3 字段）
    "slope_temperature", "range_temperature",
    "slope_humidity", "range_humidity",
    "slope_lux", "range_lux",
    # DTW 相似度（Step 1 新增）
    "dtw_temperature", "dtw_humidity", "dtw_lux",
]

# --------------------------------------------------------------------------
# 禁止特征黑名单（泄漏检查）
# --------------------------------------------------------------------------
FEATURE_BLACKLIST: list[str] = [
    # 标签相关
    "label", "next_window_attention", "attention",
    # sim_scenario 相关（B02 元数据，不进特征）
    "sim_scenario", "scenario",
    # B03 天气数据（仅精准匹配未来窗口原始值）
    "target_temperature", "target_humidity", "target_lux",
    "future_temperature", "future_humidity", "future_lux",
    "next_temperature", "next_humidity", "next_lux",
    # B03 额外气象字段
    "precipitation", "cloud_cover",
    "solar_radiation", "weather_code",
    "weather_description", "provider",
    # label 相关
    "label_source", "label_reason",
]

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
