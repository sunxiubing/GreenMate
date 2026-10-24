"""B05 Step 1 — 底层业务函数测试（不经过大模型）

四个函数：
1. get_current_environment()   → source + data + timestamp
2. get_environment_history()   → source + data + timestamp
3. get_weather_forecast()      → provider + retrieved_at + data / error
4. search_tomato_knowledge()   → Top-K + citation

状态字段：ok / unavailable / error
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

from datetime import datetime, timezone, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# 1. get_current_environment
# --------------------------------------------------------------------------
def get_current_environment():
    """获取最新一条环境样本。"""
    from B02.api.history import get_current_environment as _fn
    result = _fn()
    if result is None:
        return {
            "status": "unavailable",
            "source": "B02/environment_samples",
            "data": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "status": "ok",
        "source": "B02/environment_samples",
        "data": {
            "temperature": result.temperature,
            "humidity": result.humidity,
            "lux": result.lux,
            "source_type": result.source_type,
            "sim_scenario": result.sim_scenario,
        },
        "timestamp": result.timestamp.isoformat(),
    }


# --------------------------------------------------------------------------
# 2. get_environment_history
# --------------------------------------------------------------------------
def get_environment_history(hours: int = 24):
    """获取最近 N 小时的环境样本。"""
    from B02.api.history import get_environment_history as _fn
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    samples = _fn(start, end)
    return {
        "status": "ok",
        "source": "B02/environment_samples",
        "data": [
            {
                "timestamp": s.timestamp.isoformat(),
                "temperature": s.temperature,
                "humidity": s.humidity,
                "lux": s.lux,
                "source_type": s.source_type,
                "sim_scenario": s.sim_scenario,
            }
            for s in samples
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query_hours": hours,
        "count": len(samples),
    }


# --------------------------------------------------------------------------
# 3. get_weather_forecast（支持 mock 注入，用于测试 unavailable）
# --------------------------------------------------------------------------
def get_weather_forecast(
    location: str | tuple[float, float] = "北京大兴",
    forecast_window: str = "now",
    _inject: dict | None = None,
):
    """获取天气预报，模拟断网场景（超时注入）。

    Args:
        location: 预置城市名（如 "北京大兴"）或 (lat, lon) 元组
        forecast_window: 预测窗口，透传给 B03。
            取值见 B03.weather_service._WINDOW_TO_HOURS：
            now / 1h / 3h / 6h / 12h / 1d / 3d。
            默认 "now" —— 保持既有行为完全不变。
        _inject: 测试用注入，{"status": "unavailable"|"error", "error": "..."}
    """
    from B03.weather_service import get_weather_evidence
    import httpx

    try:
        # _inject 用于测试：{"status": "unavailable", "error": "..."} 或 {"status": "error", "error": "..."}
        if _inject:
            return {
                "status": _inject.get("status", "error"),
                "provider": "Open-Meteo",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "data": None,
                "error": _inject.get("error", "injected error"),
            }
        we = get_weather_evidence(location, forecast_window=forecast_window)
        return {
            "status": "ok",
            "provider": we.provider,
            "retrieved_at": we.retrieved_at,
            "data": {
                "location": we.location,
                "forecast_window": we.forecast_window,
                "timestamp": we.timestamp,
                "temperature": we.temperature,
                "humidity": we.humidity,
                "precipitation_probability": we.precipitation_probability,
                "cloud_cover": we.cloud_cover,
                "solar_radiation": we.solar_radiation,
                "weather_code": we.weather_code,
                "weather_description": we.weather_description,
            },
            "error": None,
        }
    except httpx.TimeoutException:
        return {
            "status": "unavailable",
            "provider": "Open-Meteo",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "data": None,
            "error": "weather API timeout after 10s",
        }
    except (httpx.ConnectError, httpx.RemoteProtocolError, OSError):
        # 断网时 httpx 抛出 ConnectError（底层 ConnectionError），RemoteProtocolError 为协议异常
        return {
            "status": "unavailable",
            "provider": "Open-Meteo",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "data": None,
            "error": "network connection failed",
        }
    except httpx.HTTPStatusError as e:
        return {
            "status": "unavailable",
            "provider": "Open-Meteo",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "data": None,
            "error": f"HTTP {e.response.status_code}",
        }
    except Exception as e:
        return {
            "status": "error",
            "provider": "Open-Meteo",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "data": None,
            "error": f"{type(e).__name__}: {e}",
        }


# --------------------------------------------------------------------------
# 4. search_tomato_knowledge
# --------------------------------------------------------------------------
def search_tomato_knowledge(query: str, top_k: int = 5):
    """番茄知识库检索。"""
    # 直接内联 TF-IDF 检索逻辑，避免触发 B04/retrieve.py 的 __main__ 调试输出
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    from B04.SPEC import CHUNKS

    corpus = [c["content"] for c in CHUNKS]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3))
    tfidf_matrix = vectorizer.fit_transform(corpus)
    q_vec = vectorizer.transform([query])
    scores = cosine_similarity(q_vec, tfidf_matrix).flatten()
    top_indices = np.argsort(scores)[::-1][:top_k]
    results = []
    for idx in top_indices:
        c = CHUNKS[idx]
        results.append({
            "chunk_id": c["chunk_id"],
            "source_id": c["source_id"],
            "section": c["section"],
            "page": c["page"],
            "score": float(scores[idx]),
            "content": c["content"],
        })

    return {
        "status": "ok",
        "query": query,
        "Top-K": top_k,
        "citation": results,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# 测试执行
# --------------------------------------------------------------------------
def run_tests():
    print("=" * 70)
    print("B05 Step 1 — 底层业务函数测试")
    print("=" * 70)

    # --- Test 1: get_current_environment ---
    print("\n【Test 1】get_current_environment()")
    print("-" * 50)
    try:
        r = get_current_environment()
        print(f"  status: {r['status']}")
        print(f"  source: {r['source']}")
        print(f"  timestamp: {r['timestamp']}")
        if r["status"] == "ok":
            print(f"  data: temperature={r['data']['temperature']}°C  "
                  f"humidity={r['data']['humidity']}%  lux={r['data']['lux']}  "
                  f"[{r['data']['sim_scenario']}]")
        else:
            print(f"  data: {r['data']}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 2a: get_environment_history hours=24 ---
    print("\n【Test 2a】get_environment_history(hours=24)")
    print("-" * 50)
    try:
        r = get_environment_history(hours=24)
        print(f"  status: {r['status']}")
        print(f"  source: {r['source']}")
        print(f"  query_hours: {r['query_hours']}")
        print(f"  count: {r['count']}")
        print(f"  timestamp: {r['timestamp']}")
        if r["count"] > 0:
            print(f"  first: {r['data'][0]['timestamp']}  "
                  f"{r['data'][0]['temperature']}°C  [{r['data'][0]['sim_scenario']}]")
            print(f"  last:  {r['data'][-1]['timestamp']}  "
                  f"{r['data'][-1]['temperature']}°C  [{r['data'][-1]['sim_scenario']}]")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 2b: get_environment_history hours=12 ---
    print("\n【Test 2b】get_environment_history(hours=12)")
    print("-" * 50)
    try:
        r = get_environment_history(hours=12)
        print(f"  status: {r['status']}")
        print(f"  query_hours: {r['query_hours']}")
        print(f"  count: {r['count']}")
        if r["count"] > 0:
            print(f"  first: {r['data'][0]['timestamp']}  "
                  f"{r['data'][0]['temperature']}°C")
            print(f"  last:  {r['data'][-1]['timestamp']}  "
                  f"{r['data'][-1]['temperature']}°C")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 3a: get_weather_forecast 正常网络 ---
    print("\n【Test 3a】get_weather_forecast('北京大兴') — 正常网络")
    print("-" * 50)
    try:
        r = get_weather_forecast("北京大兴")
        print(f"  status: {r['status']}")
        print(f"  provider: {r['provider']}")
        print(f"  retrieved_at: {r['retrieved_at']}")
        if r["status"] == "ok":
            print(f"  data.location: {r['data']['location']}")
            print(f"  data.temperature: {r['data']['temperature']}°C")
            print(f"  data.humidity: {r['data']['humidity']}%")
            print(f"  data.weather_description: {r['data']['weather_description']}")
            print(f"  data.precipitation_probability: {r['data']['precipitation_probability']}%")
            print(f"  data.cloud_cover: {r['data']['cloud_cover']}%")
        else:
            print(f"  data: {r['data']}")
            print(f"  error: {r['error']}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 3b: get_weather_forecast 模拟断网（无效地点触发异常）---
    print("\n【Test 3b】get_weather_forecast('不存在的地点') — 触发 error")
    print("-" * 50)
    try:
        r = get_weather_forecast("不存在的地点")
        print(f"  status: {r['status']}")
        print(f"  provider: {r['provider']}")
        print(f"  retrieved_at: {r['retrieved_at']}")
        print(f"  data: {r['data']}")
        print(f"  error: {r['error']}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 3c: get_weather_forecast 模拟网络断开（unavailable）---
    print("\n【Test 3c】get_weather_forecast(mock_timeout=True) — 触发 unavailable")
    print("-" * 50)
    try:
        r = get_weather_forecast("北京大兴", _mock_timeout=True)
        print(f"  status: {r['status']}")
        print(f"  provider: {r['provider']}")
        print(f"  retrieved_at: {r['retrieved_at']}")
        print(f"  data: {r['data']}")
        print(f"  error: {r['error']}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 4a: search_tomato_knowledge 正常关键词 ---
    print("\n【Test 4a】search_tomato_knowledge('SIMULATED PUBLIC 区别') — 正常")
    print("-" * 50)
    try:
        r = search_tomato_knowledge("SIMULATED PUBLIC 区别")
        print(f"  status: {r['status']}")
        print(f"  query: {r['query']}")
        print(f"  Top-K: {r['Top-K']}")
        print(f"  citation_count: {len(r['citation'])}")
        for c in r["citation"]:
            print(f"  [{c['chunk_id']}] {c['source_id']} | {c['section']}  score={c['score']:.4f}")
            print(f"    {c['content'][:80]}...")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    # --- Test 4b: search_tomato_knowledge 不存在关键词 ---
    print("\n【Test 4b】search_tomato_knowledge('告警音频文件名称') — 不存在关键词")
    print("-" * 50)
    try:
        r = search_tomato_knowledge("告警音频文件名称")
        print(f"  status: {r['status']}")
        print(f"  query: {r['query']}")
        print(f"  citation_count: {len(r['citation'])}")
        for c in r["citation"]:
            print(f"  [{c['chunk_id']}] {c['source_id']} | {c['section']}  score={c['score']:.4f}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print("全部测试执行完毕")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()
