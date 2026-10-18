"""B03 Step 1 — 天气 API 接入：外部环境证据

目标：根据 location + forecast_window，获取真实天气数据，
输出 weather_evidence 结构，作为 B01 的外部环境证据注入。

数据源：Open-Meteo 免费天气 API（无需 API Key）
https://open-meteo.com/en/docs

weather_evidence 结构：
{
    "location": str,           # 经纬度或城市名
    "forecast_window": str,    # 预测窗口描述
    "timestamp": str,          # ISO 8601
    "temperature": float,      # °C，当前温度
    "humidity": float,         # %，相对湿度
    "precipitation_probability": float,  # %，降水概率
    "cloud_cover": float,      # %，云量
    "solar_radiation": float,  # W/m²，短波太阳辐射
    "weather_code": int,       # WMO 天气代码
    "weather_description": str # 天气描述
}




误差说明：
- 温度误差 ±1°C，湿度 ±5%
- 降水概率为概率预测，非实测
- 太阳辐射仅白天有值，夜间为 0

---
## Open-Meteo API 参考

### Endpoint
  https://api.open-meteo.com/v1/forecast    # 天气预报（免费，无需 Key）
  https://archive-api.open-meteo.com/v1/archive  # 历史数据
  https://air-quality-api.open-meteo.com/v1/air-quality  # 空气质量
  https://geocoding-api.open-meteo.com/v1/search  # 地址 geocoding

### 认证
  免费版：无 Key，无需认证，不限次数（见配额限制）

### 必填参数
  latitude   float   经纬度（-90~90）
  longitude  float   经纬度（-180~180）

### 常用选填参数
  current            实时数据字段（逗号分隔）
                      temperature_2m, relative_humidity_2m, precipitation_probability,
                      cloud_cover, shortwave_radiation, weather_code, wind_speed_10m
  hourly             每小时数据字段（逗号分隔）
  daily              每日数据字段（逗号分隔）
  timezone           时区，如 Asia/Shanghai、UTC
  start_date         开始日期 YYYY-MM-DD
  end_date           结束日期 YYYY-MM-DD
  forecast_days      预报天数 1-16（免费版最大16天）
  temperature_unit   温度单位：celsius（默认）/ fahrenheit / kelvin
  wind_speed_unit    风速单位：kmh / ms / mph / knot

### 配额限制（非商业免费版）
  每分钟：600 次
  每小时：5,000 次
  每天：  10,000 次
  每月：  300,000 次

### 错误码
  401  Unauthorized      - 不适用于免费版（无认证）
  429  Too Many Requests - 超出配额，暂停后自动恢复
  500  Server Error      - Open-Meteo 服务端错误，指数退避重试
  timeout               - 网络超时，默认 10s，建议重试 3 次

### 商业版（付费，€29/月起）
  专属 endpoint，无配额限制，SLA 保证
  申请：https://open-meteo.com/en/pricing

Sources:
- https://open-meteo.com/en/docs
- https://dev.to/0012303/open-meteo-has-a-free-weather-api-no-key-no-signup-real-forecast-data-2nna
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# WMO 天气代码映射
WMO_CODES: dict[int, str] = {
    0:  "晴朗",
    1:  "基本晴朗",
    2:  "多云",
    3:  "阴天",
    45: "雾",
    48: "霜雾",
    51: "小毛毛雨",
    53: "中毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中阵雨",
    82: "大阵雨",
    85: "小阵雪",
    86: "大阵雪",
    95: "雷暴",
    96: "雷暴+小冰雹",
    99: "雷暴+大冰雹",
}

# 预置温室大棚位置（北京地区示例）
GREENHOUSE_LOCATIONS: dict[str, tuple[float, float]] = {
    "北京大兴": (39.73, 116.33),
    "北京顺义": (40.13, 116.65),
    "河北固安": (39.44, 116.30),
    "山东寿光": (36.88, 118.79),
    "上海崇明": (31.62, 121.45),
    "浙江嘉善": (30.85, 120.93),
    "广东广州": (23.16, 113.23),
    "海南三亚": (18.25, 109.51),
    "江苏南京": (32.06, 118.79),
}


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class WeatherEvidence:
    """天气证据结构，对应 B01 的外部环境证据注入格式"""
    location: str
    forecast_window: str
    timestamp: str
    temperature: float       # °C
    humidity: float          # %
    precipitation_probability: float  # %
    cloud_cover: float       # %
    solar_radiation: float   # W/m²
    weather_code: int
    weather_description: str
    retrieved_at: str = ""   # UTC 时间戳，API 拉取时刻
    provider: str = "Open-Meteo"

    def to_dict(self) -> dict:
        return {
            "location": self.location,
            "forecast_window": self.forecast_window,
            "timestamp": self.timestamp,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "precipitation_probability": self.precipitation_probability,
            "cloud_cover": self.cloud_cover,
            "solar_radiation": self.solar_radiation,
            "weather_code": self.weather_code,
            "weather_description": self.weather_description,
        }

    def summary(self) -> str:
        return (
            f"[{self.location}] {self.forecast_window}\n"
            f"  天气: {self.weather_description} (WMO {self.weather_code})\n"
            f"  温度: {self.temperature}°C | 湿度: {self.humidity}%\n"
            f"  降水概率: {self.precipitation_probability}% | 云量: {self.cloud_cover}%\n"
            f"  太阳辐射: {self.solar_radiation} W/m²\n"
            f"  更新时间: {self.timestamp}"
        )


# --------------------------------------------------------------------------
# 核心函数
# --------------------------------------------------------------------------

def get_weather(
    location: str | tuple[float, float],
    forecast_window: Literal["now", "1h", "3h", "6h", "12h", "1d", "3d"] = "now",
) -> WeatherEvidence:
    """获取指定位置和预测窗口的天气证据。

    参数：
        location: 预置城市名（如 "北京大兴"）或 (lat, lon) 元组
        forecast_window: 预测窗口
            - "now":      当前实况
            - "1h/3h/6h/12h": 未来 N 小时预测
            - "1d":       明天平均
            - "3d":       未来3天平均
    """
    # 解析经纬度
    if isinstance(location, str):
        if location not in GREENHOUSE_LOCATIONS:
            raise ValueError(f"未知地点: {location}，可选: {list(GREENHOUSE_LOCATIONS.keys())}")
        lat, lon = GREENHOUSE_LOCATIONS[location]
        loc_name = location
    else:
        lat, lon = location
        loc_name = f"{lat},{lon}"

    # 确定时间范围
    now = datetime.now(timezone.utc)
    if forecast_window == "now":
        start_dt = now
        end_dt = now + timedelta(hours=1)
        agg_func = "none"  # 取单点
    elif forecast_window == "1h":
        start_dt = now + timedelta(hours=1)
        end_dt = start_dt + timedelta(minutes=30)
        agg_func = "none"
    elif forecast_window == "3h":
        start_dt = now + timedelta(hours=3)
        end_dt = start_dt + timedelta(minutes=30)
        agg_func = "none"
    elif forecast_window == "6h":
        start_dt = now + timedelta(hours=6)
        end_dt = start_dt + timedelta(hours=1)
        agg_func = "none"
    elif forecast_window == "12h":
        start_dt = now + timedelta(hours=12)
        end_dt = start_dt + timedelta(hours=1)
        agg_func = "none"
    elif forecast_window == "1d":
        start_dt = now + timedelta(days=1)
        end_dt = start_dt + timedelta(hours=23, minutes=59)
        agg_func = "mean"
    elif forecast_window == "3d":
        start_dt = now + timedelta(days=1)
        end_dt = start_dt + timedelta(days=3)
        agg_func = "mean"
    else:
        raise ValueError(f"未知 forecast_window: {forecast_window}")

    # 调用 Open-Meteo API
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
        "timezone": "Asia/Shanghai",
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    }

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(OPEN_METEO_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()

    current = data.get("current", {})
    if not current:
        raise RuntimeError(f"API 返回数据异常，缺少 current 字段: {data}")

    temp = float(current.get("temperature_2m", 0))
    humid = float(current.get("relative_humidity_2m", 0))
    precip = float(current.get("precipitation_probability", 0))
    cloud = float(current.get("cloud_cover", 0))
    radiation = float(current.get("shortwave_radiation", 0))
    code = int(current.get("weather_code", 0))

    return WeatherEvidence(
        location=loc_name,
        forecast_window=forecast_window,
        timestamp=current.get("time", "") or now.isoformat(),
        temperature=round(temp, 1),
        humidity=round(humid, 1),
        precipitation_probability=round(precip, 1),
        cloud_cover=round(cloud, 1),
        solar_radiation=round(radiation, 1),
        weather_code=code,
        weather_description=WMO_CODES.get(code, f"未知({code})"),
    )


# --------------------------------------------------------------------------
# 测试入口
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== B03 天气 API 接入测试 ===\n")

    for loc in ["北京大兴", "山东寿光", "广东广州"]:
        try:
            we = get_weather(loc, "now")
            print(we.summary())
            print()
        except Exception as e:
            print(f"  {loc} 获取失败: {e}\n")
