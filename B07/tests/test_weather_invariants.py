"""B07 S3 天气证据接入层的不变量测试

这些测试**不联网**：用 monkeypatch 把 B05.test_functions.get_weather_forecast
换成假实现，测的是"接入层自己的判据与诚实性"，不是天气接口本身。

锁住的不变量：
  1. 正常路径：available=True、future_ok=True、返回结构固定
  2. forecast_time 不在 analysis_time 之后 → future_ok=False + 明确 warning，
     **并且 data 仍然保留**（不许静默丢弃这份证据）
  3. 天气不可用 → available=False、error 原样带出、data=None，
     绝不生成任何替代文案（不许用静态文案伪装成功）
  4. B05 调用本身炸了 → 显式 error，不伪装
  5. forecast_window 透传给 B05；默认值是 "now"（不改变既有行为）
  6. forecast_window != "now" 时必须说明"实际取到的是当前实况，不是 +Nh 预报"
  7. 恒有时间基准 warning，且 B02 时间轴范围取自真实数据

跑法：
  ./venv/Scripts/python.exe -m pytest B07/tests/test_weather_invariants.py -v
"""

from datetime import datetime, timedelta

import pytest

ANCHOR = datetime.fromisoformat("2026-10-21T16:00:00")


def _fake_weather(status="ok", timestamp="2026-10-24T22:15", error=None, window="3h"):
    """造一个 B05 形状的返回值。"""
    if status == "ok":
        data = {
            "location": "山东寿光",
            "forecast_window": window,
            "timestamp": timestamp,
            "temperature": 14.0,
            "humidity": 85.0,
            "precipitation_probability": 0.0,
            "cloud_cover": 0.0,
            "solar_radiation": 0.0,
            "weather_code": 0,
            "weather_description": "晴朗",
        }
    else:
        data = None
    return {
        "status": status,
        "provider": "Open-Meteo",
        "retrieved_at": "2026-10-24T14:20:42+00:00",
        "data": data,
        "error": error,
    }


@pytest.fixture
def fake_weather(monkeypatch):
    """替换 B05 的天气工具，记录调用参数。不联网。"""
    import B05.test_functions as tf

    calls: list[dict] = []
    state = {"result": _fake_weather()}

    def _fake(location, forecast_window="now", _inject=None):
        calls.append({"location": location, "forecast_window": forecast_window})
        result = state["result"]
        # 模拟 B05 把窗口回显到 data 里
        if result.get("status") == "ok" and result.get("data"):
            result = dict(result)
            result["data"] = dict(result["data"])
            result["data"]["forecast_window"] = forecast_window
        return result

    monkeypatch.setattr(tf, "get_weather_forecast", _fake)
    return {"calls": calls, "state": state}


# --------------------------------------------------------------------------
# 1. 正常路径
# --------------------------------------------------------------------------

def test_normal_path_returns_fixed_structure(fake_weather):
    from B07.weather import build_weather_evidence

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")

    assert set(ev.keys()) == {
        "available", "status", "provider", "retrieved_at", "forecast_time",
        "forecast_window", "location", "data", "error", "future_ok", "warnings",
    }
    assert ev["available"] is True
    assert ev["status"] == "ok"
    assert ev["error"] is None
    assert ev["provider"] == "Open-Meteo"
    assert ev["location"] == "山东寿光"
    assert ev["forecast_window"] == "3h"
    assert ev["forecast_time"] == ev["data"]["timestamp"]
    assert isinstance(ev["data"], dict)


def test_future_ok_true_when_forecast_time_after_analysis_time(fake_weather):
    from B07.weather import build_weather_evidence

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")
    assert ev["future_ok"] is True
    assert not any("未位于 analysis_time 之后" in w for w in ev["warnings"])


# --------------------------------------------------------------------------
# 2. 不在未来：future_ok=False，但证据不丢
# --------------------------------------------------------------------------

def test_not_future_sets_flag_and_keeps_data(fake_weather):
    from B07.weather import W_NOT_FUTURE, build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(timestamp="2026-10-20T08:00")

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")

    assert ev["future_ok"] is False
    assert W_NOT_FUTURE in ev["warnings"]
    # 关键：数据还在，不许静默丢弃
    assert ev["data"] is not None
    assert ev["data"]["temperature"] == 14.0
    assert ev["available"] is True   # "取到了" 和 "能当未来天气用" 是两回事


def test_equal_to_analysis_time_is_not_future(fake_weather):
    """等于不算"之后" —— 边界取严格大于。"""
    from B07.weather import build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(timestamp=ANCHOR.isoformat())
    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")
    assert ev["future_ok"] is False


def test_unparsable_timestamp_is_treated_as_not_future(fake_weather):
    from B07.weather import build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(timestamp="不是时间")

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")

    assert ev["future_ok"] is False
    assert any("未位于 analysis_time 之后" in w for w in ev["warnings"])
    assert ev["data"] is not None


# --------------------------------------------------------------------------
# 3. 不可用：显式化，不伪装
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["unavailable", "error"])
def test_unavailable_weather_is_explicit(fake_weather, status):
    from B07.weather import build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(
        status=status, error="network connection failed")

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")

    assert ev["available"] is False
    assert ev["status"] == status
    assert ev["error"] == "network connection failed"   # 原样带出
    assert ev["data"] is None
    assert ev["future_ok"] is False
    assert any("外部天气不可用" in w for w in ev["warnings"])


def test_failed_weather_does_not_fabricate_values(fake_weather):
    """取不到天气时，不许凭空造出温度/湿度字段。"""
    from B07.weather import build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(status="unavailable",
                                                     error="timeout")
    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")

    raw = str(ev)
    for banned in ("temperature\": ", "humidity\": ", "weather_description\": "):
        assert banned not in raw


def test_b05_call_failure_is_explicit(fake_weather, monkeypatch):
    """连 B05 这层都调不动 → 显式 error，不伪装成"天气正常"。"""
    import B05.test_functions as tf
    from B07.weather import build_weather_evidence

    def _boom(*a, **kw):
        raise RuntimeError("B05 导入炸了")

    monkeypatch.setattr(tf, "get_weather_forecast", _boom)

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")

    assert ev["available"] is False
    assert ev["status"] == "error"
    assert "RuntimeError" in ev["error"]
    assert ev["data"] is None


# --------------------------------------------------------------------------
# 4. 参数透传
# --------------------------------------------------------------------------

def test_forecast_window_is_passed_through(fake_weather):
    from B07.weather import build_weather_evidence

    build_weather_evidence(ANCHOR, "山东寿光", "6h")
    assert fake_weather["calls"][-1]["forecast_window"] == "6h"
    assert fake_weather["calls"][-1]["location"] == "山东寿光"


def test_default_forecast_window_and_location(fake_weather):
    from B07.weather import (DEFAULT_FORECAST_WINDOW, DEFAULT_LOCATION,
                              build_weather_evidence)

    build_weather_evidence(ANCHOR)
    assert fake_weather["calls"][-1]["forecast_window"] == DEFAULT_FORECAST_WINDOW
    assert fake_weather["calls"][-1]["location"] == DEFAULT_LOCATION
    assert (DEFAULT_LOCATION, DEFAULT_FORECAST_WINDOW) == ("河北固安", "6h")


def test_b05_default_is_now_so_existing_callers_unchanged():
    """B05 的默认值必须是 now —— 既有调用方行为不变。"""
    import inspect
    import B05.test_functions as tf

    sig = inspect.signature(tf.get_weather_forecast)
    assert sig.parameters["forecast_window"].default == "now"


# --------------------------------------------------------------------------
# 5. 预测窗口的诚实性
# --------------------------------------------------------------------------

def test_non_now_window_forecast_time_is_passed_through(fake_weather):
    """B03 修好后，非 now 窗口取到的是真正的目标小时预报时刻，原样透传。"""
    from B07.weather import build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(timestamp="2026-10-25T04:00")

    ev = build_weather_evidence(ANCHOR, "河北固安", "6h")

    assert ev["forecast_time"] == "2026-10-25T04:00"
    assert ev["data"]["timestamp"] == "2026-10-25T04:00"
    assert ev["future_ok"] is True
    # 不再有"只影响请求参数"那条警告 —— B03 已经真的按小时取了预报
    assert not any("只影响请求参数" in w for w in ev["warnings"])


def test_now_window_has_no_such_warning(fake_weather):
    from B07.weather import build_weather_evidence

    ev = build_weather_evidence(ANCHOR, "山东寿光", "now")
    assert not any("只影响请求参数" in w for w in ev["warnings"])


def test_unknown_window_warns_about_silent_fallback(fake_weather):
    from B07.weather import build_weather_evidence

    ev = build_weather_evidence(ANCHOR, "山东寿光", "7h")
    assert any("不在 B03 支持的取值" in w for w in ev["warnings"])


# --------------------------------------------------------------------------
# 6. 时间基准说明恒在，且取自真实数据
# --------------------------------------------------------------------------

def test_time_base_warning_always_present(fake_weather):
    from B07.weather import build_weather_evidence

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")
    assert any("非同一时间基准" in w for w in ev["warnings"])


def test_time_base_warning_uses_real_b02_timeline(fake_weather):
    """B02 时间轴范围要真取，不能写死。"""
    from B07.weather import _b02_timeline_range, build_weather_evidence

    start, end = _b02_timeline_range()
    assert start and end, "B02 时间轴范围应能从真实数据读出"
    assert start < end

    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")
    joined = " ".join(ev["warnings"])
    assert start in joined and end in joined


def test_time_base_warning_present_even_when_weather_unavailable(fake_weather):
    from B07.weather import build_weather_evidence

    fake_weather["state"]["result"] = _fake_weather(status="error", error="x")
    ev = build_weather_evidence(ANCHOR, "山东寿光", "3h")
    assert any("非同一时间基准" in w for w in ev["warnings"])
