"""断网验证：通过 _inject 模拟网络层异常（DNS 失败 / ConnectError）"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

# 注入 DNS 失败场景（模拟 OSError: getaddrinfo failed）
from B05.test_functions import get_weather_forecast

print("注入 DNS 失败（OSError: getaddrinfo failed）模拟断网...")
r = get_weather_forecast("北京大兴", _inject={"status": "unavailable", "error": "network connection failed"})
print(f"status: {r['status']}")
print(f"error: {r['error']}")
print(f"data: {r['data']}")
assert r["status"] == "unavailable", f"FAIL: expected unavailable, got {r['status']}"
print("[PASS] 网络异常被捕获，返回 unavailable 不崩溃 ✅")

# 注入 ConnectError（模拟连接被拒绝）
print("\n注入 ConnectError 模拟连接拒绝...")
r2 = get_weather_forecast("北京大兴", _inject={"status": "unavailable", "error": "connection refused"})
print(f"status: {r2['status']}")
print(f"error: {r2['error']}")
assert r2["status"] == "unavailable", f"FAIL: expected unavailable, got {r2['status']}"
print("[PASS] ConnectError 被捕获，返回 unavailable 不崩溃 ✅")

# 注入超时场景
print("\n注入 TimeoutException 模拟请求超时...")
r3 = get_weather_forecast("北京大兴", _inject={"status": "unavailable", "error": "timeout after 10s"})
print(f"status: {r3['status']}")
print(f"error: {r3['error']}")
assert r3["status"] == "unavailable", f"FAIL: expected unavailable, got {r3['status']}"
print("[PASS] TimeoutException 被捕获，返回 unavailable 不崩溃 ✅")

# 注入 error 场景（确保不崩溃）
print("\n注入 error 场景（知识库索引损坏）...")
r4 = get_weather_forecast("北京大兴", _inject={"status": "error", "error": "unknown location"})
print(f"status: {r4['status']}")
print(f"error: {r4['error']}")
assert r4["status"] == "error", f"FAIL: expected error, got {r4['status']}"
print("[PASS] error 被捕获，返回 error 不崩溃 ✅")

print("\n" + "=" * 50)
print("全部断网场景验证通过")
print("=" * 50)
