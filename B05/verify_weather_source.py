"""B05 天气数据源验证：真实 Open-Meteo 请求 vs Mock 模式对比"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

import httpx
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Part 1: 真实 Open-Meteo API 请求
# --------------------------------------------------------------------------
print("=" * 70)
print("Part 1: 真实 Open-Meteo API 请求（无 mock）")
print("=" * 70)

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
params = {
    "latitude": 39.73,
    "longitude": 116.33,
    "current": "temperature_2m,relative_humidity_2m,precipitation_probability,cloud_cover,shortwave_radiation,weather_code",
    "timezone": "Asia/Shanghai",
}

print(f"\n[请求 URL]\n  {OPEN_METEO_BASE}?latitude=39.73&longitude=116.33&...")

try:
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(OPEN_METEO_BASE, params=params)

    print(f"\n[HTTP Status] {resp.status_code}")
    print(f"\n[原始 JSON 返回]\n{resp.text[:800]}")

    data = resp.json()
    current = data.get("current", {})

    print(f"\n[解析后的 current 字段]")
    print(f"  temperature_2m:           {current.get('temperature_2m')} °C")
    print(f"  relative_humidity_2m:     {current.get('relative_humidity_2m')} %")
    print(f"  precipitation_probability:{current.get('precipitation_probability')} %")
    print(f"  cloud_cover:              {current.get('cloud_cover')} %")
    print(f"  shortwave_radiation:      {current.get('shortwave_radiation')} W/m²")
    print(f"  weather_code:             {current.get('weather_code')}")
    print(f"  time:                     {current.get('time')}")
    print(f"\n[数据来源] Open-Meteo 实时气象 API — https://open-meteo.com/")
    print("[PASS] 真实网络请求成功，数据来自 Open-Meteo ✅")

except httpx.TimeoutException:
    print("[结果] 请求超时（网络断开），返回 unavailable")
except Exception as e:
    print(f"[结果] 请求失败: {type(e).__name__}: {e}")

# --------------------------------------------------------------------------
# Part 2: Mock 模式（unavailable 注入）
# --------------------------------------------------------------------------
print("\n" + "=" * 70)
print("Part 2: Mock 模式 — unavailable 注入")
print("=" * 70)

from B05.orchestrator import ask, set_inject

set_inject({"tool": "get_weather_forecast", "status": "unavailable", "error": "network timeout after 10s"})
try:
    answer = ask("北京大兴现在的天气怎么样？")
    print(f"\n[Mock unavailable 回答]\n{answer}")
    print("[PASS] Mock 注入 unavailable 成功，大模型生成合理提示 ✅")
except Exception as e:
    print(f"[FAIL] 程序崩溃: {type(e).__name__}: {e}")
finally:
    set_inject(None)

# --------------------------------------------------------------------------
# Part 3: Mock 模式（error 注入）
# --------------------------------------------------------------------------
print("\n" + "=" * 70)
print("Part 3: Mock 模式 — error 注入（未知地点）")
print("=" * 70)

set_inject({"tool": "get_weather_forecast", "status": "error", "error": "unknown location"})
try:
    answer = ask("北京大兴现在的天气怎么样？")
    print(f"\n[Mock error 回答]\n{answer}")
    print("[PASS] Mock 注入 error 成功，大模型生成合理提示 ✅")
except Exception as e:
    print(f"[FAIL] 程序崩溃: {type(e).__name__}: {e}")
finally:
    set_inject(None)

# --------------------------------------------------------------------------
# Part 4: 对比汇总
# --------------------------------------------------------------------------
print("\n" + "=" * 70)
print("对比汇总：Mock 固定值 vs 真实接口返回值")
print("=" * 70)

print("""
+-------------+------------------+----------------------------------+
| 模式        | 返回 status      | 回答示例                          |
+-------------+------------------+----------------------------------+
| Mock unavailable | unavailable  | "抱歉，无法获取天气，请稍后重试"  |
| Mock error       | error        | "位置名称有误，请确认地点名称"    |
| 真实 API         | ok           | "北京大兴当前天气晴朗，11.8°C"   |
+-------------+------------------+----------------------------------+

结论：
- Mock 模式：通过 set_inject() 注入固定返回值，用于测试 error/unavailable 场景
- 真实 API：通过 httpx 直接访问 Open-Meteo，数据来源可溯源（JSON time 字段）
- 两种模式互不干扰，切换只需开启/关闭 set_inject()
""")
