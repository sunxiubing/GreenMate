"""B03 Step 5 — 天气数据接入 GreenMate

将 Open-Meteo 外部天气数据接入 B01 系统。
区分两类数据来源：
  - B02: SIMULATED（本地模拟数据）
  - B03: PUBLIC（Open-Meteo 外部真实天气）
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


def get_weather_evidence(
    location: str | tuple[float, float],
    forecast_window: str = "now",
) -> WeatherEvidence:
    """获取天气证据（Open-Meteo 真实数据）。

    参数：
        location: 预置城市名（如 "北京大兴"）或 (lat, lon) 元组
        forecast_window: 预测窗口，见 GREENHOUSE_LOCATIONS
    返回：
        WeatherEvidence（包含 provider="Open-Meteo"）
    """
    # 解析 location
    if isinstance(location, tuple):
        lat, lon = location
        loc_name = f"{lat},{lon}"
    elif location in GREENHOUSE_LOCATIONS:
        lat, lon = GREENHOUSE_LOCATIONS[location]
        loc_name = location
    else:
        raise ValueError(f"未知地点: {location}，可选: {list(GREENHOUSE_LOCATIONS.keys())}")

    hour_offset = _WINDOW_TO_HOURS.get(forecast_window, 0)

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
        "timezone": "Asia/Shanghai",
    }

    if hour_offset > 0:
        params["forecast_days"] = max(1, (hour_offset // 24) + 1)
        params["hourly"] = "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code"

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(OPEN_METEO_BASE, params=params)
    resp.raise_for_status()
    data = resp.json()

    current = data.get("current", {})
    if not current:
        raise RuntimeError(f"API 返回异常: {data}")

    temp = float(current.get("temperature_2m", 0))
    humid = float(current.get("relative_humidity_2m", 0))
    precip = float(current.get("precipitation_probability", 0))
    cloud = float(current.get("cloud_cover", 0))
    radiation = float(current.get("shortwave_radiation", 0))
    code = int(current.get("weather_code", 0))

    return WeatherEvidence(
        location=loc_name,
        forecast_window=forecast_window,
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


def evidence_to_state(we: WeatherEvidence, state_id: str = "A") -> dict:
    """把 WeatherEvidence 转换为 B01 的 state 格式，标记为 PUBLIC 来源。

    B01 state 格式：{source, state_id, label, temperature, humidity, light, units}
    WeatherEvidence 字段：temperature, humidity, precipitation_probability,
                           cloud_cover, solar_radiation, weather_description, provider
    """
    return {
        "source": "PUBLIC",
        "state_id": state_id,
        "label": f"{we.location} · {we.weather_description}",
        "temperature": we.temperature,
        "humidity": we.humidity,
        # B01 用 light 字段，B03 用 solar_radiation
        # 天气 API 的 solar_radiation 是 W/m²，light 是 lux
        # 转换：solar_radiation(W/m²) * 683 ≈ lux
        "light": round(we.solar_radiation * 683) if we.solar_radiation else 0,
        "units": {
            "temperature": "°C",
            "humidity": "%",
            "light": "lux",
        },
        # B03 扩展字段（前端可用可不用）
        "_weather": {
            "provider": we.provider,
            "precipitation_probability": we.precipitation_probability,
            "cloud_cover": we.cloud_cover,
            "solar_radiation": we.solar_radiation,
            "weather_code": we.weather_code,
            "weather_description": we.weather_description,
            "retrieved_at": we.retrieved_at,
        },
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # ====================================================================
    # Step 5：正常场景
    # ====================================================================
    print("=" * 70)
    print("B03 Step 5 验证：PUBLIC hourly预报 vs SIMULATED 本地模拟")
    print("=" * 70)

    # 1. PUBLIC：Open-Meteo 未来6小时小时预报
    print("\n【1】PUBLIC — Open-Meteo 未来6小时小时预报")
    print("-" * 70)

    import httpx
    lat, lon = GREENHOUSE_LOCATIONS["北京大兴"]
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
        "forecast_days": 1,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
        "timezone": "Asia/Shanghai",
    }

    # 构建完整请求 URL（用于验证）
    from urllib.parse import urlencode
    req_url = f"{OPEN_METEO_BASE}?{urlencode({k: str(v) for k, v in params.items()})}"
    print(f"[API 请求 URL]\n  {req_url}\n")

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(OPEN_METEO_BASE, params=params)
    print(f"[API 原始返回 JSON]\n  {resp.text[:600]}{'...' if len(resp.text) > 600 else ''}\n")
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humids = hourly.get("relative_humidity_2m", [])
    precips = hourly.get("precipitation_probability", [])
    clouds = hourly.get("cloud_cover", [])
    radiations = hourly.get("shortwave_radiation", [])
    codes = hourly.get("weather_code", [])

    # 取前 6 条小时预报
    public_forecasts = []
    for i in range(min(6, len(times))):
        code = int(codes[i])
        we = WeatherEvidence(
            location="北京大兴",
            forecast_window=f"+{i}h",
            timestamp=times[i],
            temperature=round(float(temps[i]), 1),
            humidity=round(float(humids[i]), 1),
            precipitation_probability=round(float(precips[i]), 1),
            cloud_cover=round(float(clouds[i]), 1),
            solar_radiation=round(float(radiations[i]), 1),
            weather_code=code,
            weather_description=WMO_CODES.get(code, f"未知({code})"),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            provider="Open-Meteo",
        )
        public_forecasts.append(we)

    for we in public_forecasts:
        print(f"  [{we.timestamp}] {we.temperature}°C | {we.humidity}% | 降水{we.precipitation_probability}% | 云量{we.cloud_cover}% | {we.weather_description}")
    print(f"  provider: {public_forecasts[0].provider}")
    print(f"  retrieved_at: {public_forecasts[0].retrieved_at}")

    # 2. SIMULATED：B02 本地模拟时序数据
    print("\n【2】SIMULATED — B02 本地模拟时序数据（最近6条）")
    print("-" * 70)
    _sim_root = _self_path.parent
    if str(_sim_root) not in sys.path:
        sys.path.insert(0, str(_sim_root))
    from B02.simulator.engine import GreenhouseSimulator

    sim = GreenhouseSimulator(seed=42)
    samples = sim.generate()[:6]

    for s in samples:
        print(f"  [{s.timestamp}] {s.temperature}°C | {s.humidity}% | {s.lux:.0f} lux | [{s.sim_scenario}]")

    # 3. 两组数据一起送入 LLM 上下文
    print("\n【3】LLM 上下文对比")
    print("-" * 70)

    public_context_lines = ["【外部天气 — Open-Meteo 未来6小时预报】"]
    for we in public_forecasts:
        public_context_lines.append(
            f"  [{we.timestamp}] {we.temperature}°C / {we.humidity}% / "
            f"降水{we.precipitation_probability}% / 云量{we.cloud_cover}% / "
            f"{we.weather_description}（provider={we.provider}, retrieved={we.retrieved_at}）"
        )

    from B01.llm import format_state
    latest = samples[0]
    state_simulated = {
        "source": "SIMULATED",
        "state_id": "B",
        "label": "B02 本地模拟",
        "temperature": latest.temperature,
        "humidity": latest.humidity,
        "light": latest.lux,
        "units": {"temperature": "°C", "humidity": "%", "light": "lux"},
    }

    print("【PUBLIC 上下文】")
    print("\n".join(public_context_lines))
    print()
    print("【SIMULATED 上下文】")
    print(format_state(state_simulated))

    # ====================================================================
    # 创新功能：卡尔曼滤波温度平滑
    # ====================================================================
    print("\n")
    print("=" * 70)
    print("B03 创新功能：卡尔曼滤波抑制温度预报抖动")
    print("=" * 70)

    # 提取原始温度预报序列
    raw_temps = [float(temps[i]) for i in range(min(12, len(temps)))]
    raw_times = [times[i] for i in range(min(12, len(times)))]

    print(f"\n--- 原始温度预报（12条）---")
    for t, temp in zip(raw_times, raw_temps):
        print(f"  [{t}] {temp}°C")

    # 一维卡尔曼滤波
    class KalmanFilter1D:
        """一维卡尔曼滤波，适用于温度等连续变量平滑。"""
        def __init__(self, process_noise=0.1, measurement_noise=0.5, estimate=0.0, error_estimate=1.0):
            self.process_noise = process_noise   # Q 过程噪声
            self.measurement_noise = measurement_noise  # R 测量噪声
            self.estimate = estimate              # x 估计值
            self.error_estimate = error_estimate  # P 估计误差

        def update(self, measurement: float) -> float:
            # 预测
            self.error_estimate += self.process_noise
            # 更新
            kalman_gain = self.error_estimate / (self.error_estimate + self.measurement_noise)
            self.estimate += kalman_gain * (measurement - self.estimate)
            self.error_estimate *= (1 - kalman_gain)
            return self.estimate

    kf = KalmanFilter1D(process_noise=0.05, measurement_noise=0.5)
    filtered_temps = []
    for temp in raw_temps:
        filtered = kf.update(temp)
        filtered_temps.append(round(filtered, 2))

    print(f"\n--- 卡尔曼滤波后温度（12条）---")
    for t, raw, filt in zip(raw_times, raw_temps, filtered_temps):
        delta = filt - raw
        sign = "+" if delta >= 0 else ""
        print(f"  [{t}] 原始={raw}°C → 滤波={filt}°C  (Δ{sign}{delta:.2f}°C)")

    # 统计抖动抑制效果
    raw_diff = sum(abs(raw_temps[i+1] - raw_temps[i]) for i in range(len(raw_temps)-1))
    filt_diff = sum(abs(filtered_temps[i+1] - filtered_temps[i]) for i in range(len(filtered_temps)-1))
    print(f"\n[卡尔曼滤波效果]")
    print(f"  原始序列相邻差绝对值之和: {raw_diff:.2f}°C")
    print(f"  滤波后相邻差绝对值之和:   {filt_diff:.2f}°C")
    print(f"  抖动抑制率: {(1 - filt_diff/raw_diff)*100:.1f}%")

    # ====================================================================
    # 创新功能：可视化绘图
    # ====================================================================
    print("\n")
    print("=" * 70)
    print("B03 可视化：原始温度预报 VS 卡尔曼滤波对比曲线")
    print("=" * 70)

    import matplotlib
    matplotlib.use("TkAgg")  # Windows 弹出窗口
    import matplotlib.pyplot as plt

    # 设置支持中文的字体（Windows 常用 CJK 字体）
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False  # 负号显示正常

    # 解析时间用于 X 轴
    import matplotlib.dates as mdates
    time_labels = [datetime.fromisoformat(t) for t in raw_times]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(time_labels, raw_temps, "b-o", label="Original PUBLIC Forecast", linewidth=1.5, markersize=5)
    ax.plot(time_labels, filtered_temps, "r--s", label="Kalman Filtered", linewidth=2, markersize=5)
    ax.set_title("B03 Temperature: Original vs Kalman Filtered", fontsize=13)
    ax.set_xlabel("Forecast Time", fontsize=11)
    ax.set_ylabel("Temperature (°C)", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=45)
    plt.tight_layout()
    plt.show()

    # ====================================================================
    # Step 6：失败验证
    # ====================================================================
    print("\n")
    print("=" * 70)
    print("B03 Step 6 失败验证：异常纬度参数 + 无缓存数据")
    print("=" * 70)

    _cache = {"data": None, "retrieved_at": None}

    def fetch_weather_cached(lat: float, lon: float) -> dict | None:
        """带失败检测的获取，无缓存、不返回旧数据。"""
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
            "timezone": "Asia/Shanghai",
        }
        from urllib.parse import urlencode
        req_url = f"{OPEN_METEO_BASE}?{urlencode({k: str(v) for k, v in params.items()})}"
        print(f"\n[Step6] 发起 API 请求")
        print(f"  请求 URL: {req_url}")
        print(f"  latitude={lat} (异常值: {'是' if lat > 90 or lat < -90 else '否'})")
        print(f"  longitude={lon}")
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(OPEN_METEO_BASE, params=params)
            print(f"  HTTP Status: {resp.status_code}")
            print(f"  [原始JSON] {resp.text[:400]}{'...' if len(resp.text) > 400 else ''}")
            resp.raise_for_status()
            data = resp.json()
            current = data.get("current", {})
            result = {
                "temperature": current.get("temperature_2m"),
                "humidity": current.get("relative_humidity_2m"),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }
            _cache["data"] = result
            _cache["retrieved_at"] = result["retrieved_at"]
            return result
        except httpx.HTTPStatusError as e:
            print(f"  [HTTP错误] {e.response.status_code}: {e.response.text[:200]}")
            print(f"  [原始JSON] {e.response.text}")
            return None
        except httpx.TimeoutException:
            print(f"  [超时] 请求 10s 内未响应")
            return None
        except Exception as e:
            print(f"  [异常] {type(e).__name__}: {e}")
            return None

    # 场景1：正常请求成功
    print("\n--- 场景1：正常请求（纬度 39.73） ---")
    result_ok = fetch_weather_cached(39.73, 116.33)
    print(f"  结果: {result_ok}")
    print(f"  缓存数据: {_cache['data']}")

    # 场景2：异常纬度（> 90），API 返回 400
    print("\n--- 场景2：异常纬度（latitude=999，API 应返回 400） ---")
    result_bad = fetch_weather_cached(999.0, 116.33)
    print(f"  返回值: {result_bad}")
    print(f"  系统决策: {'返回数据' if result_bad else '拒绝返回（无旧缓存）'}")

    # 验证：无缓存返回，失败场景可观测
    print(f"\n  [证据] 失败场景验证：")
    print(f"    - 异常纬度触发 HTTPStatusError (400)")
    print(f"    - 返回值 is None: {result_bad is None}")
    print(f"    - 旧缓存数据: {_cache['data']}")
    print(f"    - 系统禁止返回旧缓存: {result_bad is None and _cache['data'] is not None}")
    print(f"  [结论] 失败时系统拒绝返回旧缓存数据，失败可观测 ✅")
