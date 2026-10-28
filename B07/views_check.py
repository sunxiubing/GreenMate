"""B07 第三遍 · 四视图统一检查（文档 p.69）

检查**四个视图讲的是同一次分析**，而不是"判断本身对不对"。

四条判定：

  U1 **身份一致**：run_id / analysis_time / model /
      system_prompt_sha256 / question_sha256 在五个地方
      （视图一 view_data、视图二 view_evolution_data、
        视图三 view_evidence_data、视图四 view_compare_data 的 A 侧与 B 侧）
      **完全一致**。
  U2 **同一份存档**：四个视图读盘的 run.json 的 sha256 **相同**。
  U3 **零重算**：把 openai / httpx / requests / B03 天气 / B06 推理 /
      B04 知识检索**全部打桩成"一调用就抛"**，四个视图的装配 + 渲染
      **仍然全部成功**。
  U4 **视图一/二/三既有断言仍然通过**（跑那三个测试文件）。

★ 本模块**只读存档**：不调 LLM、不跑 B06、不取天气、不检索知识库、
  不重查 B02、不重跑判据。任何一项不通过都**显式报 FAIL** 并说明差在哪，
  绝不返回静态文案伪装成功。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

#: 视图一/二/三的既有测试文件（U4 跑它们）
VIEW13_TESTS = [
    _PROJECT_ROOT / "B07" / "tests" / "test_views_invariants.py",
    _PROJECT_ROOT / "B07" / "tests" / "test_views_evolution.py",
    _PROJECT_ROOT / "B07" / "tests" / "test_views_evidence.py",
]

IDENTITY_FIELDS = ("run_id", "analysis_time", "model",
                   "system_prompt_sha256", "question_sha256")


def _boom(*a, **kw):
    raise RuntimeError("不该被调用：视图四的检查不允许重新取数 / 重新推理")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# U1 身份一致
# --------------------------------------------------------------------------

def _identity_from_view1(d: dict) -> dict:
    fp = d.get("fingerprint") or {}
    return {
        "run_id": d.get("run_id"),
        "analysis_time": d.get("analysis_time"),
        "model": fp.get("model"),
        "system_prompt_sha256": fp.get("system_prompt_sha256"),
        "question_sha256": fp.get("question_sha256"),
    }


def _identity_from_view2(d: dict) -> dict:
    fp = d.get("fingerprint") or {}
    return {
        "run_id": d.get("run_id"),
        "analysis_time": d.get("analysis_time"),
        "model": fp.get("model"),
        "system_prompt_sha256": fp.get("system_prompt_sha256"),
        "question_sha256": fp.get("question_sha256"),
    }


def _identity_from_view3(d: dict) -> dict:
    fp = d.get("fingerprint") or {}
    return {
        "run_id": d.get("run_id"),
        "analysis_time": d.get("analysis_time"),
        "model": fp.get("model"),
        "system_prompt_sha256": fp.get("system_prompt_sha256"),
        "question_sha256": fp.get("question_sha256"),
    }


def _identity_from_side(side: dict) -> dict:
    fp = side.get("fingerprint") or {}
    return {
        "run_id": side.get("run_id"),
        "analysis_time": side.get("analysis_time"),
        "model": fp.get("model"),
        "system_prompt_sha256": fp.get("system_prompt_sha256"),
        "question_sha256": fp.get("question_sha256"),
    }


def check_identity(run_id: str) -> dict:
    """U1：五个地方的身份字段是否完全一致。"""
    from B07.views import (view_compare_data, view_data,
                           view_evolution_data, view_evidence_data)

    d1 = view_data(run_id)
    d2 = view_evolution_data(run_id)
    d3 = view_evidence_data(run_id)
    d4 = view_compare_data(run_id, run_id)

    places = {
        "视图一 view_data": _identity_from_view1(d1),
        "视图二 view_evolution_data": _identity_from_view2(d2),
        "视图三 view_evidence_data": _identity_from_view3(d3),
        "视图四 view_compare_data(A)": _identity_from_side(d4["a"]),
        "视图四 view_compare_data(B)": _identity_from_side(d4["b"]),
    }

    diffs: list[str] = []
    names = list(places)
    base_name = names[0]
    base = places[base_name]
    for name in names[1:]:
        cur = places[name]
        for f in IDENTITY_FIELDS:
            if cur.get(f) != base.get(f):
                diffs.append(
                    f"{name} 的 {f} = {cur.get(f)!r} ≠ "
                    f"{base_name} 的 {base.get(f)!r}")

    return {
        "passed": not diffs,
        "places": places,
        "diffs": diffs,
        "base": base_name,
    }


# --------------------------------------------------------------------------
# U2 四个视图读的是同一份存档
# --------------------------------------------------------------------------

def check_same_source(run_id: str) -> dict:
    """U2：四个视图读盘的 run.json 的 sha256 是否相同。"""
    from B07.views import RUNS_DIR
    from B07.views_diff import source_path

    path = source_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"找不到存档：{path}")

    # 四个视图都走 `B07.views.load_run()`；这里把 load_run 打桩成
    # "记录每次读到的文件的 sha256"，跑完看四份哈希是否一致。
    import B07.views as views

    reads: list[dict] = []
    original = views.load_run

    def _recording_load(rid: str) -> dict:
        p = source_path(rid)
        reads.append({"run_id": rid, "path": str(p), "sha256": _sha256_file(p)})
        return original(rid)

    views.load_run = _recording_load
    try:
        views.view_data(run_id)
        views.view_evolution_data(run_id)
        views.view_evidence_data(run_id)
        # 视图四通过 views_diff._load() → `from B07.views import load_run`
        # 延迟导入，这里替换模块属性同样生效
        from B07.views_diff import diff_runs
        diff_runs(run_id, run_id)
    finally:
        views.load_run = original

    hashes = {r["sha256"] for r in reads}
    expected = _sha256_file(path)
    paths = {r["path"] for r in reads}
    return {
        "passed": len(hashes) == 1 and hashes == {expected} and len(paths) == 1,
        "source_path": str(path),
        "source_sha256": expected,
        "read_count": len(reads),
        "read_sha256": sorted(hashes),
        "read_paths": sorted(paths),
        "expected_path": str(RUNS_DIR / run_id / "run.json"),
    }


# --------------------------------------------------------------------------
# U3 零重算
# --------------------------------------------------------------------------

def check_zero_recompute(run_id: str) -> dict:
    """U3：把所有外部依赖打桩成"一调用就抛"，四个视图仍装配 + 渲染成功。"""
    import socket

    import httpx
    import openai
    import requests

    # ★ B04.retrieve 在**导入时**就把 KB 检索结果打印一遍（模块级代码）。
    #   导入只是为了打桩，不是为了用；这里把它的输出吞掉，
    #   免得检查结果里混进一堆与检查无关的检索日志。
    import contextlib
    import io

    class _Quiet(io.StringIO):
        """能顶替 stdout 的哑对象（B04.retrieve 导入时会调 reconfigure）。"""

        def reconfigure(self, *a, **kw):
            return None

    with contextlib.redirect_stdout(_Quiet()):
        import B01.llm as b01_llm
        import B03.weather as b03_weather
        import B04.retrieve as b04_retrieve
        import B05.test_functions as b05_tf
        import B06.inference.inference as b06_inference
        import B02.database.store as b02_store

    from B07.views import (render_compare, render_evidence, render_evolution,
                           render_overview, view_compare_data, view_data,
                           view_evolution_data, view_evidence_data)

    real_socket = socket.socket

    class NoNet(real_socket):            # type: ignore[misc]
        def connect(self, *a, **kw):
            raise RuntimeError("不该被调用：统一检查不允许联网")

        def connect_ex(self, *a, **kw):
            raise RuntimeError("不该被调用：统一检查不允许联网")

    steps: list[dict] = []
    with ExitStack() as stack:
        # ---- 打桩：任何外部调用都直接抛 ----
        stack.enter_context(_patch(b01_llm, "get_client", _boom))
        stack.enter_context(_patch(b01_llm, "ask", _boom))
        stack.enter_context(_patch(openai, "OpenAI", _boom))
        stack.enter_context(_patch(openai, "AzureOpenAI", _boom,
                                   raising=False))
        stack.enter_context(_patch(httpx, "Client", _boom))
        stack.enter_context(_patch(httpx, "AsyncClient", _boom))
        stack.enter_context(_patch(httpx, "get", _boom))
        stack.enter_context(_patch(httpx, "post", _boom))
        stack.enter_context(_patch(requests, "get", _boom))
        stack.enter_context(_patch(requests, "post", _boom))
        stack.enter_context(_patch(b03_weather, "get_weather", _boom))
        stack.enter_context(_patch(b03_weather, "_api_request", _boom))
        stack.enter_context(_patch(b05_tf, "get_weather_forecast", _boom))
        stack.enter_context(_patch(b06_inference, "predict_window", _boom))
        stack.enter_context(_patch(b06_inference, "load_for_inference", _boom))
        stack.enter_context(_patch(b04_retrieve, "retrieve", _boom))
        stack.enter_context(_patch(b02_store, "query_samples", _boom,
                                   raising=False))
        stack.enter_context(_patch(b02_store, "get_samples", _boom,
                                   raising=False))
        stack.enter_context(_patch(socket, "socket", NoNet))

        for label, fn in (
            ("视图一 装配", lambda: view_data(run_id)),
            ("视图一 渲染", lambda: render_overview(run_id)),
            ("视图二 装配", lambda: view_evolution_data(run_id)),
            ("视图二 渲染", lambda: render_evolution(run_id)),
            ("视图三 装配", lambda: view_evidence_data(run_id)),
            ("视图三 渲染", lambda: render_evidence(run_id)),
            ("视图四 装配", lambda: view_compare_data(run_id, run_id)),
            ("视图四 渲染", lambda: render_compare(run_id, run_id)),
        ):
            try:
                fn()
                steps.append({"step": label, "ok": True, "error": None})
            except Exception as exc:
                steps.append({"step": label, "ok": False,
                              "error": f"{type(exc).__name__}: {exc}"})

    failed = [s for s in steps if not s["ok"]]
    return {
        "passed": not failed,
        "steps": steps,
        "failed": failed,
        "stubbed": ["openai", "httpx", "requests", "B03.weather",
                    "B05.get_weather_forecast", "B06.inference",
                    "B04.retrieve", "B02.database.store", "socket"],
    }


class _patch:
    """极简 monkeypatch（不依赖 pytest），退出时自动还原。"""

    def __init__(self, obj, name, fn, raising=True):
        self.obj, self.name, self.fn = obj, name, fn
        self.raising = raising
        self.had = False
        self.old = None

    def __enter__(self):
        self.had = hasattr(self.obj, self.name)
        if self.had:
            self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.fn)
        return self

    def __exit__(self, *exc):
        if self.had:
            setattr(self.obj, self.name, self.old)
        else:
            try:
                delattr(self.obj, self.name)
            except AttributeError:
                pass
        return False


# --------------------------------------------------------------------------
# U4 视图一/二/三既有断言仍然通过
# --------------------------------------------------------------------------

def check_existing_tests() -> dict:
    """U4：跑视图一/二/三的既有测试文件，看是否仍然全过。"""
    files = [str(p) for p in VIEW13_TESTS if p.exists()]
    if not files:
        return {"passed": False, "detail": "找不到视图一/二/三的测试文件",
                "returncode": None, "stdout": ""}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-q", "--no-header", "-p",
         "no:cacheprovider"],
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    return {
        "passed": proc.returncode == 0,
        "files": [Path(f).name for f in files],
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
    }


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def check_same_analysis(run_id: str, *, run_tests: bool = True) -> dict:
    """四视图统一检查。

    Returns:
        {"run_id", "identity_consistent", "source_sha256", "zero_recompute",
         "views_checked", "detail"}
    """
    from B07.views import (view_compare_data, view_data,
                           view_evolution_data, view_evidence_data)
    from B07.views_diff import diff_runs

    views_checked = [
        "视图一 状态总览（view_data / render_overview）",
        "视图二 状态演化（view_evolution_data / render_evolution）",
        "视图三 证据空间（view_evidence_data / render_evidence）",
        "视图四 场景回放与对比（view_compare_data / render_compare）",
    ]
    detail: list[dict] = []

    # ---- U1 ----
    u1 = check_identity(run_id)
    detail.append({
        "id": "U1", "name": "身份字段在五个地方完全一致",
        "passed": u1["passed"],
        "actual": ("五处一致：" + " / ".join(
            f"{k}={v.get('run_id')},{v.get('analysis_time')},"
            f"{v.get('system_prompt_sha256')},{v.get('question_sha256')}"
            for k, v in u1["places"].items())
            if u1["passed"] else "；".join(u1["diffs"])),
    })

    # ---- U2 ----
    u2 = check_same_source(run_id)
    detail.append({
        "id": "U2", "name": "四个视图读盘的 run.json 的 sha256 相同",
        "passed": u2["passed"],
        "actual": (f"读盘 {u2['read_count']} 次，sha256="
                   f"{u2['source_sha256'][:16]}…，路径唯一="
                   f"{len(u2['read_paths']) == 1}"
                   + ("" if u2["passed"] else
                      f"；实际读到 {u2['read_sha256']}")),
    })

    # ---- U3 ----
    u3 = check_zero_recompute(run_id)
    detail.append({
        "id": "U3", "name": "零重算：打桩后四个视图仍能装配 + 渲染",
        "passed": u3["passed"],
        "actual": ("打桩 " + "、".join(u3["stubbed"]) +
                   f" 后 {len(u3['steps'])} 步全部成功"
                   if u3["passed"] else
                   "；".join(f"{s['step']}: {s['error']}" for s in u3["failed"])),
    })

    # ---- U4 ----
    if run_tests:
        u4 = check_existing_tests()
        detail.append({
            "id": "U4", "name": "视图一/二/三既有断言仍然通过",
            "passed": u4["passed"],
            "actual": (f"{'、'.join(u4.get('files') or [])} 全部通过"
                       if u4["passed"] else
                       (u4.get("stdout", "")[-600:] or "测试未通过")),
        })
    else:
        detail.append({
            "id": "U4", "name": "视图一/二/三既有断言仍然通过",
            "passed": True,
            "actual": "本次检查跳过了子进程跑测试（run_tests=False）",
        })

    all_pass = all(d["passed"] for d in detail)
    return {
        "run_id": run_id,
        "identity_consistent": bool(u1["passed"]),
        "source_sha256": u2["source_sha256"],
        "zero_recompute": bool(u3["passed"]),
        "views_checked": views_checked,
        "detail": detail,
        "passed": all_pass,
        "u1": u1,
        "u2": u2,
        "u3": u3,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def render_check(res: dict) -> str:
    W = 78
    L = ["=" * W, " B07 · 四视图统一检查（文档 p.69）", "=" * W,
         f" run_id = {res['run_id']}", ""]
    for d in res["detail"]:
        mark = "PASS" if d["passed"] else "**FAIL**"
        L.append(f" [{mark}] {d['id']}  {d['name']}")
        L.append(f"        实际：{d['actual']}")
        if not d["passed"]:
            L.append(f"        ★ 差在哪：{d['actual']}")
    L.append("")
    L.append(f" identity_consistent = {res['identity_consistent']}")
    L.append(f" source_sha256        = {res['source_sha256']}")
    L.append(f" zero_recompute       = {res['zero_recompute']}")
    L.append(f" 视图检查范围：")
    for v in res["views_checked"]:
        L.append(f"   · {v}")
    L.append("")
    L.append(f" 总体：{'全部通过 ✅' if res['passed'] else '**存在 FAIL ❌**'}")
    L.append("=" * W)
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B07 四视图统一检查（只读存档，绝不重新计算）")
    ap.add_argument("run_id", help="run_id，例如 run_082")
    ap.add_argument("--no-tests", action="store_true",
                    help="跳过 U4（不跑视图一/二/三的既有测试）")
    args = ap.parse_args(argv)

    try:
        res = check_same_analysis(args.run_id, run_tests=not args.no_tests)
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        return 2
    except Exception as exc:              # 失败显式暴露
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 2

    print(render_check(res))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
