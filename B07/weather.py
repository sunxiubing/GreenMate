"""B07 S3 天气证据接入层（只接 B03 未来外部天气）

这一层只做一件事：**把 B03 的公开天气数据接到 B07 的证据链上**，
并且把"这份天气到底能不能当本次分析的未来天气"这件事说清楚。

规矩（来自 B03/B02 的数据性质，不是实现细节）：

  1. 天气是 PUBLIC 真实数据，B02 内部是 SIMULATED 模拟数据，
     两者**不在同一个时间基准上**。所以天气只作"外部条件方向参考"，
     不做时刻级跨源对齐，也不改写 B02 的任何数值。
  2. 天气必须真的在 analysis_time 之后，否则不能当"未来天气"用
     （future_ok=False + 明确 warning），但**不许静默丢弃这份证据**。
  3. 拿不到天气就明说拿不到（status/error 原样带出），
     **绝对不许用静态文案伪装成成功的天气**。

复用方式：**走 B05.test_functions.get_weather_forecast() 这个稳定工具接口**，
不绕过 B05 直接调 B03，也不重写天气请求、不新增任何网络调用。

用法：
  from B07.weather import build_weather_evidence
  ev = build_weather_evidence(analysis_time)

默认参数为什么是"河北固安 / 6h"：

  默认地点用河北固安而非山东寿光：实测当前寿光外部条件平稳
  （降水概率 0%、晴），无法体现"外部条件将有明显变化"；
  固安当前降水概率 21%、+6h 升至 70%，外部信号有区分度。
  注意：真实天气的数值随真实时间变化，地点与窗口是教学参数，
  选点依据是"能让外部条件信号有区分度"，不是为了制造某个结论；
  若当天实测外部条件平稳，external_signal 会是 external_stable，
  这同样是合法结果。

硬约束：
  - 不接 B04 知识库、不接摄像头、不接新智能体
  - 不修改 B01 / B02 / B06 的任何文件
  - 不修改 LLM 提示词与固定问题
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Open-Meteo 的 current 时间戳是 Asia/Shanghai 墙钟时间（无时区后缀）。
# 中国自 1991 年起不再使用夏令时，固定 UTC+8 与实际一致，且不依赖 tzdata。
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# B03 支持的 forecast_window（见 B03/weather_service.py 的 _WINDOW_TO_HOURS）
SUPPORTED_FORECAST_WINDOWS = ("now", "1h", "3h", "6h", "12h", "1d", "3d")
_WINDOW_TO_HOURS = {"now": 0, "1h": 1, "3h": 3, "6h": 6,
                    "12h": 12, "1d": 24, "3d": 72}

DEFAULT_LOCATION = "河北固安"
DEFAULT_FORECAST_WINDOW = "6h"

# --------------------------------------------------------------------------
# S5-1：天气不可用开关（显式模拟 API 故障）
# --------------------------------------------------------------------------
#
# 文档 S5 第二节要求"让天气 API 暂时不可用"，然后**先不要修**，
# 观察系统还剩什么。本开关就是那个"弄坏一条证据"的手柄：
#
#   B07_WEATHER_UNAVAILABLE=1  →  天气 provider 直接返回一个"不可用"结果
#   未设置（默认）              →  一切照常
#
# 为什么用开关而不是真断网：
#   断网不可复现（机器上还有别的进程在用网、代理状态、时区…），
#   而且会让"到底是不是天气不可用"和"是不是网络整体坏了"混在一起。
#   显式开关只动天气这一条证据，其它证据、其它判据、其它进程一概不受影响。
#
# 硬约束：
#   - 这是**一个分支**：判定逻辑只有 weather_unavailable_reason() 一处，
#     provider 层只调用它；
#   - 不新建 provider、不新建模块文件、不改判据阈值；
#   - 失败必须显式：status=unavailable、data=None、error 写明原因，
#     **不许留上次成功的值、不许写空 error、不许静默兜底**。
WEATHER_UNAVAILABLE_ENV = "B07_WEATHER_UNAVAILABLE"

#: 开关打开时写进 error 的说明（固定文案，但 error 本身非空且指明开关）
WEATHER_UNAVAILABLE_ERROR = (
    "weather provider unavailable (B07_WEATHER_UNAVAILABLE=1): "
    "模拟天气 API 故障，未发起真实天气请求"
)


def weather_unavailable_reason() -> str | None:
    """开关是否打开；打开就返回显式失败原因，没打开返回 None。

    这是 S5-1 降级开关的**唯一判定出处** —— 别处只调用它，
    不各自判断环境变量（免得两处漂移）。

    Returns:
        None（正常）或一段非空的失败原因（降级）。
    """
    raw = os.environ.get(WEATHER_UNAVAILABLE_ENV, "")
    if raw.strip() == "1":
        return WEATHER_UNAVAILABLE_ERROR
    return None


# ---- 固定文案（判据口径，改这里等于改判据） ----

W_NOT_FUTURE = "天气 forecast_time 未位于 analysis_time 之后，不能作为本次分析的未来天气"
W_UNAVAILABLE = "外部天气不可用（unavailable/error）"
# B03 对未知窗口会静默降级为 now（当前实况），这里显式说出来。
W_WINDOW_UNKNOWN = (
    "forecast_window='%s' 不在 B03 支持的取值 %s 之内，"
    "B03 会静默降级为 'now'（当前实况）"
)

# --------------------------------------------------------------------------
# 外部条件判定阈值（模块级常量，不散落魔法数字）
# --------------------------------------------------------------------------

# 主判定：相对基线的降水概率变化（单位：百分点 pp）
EXTERNAL_PRECIP_DELTA_THRESHOLD = 30.0

# 兜底判定（仅当 baseline 点取不到时启用）：绝对阈值
EXTERNAL_FALLBACK_PRECIP_HIGH = 60.0    # 降水概率 ≥ 此值 → external_deteriorating
EXTERNAL_FALLBACK_PRECIP_LOW = 10.0     # 降水概率 ≤ 此值 且 云量 ≤ 下值 → external_improving
EXTERNAL_FALLBACK_CLOUD_MAX = 20.0

# 两个点（baseline / forecast）的基准时刻允许的最大偏差（小时）
EXTERNAL_BASELINE_MAX_SKEW_HOURS = 1.0

# 判定信号取值
SIGNAL_DETERIORATING = "external_deteriorating"
SIGNAL_IMPROVING = "external_improving"
SIGNAL_STABLE = "external_stable"
SIGNAL_UNAVAILABLE = "external_unavailable"

# 判定方式
METHOD_RELATIVE = "relative_delta"
METHOD_ABSOLUTE = "absolute_fallback"
METHOD_UNAVAILABLE = "unavailable"

# 已知局限（写进 external_conditions.caveats，逐条都是真的）
CAVEAT_SINGLE_POINT = (
    "外部取的是窗口末时刻的单点值，不是窗口极值——"
    "中途更高/更低的值可能被漏掉"
)
CAVEAT_NIGHT_ONLY = (
    "全部实测点位于夜间，solar_radiation 恒为 0，"
    "高温分支（≥35℃）至今未被任何实测点覆盖"
)
CAVEAT_TIME_BASE_MISMATCH = (
    "外部为真实 API 的真实时间（比模拟锚点晚约 3 天），"
    "数值不可与内部模拟值直接比较，只作方向性参考"
)
CAVEAT_TEMP_CLOUD_DESCRIPTIVE = (
    "温度与云量只作描述，不参与主判定："
    "温度方向单一（既可能太热也可能太冷），云量影响光照、与降水不是同一维度"
)

W_TIME_BASE = (
    "外部天气为 PUBLIC 真实数据，forecast_time 属于真实世界时间轴；"
    "B02 内部为 SIMULATED 模拟时间轴（%s ~ %s），两者非同一时间基准，"
    "本次仅将天气用作外部条件方向参考，不做时刻级跨源对齐。"
)


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def _to_comparable(dt: datetime) -> datetime:
    """统一成 naive 的 Asia/Shanghai 墙钟时间，才能和 Open-Meteo 的 timestamp 比。

    B02 里的 analysis_time 是 naive 的（模拟时间轴）；
    外部传入的带时区 datetime 先折算到 UTC+8 再去掉时区后缀。
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(SHANGHAI_TZ).replace(tzinfo=None)


def _parse_forecast_time(text) -> datetime | None:
    """解析 data["timestamp"]；解析不了就返回 None（由调用方显式记 warning）。"""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        return None


def _b02_timeline_range() -> tuple[str | None, str | None]:
    """B02 模拟时间轴的实际范围（从真实数据取，不写死）。

    读 B02 的原始样本，取最早/最晚时间戳。
    读不到就返回 (None, None)，由调用方决定怎么记 warning。
    """
    try:
        from B07.stability import load_all_samples

        samples = load_all_samples()
        if not samples:
            return None, None
        ts = [s.timestamp for s in samples]
        return min(ts).isoformat(), max(ts).isoformat()
    except Exception:
        return None, None


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def build_weather_evidence(analysis_time: datetime,
                            location: str = DEFAULT_LOCATION,
                            forecast_window: str = DEFAULT_FORECAST_WINDOW,
                            ) -> dict:
    """构建本次分析用的外部天气证据。

    Args:
        analysis_time: 本次分析的时间锚点（B02 模拟时间轴，naive）
        location: 预置城市名，见 B03.SPEC.GREENHOUSE_LOCATIONS
        forecast_window: 透传给 B03；见 SUPPORTED_FORECAST_WINDOWS

    Returns:
        {
          "available": bool,          # 天气是否取到（status == "ok"）
          "status": "ok"|"unavailable"|"error",
          "provider": str,            # 保留 B05 原样返回
          "retrieved_at": str,        # 保留 B05 原样返回
          "forecast_time": str|None,  # = data["timestamp"]（now 窗口=当前实况时刻；
                                     #   非 now 窗口=B03 取到的目标小时预报时刻）
          "forecast_window": str,
          "location": str,
          "data": dict|None,          # B05 返回的 data 原文
          "error": str|None,          # 原样带出
          "future_ok": bool,          # forecast_time > analysis_time
          "warnings": list[str],
        }

    注意：
      - available 只表示"取到了"，不表示"能当未来天气用"；
        能不能当未来天气看 future_ok。两个字段分开，不混着骗人。
      - 天气取不到时 data=None、error 原样带出，不生成任何替代文案。
      - 天气取到但时间不在未来时，data 仍然保留（不静默丢弃），
        同时 future_ok=False 并记 warning。
    """
    warnings: list[str] = []

    # ---- 取天气：只走 B05 的稳定工具接口 ----
    # S5-1：降级开关在这里同样生效（走真实 provider 时不发起请求）。
    raw = _safe_weather_call(location, forecast_window)

    status = raw.get("status") or "error"
    provider = raw.get("provider")
    retrieved_at = raw.get("retrieved_at")
    data = raw.get("data")
    error = raw.get("error")

    available = (status == "ok") and isinstance(data, dict) and bool(data)

    # ---- forecast_time ----
    forecast_time = data.get("timestamp") if isinstance(data, dict) else None

    # ---- future_ok：forecast_time 必须落在 analysis_time 之后 ----
    future_ok = False
    parsed = _parse_forecast_time(forecast_time)
    if available and parsed is not None:
        future_ok = _to_comparable(parsed) > _to_comparable(analysis_time)
    elif available and parsed is None:
        warnings.append(
            f"天气 forecast_time 无法解析（{forecast_time!r}），"
            f"按「未位于 analysis_time 之后」处理：{W_NOT_FUTURE}"
        )

    if available and not future_ok and parsed is not None:
        warnings.append(W_NOT_FUTURE)

    # ---- 天气不可用：显式化 ----
    if not available:
        warnings.append(f"{W_UNAVAILABLE}；status={status}，error={error}")

    # ---- 预测窗口的真实性说明（别让 forecast_window 字段造成误读） ----
    hours = _WINDOW_TO_HOURS.get(forecast_window)
    if hours is None:
        warnings.append(
            W_WINDOW_UNKNOWN % (forecast_window, "/".join(SUPPORTED_FORECAST_WINDOWS))
        )

    # ---- 时间基准说明（真实边界，不是可选的装饰） ----
    start, end = _b02_timeline_range()
    if start and end:
        warnings.append(W_TIME_BASE % (start, end))
    else:
        warnings.append(
            "外部天气为 PUBLIC 真实数据，forecast_time 属于真实世界时间轴；"
            "B02 内部为 SIMULATED 模拟时间轴（时间轴范围未能从 B02 读出），"
            "两者非同一时间基准，本次仅将天气用作外部条件方向参考，"
            "不做时刻级跨源对齐。"
        )

    return {
        "available": available,
        "status": status,
        "provider": provider,
        "retrieved_at": retrieved_at,
        "forecast_time": forecast_time,
        "forecast_window": forecast_window,
        "location": location,
        "data": data,
        "error": error,
        "future_ok": future_ok,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# S3-1：双点基线 + 相对判定
# --------------------------------------------------------------------------

def _is_pristine_weather_tool(tool) -> bool:
    """这个 provider 是不是 B05 里那个真实走网络的天气工具。

    S5-1 的降级开关有两种落点：
      - 真实 provider（本函数为真）→ 由本模块拦截，不发起请求；
      - 场景自己装上去的 provider（离线夹具，provider 身份是 Offline-Fixture）
        → 由那个 provider 自己处理开关，这样它报出来的身份和失败原因
        都是它自己的，不会出现"ctx 说是 Open-Meteo、raw 说是 Offline-Fixture"
        这种同一轮里自相矛盾的情况。
    """
    return (getattr(tool, "__module__", "") == "B05.test_functions"
            and getattr(tool, "__name__", "") == "get_weather_forecast")


def _safe_weather_call(location: str, forecast_window: str) -> dict:
    """调 B05 的天气工具；连调用层都炸了就显式返回 error（不造数据）。

    S5-1：先看降级开关。开关打开且走的是真实 provider 时
    **根本不发起天气请求**，直接返回 provider 层的"不可用"结果 ——
    status=unavailable、data=None、error 写明原因，
    不留任何上次成功的值。
    """
    try:
        from B05.test_functions import get_weather_forecast

        reason = weather_unavailable_reason()
        if reason and _is_pristine_weather_tool(get_weather_forecast):
            return {
                "status": "unavailable",
                "provider": "Open-Meteo",
                "retrieved_at": None,
                "data": None,
                "error": reason,
            }

        return get_weather_forecast(location, forecast_window=forecast_window)
    except Exception as e:
        return {
            "status": "error",
            "provider": "Open-Meteo",
            "retrieved_at": "",
            "data": None,
            "error": f"{type(e).__name__}: {e}",
        }


def _weather_point(raw: dict) -> dict | None:
    """从 B05 的返回值里抽出一个天气点；取不到就返回 None（绝不造数据）。"""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        return None
    data = raw.get("data")
    if not isinstance(data, dict) or not data:
        return None
    return {
        "timestamp": data.get("timestamp"),
        "temperature": data.get("temperature"),
        "humidity": data.get("humidity"),
        "precipitation_probability": data.get("precipitation_probability"),
        "cloud_cover": data.get("cloud_cover"),
        "solar_radiation": data.get("solar_radiation"),
        "weather_code": data.get("weather_code"),
        "weather_description": data.get("weather_description"),
    }


def _point_error(raw: dict) -> str:
    """从一个失败的 B05 返回值里取出错误原因。"""
    if not isinstance(raw, dict):
        return "返回值格式异常"
    return raw.get("error") or f"status={raw.get('status')}"


def _round_delta(a, b):
    """两个可能为 None 的数之差；任一为 None 则返回 None。"""
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 1)


# --------------------------------------------------------------------------
# 纯判定（不联网、不读配置）
# --------------------------------------------------------------------------

def baseline_skew_hours(baseline_point: dict | None,
                        forecast_point: dict | None,
                        hours: int) -> tuple[float | None, str | None]:
    """两个点的"基准时刻"偏差（小时），以及超过阈值时的 warning。

    forecast 点是 hourly[idx + hours]，它的基准时刻 = 预报时刻 - hours；
    baseline 点是 current 实况，基准时刻 = 自身时刻。
    两者偏差 > EXTERNAL_BASELINE_MAX_SKEW_HOURS，说明两点不是同一时刻取到的。

    Returns: (skew_hours, warning or None)
    """
    if not hours or hours <= 0:
        return None, None

    base_dt = _parse_forecast_time(
        baseline_point.get("timestamp") if baseline_point else None)
    fore_dt = _parse_forecast_time(
        forecast_point.get("timestamp") if forecast_point else None)
    if base_dt is None or fore_dt is None:
        return None, None

    fore_base = (fore_dt - timedelta(hours=hours)).replace(minute=0, second=0)
    skew = round(
        abs((fore_base - base_dt.replace(minute=0, second=0)).total_seconds()) / 3600.0,
        2,
    )
    if skew <= EXTERNAL_BASELINE_MAX_SKEW_HOURS:
        return skew, None

    warning = (
        f"两个点的基准时刻不一致（相差 {skew} 小时，"
        f"超过 {EXTERNAL_BASELINE_MAX_SKEW_HOURS} 小时）："
        f"基线点 {baseline_point.get('timestamp')}，"
        f"预报点 {forecast_point.get('timestamp')}（基准 "
        f"{fore_base.isoformat()}）——本次仍继续判定，但结果需谨慎看待"
    )
    return skew, warning


def judge_external_points(baseline_point: dict | None,
                          forecast_point: dict | None,
                          hours: int = 0,
                          analysis_time: datetime | None = None) -> dict:
    """**纯判定**：给定两个天气点，算出外部条件信号。

    不联网、不读配置、不看时钟——只吃两个点，吐一个判定。
    这样"在线取数后判定"与"从存档文件重读后复算"用的是**同一份判据**，
    不会出现存档里的结论和现在的规则对不上。

    判定规则（主判定，只看降水概率变化，单位 pp = 百分点）：
        ΔPP = forecast.precipitation_probability - baseline.precipitation_probability
        ΔPP ≥ +30 → external_deteriorating
        ΔPP ≤ -30 → external_improving
        其余       → external_stable

    兜底（仅当 baseline 取不到时）：
        forecast 降水概率 ≥ 60               → external_deteriorating
        forecast 降水概率 ≤ 10 且 云量 ≤ 20   → external_improving
        其余                                   → external_stable
        走兜底时 judge_method = "absolute_fallback"，basis 里写明原因。

    analysis_time 不为 None 时，多一道硬门槛：
        预报时刻必须晚于 analysis_time，否则**不产生任何判定**，
        也不返回 external_stable（"稳定"本身就是一个结论，
        拿一份不在未来的预报去说"稳定"是误导）。

    Returns:
        {signal, judge_method, basis, precip_delta, temp_delta, cloud_delta,
         solar_radiation, window_effective, warnings}
    """
    warnings: list[str] = []
    signal = SIGNAL_UNAVAILABLE
    judge_method = METHOD_UNAVAILABLE
    basis = ""
    precip_delta = temp_delta = cloud_delta = None
    solar_radiation = None
    window_effective = True

    # ---- 硬门槛：预报时刻必须真的在 analysis_time 之后 ----
    # 不在未来 → 不产生任何判定，也不返回 external_stable
    # （"稳定"本身就是一个结论，拿一份不在未来的预报去说"稳定"是误导）
    not_in_future = False
    if analysis_time is not None and forecast_point is not None:
        fore_dt_gate = _parse_forecast_time(forecast_point.get("timestamp"))
        if fore_dt_gate is None:
            not_in_future = True
        else:
            not_in_future = not (
                _to_comparable(fore_dt_gate) > _to_comparable(analysis_time))

    if forecast_point is None:
        basis = "预报点不可用，未做任何判定"
    elif not_in_future:
        basis = ("预报时刻未位于 analysis_time 之后，不能当作本次分析的未来天气，"
                 "未做任何判定（也不返回『稳定』）")
        warnings.append(W_NOT_FUTURE)
    else:
        base_dt = _parse_forecast_time(
            baseline_point.get("timestamp") if baseline_point else None)
        fore_dt = _parse_forecast_time(forecast_point.get("timestamp"))

        # 预报点必须真的比基线点晚（否则窗口没生效，不能拿 0 差分说"稳定"）
        if hours > 0 and fore_dt is not None and base_dt is not None:
            if fore_dt <= base_dt:
                window_effective = False
                warnings.append(
                    f"预报时刻（{forecast_point.get('timestamp')}）未晚于基线时刻"
                    f"（{baseline_point.get('timestamp')}），"
                    f"预测窗口未生效——不做相对判定，也不返回虚假的『稳定』结论"
                )

        if not window_effective:
            basis = "预测窗口未生效（预报时刻未晚于基线时刻），未做任何判定"
        elif baseline_point is not None:
            # ---- 主判定：相对基线差分 ----
            precip_delta = _round_delta(
                forecast_point.get("precipitation_probability"),
                baseline_point.get("precipitation_probability"),
            )
            temp_delta = _round_delta(
                forecast_point.get("temperature"), baseline_point.get("temperature"))
            cloud_delta = _round_delta(
                forecast_point.get("cloud_cover"), baseline_point.get("cloud_cover"))
            solar_radiation = forecast_point.get("solar_radiation")

            if precip_delta is None:
                basis = "降水概率缺失，未做任何判定"
            else:
                if precip_delta >= EXTERNAL_PRECIP_DELTA_THRESHOLD:
                    signal = SIGNAL_DETERIORATING
                elif precip_delta <= -EXTERNAL_PRECIP_DELTA_THRESHOLD:
                    signal = SIGNAL_IMPROVING
                else:
                    signal = SIGNAL_STABLE
                judge_method = METHOD_RELATIVE
                basis = (
                    f"相对基线差分（主判定）：降水概率 "
                    f"{baseline_point.get('precipitation_probability')}% → "
                    f"{forecast_point.get('precipitation_probability')}%"
                    f"（Δ={precip_delta} 个百分点，阈值 ±{EXTERNAL_PRECIP_DELTA_THRESHOLD} 个百分点）"
                    f" → {signal}；温度 Δ{temp_delta}℃、云量 Δ{cloud_delta}%"
                    f"（只作描述，不参与判定）"
                )
        else:
            # ---- 兜底：绝对阈值（仅当 baseline 取不到）----
            judge_method = METHOD_ABSOLUTE
            pp = forecast_point.get("precipitation_probability")
            cloud = forecast_point.get("cloud_cover")
            solar_radiation = forecast_point.get("solar_radiation")
            if pp is None:
                basis = "预报点降水概率缺失，未做任何判定"
            else:
                if pp >= EXTERNAL_FALLBACK_PRECIP_HIGH:
                    signal = SIGNAL_DETERIORATING
                elif (pp <= EXTERNAL_FALLBACK_PRECIP_LOW
                      and cloud is not None and cloud <= EXTERNAL_FALLBACK_CLOUD_MAX):
                    signal = SIGNAL_IMPROVING
                else:
                    signal = SIGNAL_STABLE
                basis = (
                    f"绝对阈值兜底（基线点 now 取不到，无法做相对差分）："
                    f"预报降水概率 {pp}%"
                    f"（恶化阈值 ≥{EXTERNAL_FALLBACK_PRECIP_HIGH}%、"
                    f"好转阈值 ≤{EXTERNAL_FALLBACK_PRECIP_LOW}% 且云量 ≤{EXTERNAL_FALLBACK_CLOUD_MAX}%）、"
                    f"云量 {cloud}% → {signal}。"
                    f"注意：兜底判定没有基线可比，可能把『本来就晴』误判为好转"
                )

    return {
        "signal": signal,
        "judge_method": judge_method,
        "basis": basis,
        "precip_delta": precip_delta,
        "temp_delta": temp_delta,
        "cloud_delta": cloud_delta,
        "solar_radiation": solar_radiation,
        "window_effective": window_effective,
        "warnings": warnings,
    }


def build_external_context(analysis_time: datetime,
                           location: str = DEFAULT_LOCATION,
                           forecast_window: str = DEFAULT_FORECAST_WINDOW,
                           ) -> dict:
    """构建"外部条件"上下文：基线点（now）+ 预报点（+Nh），并做相对判定。

    为什么用**相对基线差分**而不是绝对阈值（这是本函数的核心设计理由）：

      1. **消除系统性基线错位**。内部是 B02 模拟的下午（32.8℃），
         外部是真实世界的夜晚（13℃），两者根本不在同一个量级和时刻上。
         绝对阈值（"气温 ≥ 35℃ 算高温"）作用在这种错位的数据上毫无意义；
         相对判定只问"外部条件相对刚才是在变好还是变坏"，基线怎么平移都不影响。
      2. **绝对阈值会把"晴天"误判成 improving**。晴天并不会"变得更好"，
         而且夜间云量低反而加快辐射散热——这是误导。
         相对判定里"晴→晴"就是 0 变化，只能判 stable。

    两个点：
      - baseline：forecast_window="now"（当前实况）
      - forecast：forecast_window=<forecast_window>（B03 修好后取的是 +Nh 目标小时）

    判定规则（主判定，只看降水概率变化，单位 pp = 百分点）：
        ΔPP = forecast.precipitation_probability - baseline.precipitation_probability
        ΔPP ≥ +30 → external_deteriorating
        ΔPP ≤ -30 → external_improving
        其余       → external_stable

    兜底（仅当 baseline 取不到时）：
        forecast 降水概率 ≥ 60                          → external_deteriorating
        forecast 降水概率 ≤ 10 且 云量 ≤ 20              → external_improving
        其余                                              → external_stable
        走兜底时 judge_method = "absolute_fallback"，basis 里写明原因。

    温度与云量只作描述，不参与主判定（理由见 CAVEAT_TEMP_CLOUD_DESCRIPTIVE）。

    失败一律显式化：取不到就 available=False + signal=external_unavailable +
    warning，**不返回假的天气数据**。
    """
    warnings: list[str] = []
    caveats: list[str] = [
        CAVEAT_SINGLE_POINT,
        CAVEAT_NIGHT_ONLY,
        CAVEAT_TIME_BASE_MISMATCH,
        CAVEAT_TEMP_CLOUD_DESCRIPTIVE,
    ]

    # ---- 窗口合法性：不在支持列表里就显式失败，不让 B03 静默降级 ----
    hours = _WINDOW_TO_HOURS.get(forecast_window)
    if hours is None:
        warnings.append(
            W_WINDOW_UNKNOWN % (forecast_window, "/".join(SUPPORTED_FORECAST_WINDOWS))
        )
        warnings.append(
            f"forecast_window={forecast_window!r} 非法，本次不产生外部条件判定"
        )
        return {
            "available": False,
            "signal": SIGNAL_UNAVAILABLE,
            "judge_method": METHOD_UNAVAILABLE,
            "basis": (f"forecast_window={forecast_window!r} 不在支持取值内，"
                      f"未做任何判定"),
            "baseline_point": None,
            "forecast_point": None,
            "precip_delta": None,
            "temp_delta": None,
            "cloud_delta": None,
            "solar_radiation": None,
            "future_ok": False,
            "warnings": warnings,
            "caveats": caveats,
            "analysis_time": analysis_time.isoformat(),
            "location": location,
            "forecast_window": forecast_window,
            "error": f"非法 forecast_window: {forecast_window!r}",
        }

    # ---- 取两个点（都走 B05 已有接口，不绕过 B05、不直连 B03）----
    raw_base = _safe_weather_call(location, "now")
    raw_fore = _safe_weather_call(location, forecast_window)

    baseline_point = _weather_point(raw_base)
    forecast_point = _weather_point(raw_fore)

    if forecast_point is None:
        warnings.append(
            f"{W_UNAVAILABLE}；forecast 点取数失败：{_point_error(raw_fore)}"
        )
    if baseline_point is None:
        warnings.append(
            f"基线点（now）取数失败：{_point_error(raw_base)}"
        )

    # ---- future_ok：预报时刻必须晚于 analysis_time ----
    future_ok = False
    fore_dt = _parse_forecast_time(
        forecast_point.get("timestamp") if forecast_point else None)
    if fore_dt is not None:
        future_ok = _to_comparable(fore_dt) > _to_comparable(analysis_time)
        if not future_ok:
            warnings.append(W_NOT_FUTURE)
    elif forecast_point is not None:
        warnings.append(
            f"天气 forecast_time 无法解析（{forecast_point.get('timestamp')!r}），"
            f"按「未位于 analysis_time 之后」处理：{W_NOT_FUTURE}"
        )

    # ---- 两个点的基准时刻必须同源同刻 ----
    skew_hours, skew_warning = baseline_skew_hours(
        baseline_point, forecast_point, hours)
    if skew_warning:
        warnings.append(skew_warning)

    # ---- 判定（纯函数，与存档复算共用同一份判据）----
    judged = judge_external_points(baseline_point, forecast_point, hours=hours,
                                    analysis_time=analysis_time)
    signal = judged["signal"]
    judge_method = judged["judge_method"]
    basis = judged["basis"]
    precip_delta = judged["precip_delta"]
    temp_delta = judged["temp_delta"]
    cloud_delta = judged["cloud_delta"]
    solar_radiation = judged["solar_radiation"]
    warnings.extend(judged["warnings"])

    if hours == 0:
        warnings.append(
            "forecast_window='now' 没有未来量，预报点与基线点同刻，"
            "相对差分恒为 0"
        )

    # ---- 时间基准说明（真实边界，不是可选的装饰）----
    start, end = _b02_timeline_range()
    if start and end:
        warnings.append(W_TIME_BASE % (start, end))
    else:
        warnings.append(
            "外部天气为 PUBLIC 真实数据，forecast_time 属于真实世界时间轴；"
            "B02 内部为 SIMULATED 模拟时间轴（时间轴范围未能从 B02 读出），"
            "两者非同一时间基准，本次仅将天气用作外部条件方向参考，"
            "不做时刻级跨源对齐。"
        )

    return {
        "available": signal != SIGNAL_UNAVAILABLE,
        "signal": signal,
        "judge_method": judge_method,
        "basis": basis,
        "baseline_point": baseline_point,
        "forecast_point": forecast_point,
        "precip_delta": precip_delta,
        "temp_delta": temp_delta,
        "cloud_delta": cloud_delta,
        "solar_radiation": solar_radiation,
        "future_ok": future_ok,
        "warnings": warnings,
        "caveats": caveats,
        "analysis_time": analysis_time.isoformat(),
        "location": location,
        "forecast_window": forecast_window,
        "baseline_skew_hours": skew_hours,
        "error": None if forecast_point is not None else _point_error(raw_fore),
    }


def main():
    """命令行自检：跑一次并把完整结果打出来。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="B07 S3 天气证据接入层（真实调用 B05 → B03 Open-Meteo）")
    parser.add_argument("--analysis-time", type=str,
                        default="2026-10-21T16:00:00",
                        help="本次分析的时间锚点（默认取 S1 锚点）")
    parser.add_argument("--location", type=str, default=DEFAULT_LOCATION)
    parser.add_argument("--forecast-window", type=str, default=DEFAULT_FORECAST_WINDOW)
    parser.add_argument("--external", action="store_true",
                        help="跑 S3-1 的双点基线 + 相对判定（build_external_context）")
    args = parser.parse_args()

    analysis_time = datetime.fromisoformat(args.analysis_time)

    print("=" * 70)
    print("B07 S3 天气证据接入层 — 真实运行")
    print("=" * 70)
    print(f"\n  analysis_time  : {analysis_time.isoformat()}")
    print(f"  location       : {args.location}")
    print(f"  forecast_window: {args.forecast_window}")

    ev = build_weather_evidence(analysis_time, args.location, args.forecast_window)

    if args.external:
        ctx = build_external_context(analysis_time, args.location, args.forecast_window)
        print("\n◆ S3-1 外部条件上下文（双点基线 + 相对判定）")
        print("-" * 70)
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
        print(f"\n  signal        = {ctx['signal']}")
        print(f"  judge_method  = {ctx['judge_method']}")
        print(f"  precip_delta  = {ctx['precip_delta']} pp")
        print(f"  temp_delta    = {ctx['temp_delta']} ℃")
        print(f"  cloud_delta   = {ctx['cloud_delta']} %")
        return

    print("\n◆ 完整返回")
    print("-" * 70)
    print(json.dumps(ev, ensure_ascii=False, indent=2))

    print("\n◆ 关键判定")
    print("-" * 70)
    print(f"  available  = {ev['available']}")
    print(f"  future_ok  = {ev['future_ok']}")
    print(f"  forecast_time = {ev['forecast_time']}   analysis_time = {analysis_time.isoformat()}")

    print("\n◆ warnings")
    print("-" * 70)
    for w in ev["warnings"]:
        print(f"  ⚠️  {w}")
    if not ev["warnings"]:
        print("  无")

    print("\n" + "=" * 70)
    print("B07 天气证据接入层 完成 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
