"""B05 Step 3 — 边界异常测试：验证 error / unavailable 场景程序不崩溃"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

from B05.orchestrator import ask, set_inject


def run_case(label: str, question: str, inject: dict | None = None):
    print(f"\n{'='*70}")
    print(f"[{label}] {question}")
    if inject:
        print(f"[注入] tool={inject.get('tool')}, status={inject.get('status')}, error={inject.get('error')}")
    print("-" * 70)
    try:
        set_inject(inject)
        answer = ask(question)
        print(f"回答:\n{answer}")
        print(f"[PASS] 程序正常返回，未崩溃")
    except Exception as e:
        print(f"[FAIL] 程序崩溃: {type(e).__name__}: {e}")
    finally:
        set_inject(None)  # 清理注入状态


if __name__ == "__main__":
    print("=" * 70)
    print("B05 边界异常测试 — error / unavailable 场景验证")
    print("=" * 70)

    # Case B1: get_weather_forecast 返回 unavailable（断网场景）
    run_case(
        "B1",
        "北京大兴现在的天气怎么样？",
        inject={"tool": "get_weather_forecast", "status": "unavailable", "error": "network timeout after 10s"},
    )

    # Case B2: get_current_environment 返回 error（数据库异常场景）
    run_case(
        "B2",
        "当前大棚温度是多少？",
        inject={"tool": "get_current_environment", "status": "error", "error": "database connection failed"},
    )

    # Case B3: get_environment_history 返回 error（查询超时场景）
    run_case(
        "B3",
        "过去24小时大棚湿度有什么变化？",
        inject={"tool": "get_environment_history", "status": "error", "error": "query timeout after 30s"},
    )

    # Case B4: search_tomato_knowledge 返回 error（索引损坏场景）
    run_case(
        "B4",
        "SIMULATED 和 PUBLIC 数据源有什么区别？",
        inject={"tool": "search_tomato_knowledge", "status": "error", "error": "knowledge base index corrupted"},
    )

    # Case B5: 正常调用（无注入），确保修复后正常路径不受影响
    run_case(
        "B5",
        "现在棚内温度是多少？",
        inject=None,
    )

    print("\n" + "=" * 70)
    print("边界异常测试完毕")
    print("=" * 70)
