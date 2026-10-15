"""B03 Step 3/4 — 天气 API 调用

支持两种调用方式：
- location: str  → 使用预置城市名（GREENHOUSE_LOCATIONS）
- location: tuple → 使用 (latitude, longitude) 经纬度

支持多小时预测（6h/12h/1d/3d）返回 List[WeatherEvidence]
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

_self_path = Path(__file__).resolve().parent
_b03_root = _self_path.parent
if str(_b03_root) not in sys.path:
    sys.path.insert(0, str(_b03_root))

import httpx
from B03.SPEC import GREENHOUSE_LOCATIONS, WMO_CODES, WeatherEvidence


OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

_WINDOW_TO_HOURS = {
    "now":  0,
    "1h":   1,
    "3h":   3,
    "6h":   6,
    "12h":  12,
    "1d":   24,
    "3d":   72,
}


def _resolve_location(location):
    """解析 location：支持城市名字符串或 (lat, lon) 元组"""
    if isinstance(location, tuple):
        return location, f"{location[0]},{location[1]}"
    if location in GREENHOUSE_LOCATIONS:
        return GREENHOUSE_LOCATIONS[location], location
    raise ValueError(f"未知地点: {location}，可选: {list(GREENHOUSE_LOCATIONS.keys())}")


def _api_request(params: dict) -> tuple[int, dict]:
    """发起请求，返回 (status_code, json_data)"""
    print(f"[B03] URL: {OPEN_METEO_BASE}")
    print(f"[B03] 参数: {params}")

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(OPEN_METEO_BASE, params=params)

    print(f"[B03] HTTP Status: {resp.status_code}")
    print(f"[B03] Raw JSON Response:")
    print(resp.text[:800] + ("..." if len(resp.text) > 800 else ""))
    print()

    resp.raise_for_status()
    return resp.status_code, resp.json()


def get_weather(
    location: str | tuple[float, float],
    forecast_window: str = "now",
) -> list[WeatherEvidence]:
    """获取天气证据，支持单点和多小时预测。

    - forecast_window="now" → 返回单条 [WeatherEvidence]
    - forecast_window="6h" 等 → 返回多条（每条间隔1小时）

    每次调用都打印完整 WeatherEvidence，包含 provider 和 retrieved_at。
    """
    (lat, lon), loc_name = _resolve_location(location)
    hour_offset = _WINDOW_TO_HOURS.get(forecast_window, 0)

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
        "timezone": "Asia/Shanghai",
    }

    if hour_offset == 0:
        # 当前实况：只用 current 参数
        status, data = _api_request(params)
        current = data.get("current", {})
        if not current:
            raise RuntimeError(f"API 返回异常，缺少 current 字段: {data}")

        return [_parse_current(current, loc_name, forecast_window)]

    else:
        # 未来N小时/天：forecast_days=1 + hourly 取特定小时
        forecast_days = max(1, (hour_offset // 24) + 1)
        params["forecast_days"] = forecast_days
        params["hourly"] = "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code"

        status, data = _api_request(params)

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            raise RuntimeError(f"API 返回异常，缺少 hourly.time 字段: {data}")

        # 取当前小时开始的 N 条记录（hourly 每小时一条，共24条）
        results = []
        for i in range(len(times)):
            results.append(_parse_hourly(hourly, i, loc_name, forecast_window))
            if len(results) >= hour_offset:
                break

        if not results:
            raise RuntimeError(f"未找到 {hour_offset}h 后的预测数据（hourly 共 {len(times)} 条）")

        if not results:
            raise RuntimeError(f"未找到 {hour_offset}h 后的预测数据")
        return results


def _parse_current(current: dict, loc_name: str, window: str) -> WeatherEvidence:
    temp = float(current.get("temperature_2m", 0))
    humid = float(current.get("relative_humidity_2m", 0))
    precip = float(current.get("precipitation_probability", 0))
    cloud = float(current.get("cloud_cover", 0))
    radiation = float(current.get("shortwave_radiation", 0))
    code = int(current.get("weather_code", 0))

    return WeatherEvidence(
        location=loc_name,
        forecast_window=window,
        timestamp=current.get("time", ""),
        temperature=round(temp, 1),
        humidity=round(humid, 1),
        precipitation_probability=round(precip, 1),
        cloud_cover=round(cloud, 1),
        solar_radiation=round(radiation, 1),
        weather_code=code,
        weather_description=WMO_CODES.get(code, f"未知({code})"),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        provider="Open-Meteo",
    )


def _parse_hourly(hourly: dict, idx: int, loc_name: str, window: str) -> WeatherEvidence:
    times = hourly.get("time", [])
    temp = float(hourly.get("temperature_2m", [])[idx] or 0)
    humid = float(hourly.get("relative_humidity_2m", [])[idx] or 0)
    precip = float(hourly.get("precipitation_probability", [])[idx] or 0)
    cloud = float(hourly.get("cloud_cover", [])[idx] or 0)
    radiation = float(hourly.get("shortwave_radiation", [])[idx] or 0)
    code = int(hourly.get("weather_code", [])[idx] or 0)

    return WeatherEvidence(
        location=loc_name,
        forecast_window=window,
        timestamp=times[idx],
        temperature=round(temp, 1),
        humidity=round(humid, 1),
        precipitation_probability=round(precip, 1),
        cloud_cover=round(cloud, 1),
        solar_radiation=round(radiation, 1),
        weather_code=code,
        weather_description=WMO_CODES.get(code, f"未知({code})"),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        provider="Open-Meteo",
    )


def _print_evidence(we: WeatherEvidence):
    print(we.summary())
    print(f"  retrieved_at: {we.retrieved_at}")
    print(f"  provider: {we.provider}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("测试A：南京 + 6h 预测（多条记录）")
    print("=" * 60)
    try:
        results = get_weather("江苏南京", "6h")
        print(f"\n共返回 {len(results)} 条预测记录：\n")
        for i, we in enumerate(results, 1):
            print(f"--- 第 {i} 条 ---")
            _print_evidence(we)
            print()
    except Exception as e:
        print(f"[B03] 错误: {e}\n")

    print("=" * 60)
    print("测试B：南京经纬度 (32.06, 118.79) + now")
    print("=" * 60)
    try:
        results = get_weather((32.06, 118.79), "now")
        print(f"\n共返回 {len(results)} 条：\n")
        for we in results:
            _print_evidence(we)
            print()
    except Exception as e:
        print(f"[B03] 错误: {e}\n")

    print("=" * 60)
    print("对比：北京大兴 + now")
    print("=" * 60)
    try:
        results = get_weather("北京大兴", "now")
        for we in results:
            _print_evidence(we)
    except Exception as e:
        print(f"[B03] 错误: {e}")
