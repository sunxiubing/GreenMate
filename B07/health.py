"""B08 第一遍 · 健康检查（浅 / 深两层）

★ 为什么分两层（文档 p.92 的硬要求）：

  **"如果天气、知识等外部服务异常，也要能够明确显示，而不是整个页面无响应。"**

  所以浅检查与深检查必须**物理隔离**：

  · `/health`（浅）：只证明**进程活着**。
    永远返回 200，**绝不触碰任何外部服务**（不连数据库、不调天气、不检索知识、
    不加载模型）。这样 B03 天气挂了、Open-Meteo 超时了，页面照样秒回。

  · `/health/deep`（深）：逐项检查 B02 库 / B03 天气 / B04 知识 / B06 模型，
    每项返回 ok | unavailable | error + 原因（失败必须显式暴露，
    不用静态文案伪装成功）。

  ★ 深检查里的 B03 天气**默认不真的发请求**（每次都耗网络、还可能卡 10 秒），
    默认只做"离线可装载性"检查；显式加 `?probe=true` 才真调一次。

本模块是**纯函数式的检查集合**，不含任何副作用（不写文件、不改配置），
被 `B07/views_web.py` 与 `B01/main.py` 同时复用 —— 两个入口的答案必须一致。

用法：
    python -m B07.health              # 打印浅检查
    python -m B07.health --deep       # 打印深检查
    python -m B07.health --deep --probe   # 深检查 + 真的调一次天气 API
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

#: B08 基线版本号（冻结的第一件事）
APP_VERSION = "B08_baseline_01"

#: 基线快照文件（版本号 / 模型版本的权威来源）
BASELINE_PATH = _PROJECT_ROOT / "B07" / "artifacts" / "b08_baseline.json"

#: B06 模型产物目录（模型版本的默认来源）
B06_ARTIFACTS = _PROJECT_ROOT / "B06" / "artifacts"

#: 深检查里 B03 天气用的探测位置（与 B01 页面默认一致）
WEATHER_PROBE_LOCATION = "北京大兴"

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _baseline() -> dict | None:
    """读基线快照；读不到就返回 None（**不假装有基线**）。"""
    if not BASELINE_PATH.exists():
        return None
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001 —— 基线坏了也要让服务活着
        return None


def baseline_app_version() -> str:
    b = _baseline()
    if b and b.get("app_version"):
        return str(b["app_version"])
    return APP_VERSION


def baseline_model_version() -> str:
    b = _baseline()
    if b:
        mv = (b.get("model") or {}).get("version")
        if mv:
            return str(mv)
    # 没有基线文件时，退回 B06 产物目录里最新的一个 manifest
    manifests = sorted(B06_ARTIFACTS.glob("b06-*.json"))
    if manifests:
        try:
            meta = json.loads(manifests[-1].read_text(encoding="utf-8"))
            return str(meta.get("model_version") or manifests[-1].stem)
        except Exception:                   # noqa: BLE001
            pass
    return "unknown"


def _ok(detail: str, **extra) -> dict:
    return {"status": STATUS_OK, "detail": detail, **extra}


def _unavailable(detail: str, **extra) -> dict:
    return {"status": STATUS_UNAVAILABLE, "detail": detail, **extra}


def _error(detail: str, **extra) -> dict:
    return {"status": STATUS_ERROR, "detail": detail, **extra}


# --------------------------------------------------------------------------
# 浅检查
# --------------------------------------------------------------------------

def shallow_health() -> dict:
    """浅检查：**只证明进程活着**。

    不碰任何外部服务 —— 不连数据库、不调天气、不检索知识、不加载模型。
    无论外部服务死活，这里都返回 status=ok（HTTP 200）。
    """
    return {
        "status": STATUS_OK,
        "scope": "shallow",
        "app_version": baseline_app_version(),
        "model_version": baseline_model_version(),
        "time": _now_iso(),
        "external_services_touched": False,
        "detail": "浅检查：只证明进程存活，未访问任何外部服务"
                  "（数据库 / 天气 / 知识 / 模型均未触碰）。"
                  "外部服务状态请查 /health/deep。",
    }


# --------------------------------------------------------------------------
# 深检查：四项依赖
# --------------------------------------------------------------------------

def _check_b02_database() -> dict:
    """B02 环境样本库：能不能打开、表在不在、有多少样本。"""
    db_path = _PROJECT_ROOT / "B02" / "greenmate.db"
    if not db_path.exists():
        return _unavailable(f"B02 数据库文件不存在：{db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True,
                               timeout=3.0)
        try:
            row = conn.execute(
                "SELECT COUNT(*), MAX(timestamp) FROM environment_samples"
            ).fetchone()
        finally:
            conn.close()
        count, latest = row[0], row[1]
        return _ok("B02 库可读",
                   path=str(db_path.relative_to(_PROJECT_ROOT)),
                   sample_count=count,
                   latest_sample=str(latest))
    except sqlite3.Error as exc:
        return _error(f"B02 库读取失败：{type(exc).__name__}: {exc}",
                      path=str(db_path.relative_to(_PROJECT_ROOT)))
    except Exception as exc:               # noqa: BLE001
        return _error(f"B02 库检查异常：{type(exc).__name__}: {exc}",
                      path=str(db_path.relative_to(_PROJECT_ROOT)))


def _check_b03_weather(probe: bool) -> dict:
    """B03 天气。

    probe=False（默认）：**不发网络请求**，只做离线可装载性检查
      —— 模块能不能导入、provider 是不是还在、端点常量还在不在。
      注意：这**不能**证明天气 API 现在能用，只证明接线没坏。
    probe=True：真的调一次天气 API（Open-Meteo），返回实测结果或真实失败原因。
    """
    try:
        from B03 import weather as b03_weather
    except Exception as exc:               # noqa: BLE001
        return _error(f"B03 天气模块导入失败：{type(exc).__name__}: {exc}",
                      network_requested=False)

    provider = getattr(b03_weather, "OPEN_METEO_BASE", None)
    base = {
        "provider": "Open-Meteo",
        "endpoint": provider,
        "network_requested": bool(probe),
    }

    if not probe:
        return _ok("B03 天气模块可导入，接线正常；**本次未发起真实请求**"
                   "（默认不发网络，避免每次检查都耗网络/卡超时）。"
                   "要实测请加 ?probe=true。",
                   **base)

    try:
        from B03.weather_service import get_weather_evidence
        we = get_weather_evidence(WEATHER_PROBE_LOCATION, "now")
    except Exception as exc:               # noqa: BLE001 —— 失败原因必须原样带出
        return _error(f"天气 API 真实调用失败：{type(exc).__name__}: {exc}", **base)

    try:
        temp = getattr(we, "temperature", None)
        ts = getattr(we, "timestamp", None)
        return _ok(f"天气 API 真实调用成功：{WEATHER_PROBE_LOCATION} "
                   f"temperature={temp} timestamp={ts}",
                   observed_temperature=temp,
                   observed_timestamp=str(ts),
                   **base)
    except Exception as exc:               # noqa: BLE001
        return _error(f"天气响应解析异常：{type(exc).__name__}: {exc}", **base)


def _check_b04_knowledge() -> dict:
    """B04 知识库：语料在不在、检索跑不跑得通。纯本地，不联网。"""
    try:
        from B04.SPEC import CHUNKS
    except Exception as exc:               # noqa: BLE001
        return _error(f"B04 知识语料导入失败：{type(exc).__name__}: {exc}")

    if not CHUNKS:
        return _unavailable("B04 知识语料为空（CHUNKS = []），检索无内容可召回")

    try:
        from B05.test_functions import search_tomato_knowledge
        raw = search_tomato_knowledge("证据缺失时的正确做法", top_k=1)
    except Exception as exc:               # noqa: BLE001
        return _error(f"B04 检索执行失败：{type(exc).__name__}: {exc}",
                      chunk_count=len(CHUNKS))

    if not isinstance(raw, dict) or raw.get("status") != "ok":
        return _unavailable(f"B04 检索未成功：status="
                            f"{(raw or {}).get('status') if isinstance(raw, dict) else 'unknown'}",
                            chunk_count=len(CHUNKS))

    cites = raw.get("citation") or []
    top = cites[0] if cites else {}
    return _ok(f"B04 知识库可检索：{len(CHUNKS)} 个 chunk，"
               f"top1={top.get('chunk_id')} score={top.get('score')}",
               chunk_count=len(CHUNKS),
               top_chunk_id=top.get("chunk_id"),
               top_score=top.get("score"))


def _check_b06_model() -> dict:
    """B06 模型：产物文件在不在、能不能加载。纯本地，不重训。"""
    import hashlib

    meta_path = B06_ARTIFACTS / "b06-20261020-v1.json"
    weights_path = B06_ARTIFACTS / "b06-20261020-v1.joblib"
    if not meta_path.exists():
        return _unavailable(f"B06 模型 manifest 不存在：{meta_path}")
    if not weights_path.exists():
        return _unavailable(f"B06 模型权重文件不存在：{weights_path}")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:               # noqa: BLE001
        return _error(f"B06 manifest 解析失败：{type(exc).__name__}: {exc}")

    def _sha(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    try:
        import joblib
        model = joblib.load(weights_path)
        loaded = type(model).__name__
    except Exception as exc:               # noqa: BLE001
        return _error(f"B06 模型加载失败：{type(exc).__name__}: {exc}",
                      model_version=meta.get("model_version"))

    return _ok(f"B06 模型可加载：{meta.get('model_version')}"
               f"（{loaded}，feature_count={len(meta.get('feature_columns') or [])}）",
               model_version=meta.get("model_version"),
               loaded_type=loaded,
               manifest_sha256=_sha(meta_path),
               weights_sha256=_sha(weights_path))


# --------------------------------------------------------------------------
# 深检查
# --------------------------------------------------------------------------

def deep_health(probe: bool = False) -> dict:
    """深检查：逐项体检外部依赖。

    Args:
        probe: True 时 B03 天气**真的**调一次 API；False（默认）只做离线检查。

    Returns:
        {"status", "scope", "checks": {B02_database, B03_weather, B04_knowledge,
                                       B06_model}, ...}

        顶层 status：
          ok          —— 四项全 ok
          degraded    —— 有 unavailable（某个依赖缺失/不可用，但系统仍能跑）
          error       —— 有 error（检查本身失败）
    """
    checks = {
        "B02_database": _check_b02_database(),
        "B03_weather": _check_b03_weather(probe),
        "B04_knowledge": _check_b04_knowledge(),
        "B06_model": _check_b06_model(),
    }
    statuses = [c.get("status") for c in checks.values()]
    if STATUS_ERROR in statuses:
        overall = STATUS_ERROR
    elif STATUS_UNAVAILABLE in statuses:
        overall = "degraded"
    else:
        overall = STATUS_OK

    return {
        "status": overall,
        "scope": "deep",
        "app_version": baseline_app_version(),
        "model_version": baseline_model_version(),
        "time": _now_iso(),
        "probe": bool(probe),
        "checks": checks,
        "note": ("B03_weather 默认不发真实请求（probe=false）；"
                 "加 ?probe=true 才会真的调一次天气 API。"
                 if not probe else
                 "B03_weather 已按 probe=true 真实调用一次天气 API。"),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B08 健康检查（浅 / 深）")
    ap.add_argument("--deep", action="store_true", help="做深检查")
    ap.add_argument("--probe", action="store_true",
                    help="深检查时真的调一次天气 API（默认不调）")
    args = ap.parse_args(argv)

    if not args.deep:
        print(json.dumps(shallow_health(), ensure_ascii=False, indent=2))
        return 0

    res = deep_health(probe=args.probe)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["status"] in (STATUS_OK, "degraded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
