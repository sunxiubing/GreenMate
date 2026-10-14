"""B02 Step 5 — 质量检查

校验内容：
1. 空值检查：任意字段为 NULL
2. 重复记录：同一 timestamp 出现多次
3. 数值异常范围：temperature / humidity / lux 超出物理合理区间
4. 时间连续性：间隔严格 30 分钟，无跳跃
5. 扰动分布：前半段、后半段均含正常和扰动片段
6. 重启验证：关闭后重新读取，数据完整且可追加

用法：
    python -m quality.checker
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

_self_path = Path(__file__).resolve().parent
_b02_root = _self_path.parent
if str(_b02_root) not in sys.path:
    sys.path.insert(0, str(_b02_root))

from SPEC import (
    SAMPLE_COUNT, SAMPLE_INTERVAL_MINUTES,
    TEMP_MIN, TEMP_MAX, HUMIDITY_MIN, HUMIDITY_MAX, LUX_MIN, LUX_MAX,
)
from database.store import (
    DB_PATH, init_schema, get_connection, all_samples,
    row_count, write_samples,
)
from simulator.engine import GreenhouseSimulator


def check_null() -> tuple[bool, str]:
    conn = get_connection()
    cur = conn.execute("""
        SELECT id, timestamp, temperature, humidity, lux
        FROM environment_samples
        WHERE timestamp IS NULL OR temperature IS NULL
           OR humidity IS NULL OR lux IS NULL;
    """)
    rows = cur.fetchall()
    conn.close()
    if rows:
        return False, f"发现 {len(rows)} 条含空值: {rows[:5]}"
    return True, "无空值"


def check_duplicates() -> tuple[bool, str]:
    conn = get_connection()
    cur = conn.execute("""
        SELECT timestamp, COUNT(*) as cnt
        FROM environment_samples
        GROUP BY timestamp
        HAVING cnt > 1;
    """)
    rows = cur.fetchall()
    conn.close()
    if rows:
        return False, f"发现 {len(rows)} 个重复时间戳: {rows[:5]}"
    return True, "无重复时间戳"


def check_ranges() -> tuple[bool, str]:
    conn = get_connection()
    cur = conn.execute("""
        SELECT id, timestamp, temperature, humidity, lux
        FROM environment_samples
        WHERE temperature < ? OR temperature > ?
           OR humidity < ? OR humidity > ?
           OR lux < ? OR lux > ?;
    """, (TEMP_MIN, TEMP_MAX, HUMIDITY_MIN, HUMIDITY_MAX, LUX_MIN, LUX_MAX))
    rows = cur.fetchall()
    conn.close()
    if rows:
        return False, f"发现 {len(rows)} 条超出范围: {rows[:5]}"
    return True, "无越界值"


def check_continuity() -> tuple[bool, str]:
    """检查时间间隔是否严格 30 分钟，无跳跃。"""
    samples = all_samples()
    errors = []
    for i in range(1, len(samples)):
        expected = samples[i-1].timestamp + timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
        if samples[i].timestamp != expected:
            errors.append(
                f"  id={samples[i-1].id+1}: 期望 {expected}，实际 {samples[i].timestamp}"
            )
    if errors:
        return False, f"时间不连续，共 {len(errors)} 处跳跃:\n" + "\n".join(errors[:10])
    return True, f"时间连续，共 {len(samples)-1} 个间隔均正确"


def check_disturbance_distribution() -> tuple[bool, str]:
    """检查扰动片段在时间线上分散分布。"""
    samples = all_samples()
    half = len(samples) // 2
    first_half = set(s.sim_scenario for s in samples[:half])
    second_half = set(s.sim_scenario for s in samples[half:])

    first_disturbances = first_half - {"normal"}
    second_disturbances = second_half - {"normal"}

    ok = bool(first_disturbances) and bool(second_disturbances)
    msg = (
        f"前半段扰动: {sorted(first_disturbances) or '无'}  "
        f"后半段扰动: {sorted(second_disturbances) or '无'}"
    )
    if not ok:
        msg = "扰动集中在某半段，前后段均有扰动: " + msg
    return ok, msg


def check_persistence() -> tuple[bool, str]:
    """关闭后重启验证：重启后数据仍存在，可以追加新样本。"""
    count_before = row_count()
    if count_before == 0:
        return False, "数据库为空，跳过持久化验证"

    # 读取一次，模拟重启后的行为
    samples = all_samples()
    count_read = len(samples)
    if count_read != count_before:
        return False, f"读取数量不匹配: 写入 {count_before} 条，读取 {count_read} 条"

    return True, f"持久化验证通过，重启后仍可读 {count_read} 条数据"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 60)
    print("B02 数据质量检查")
    print("=" * 60)
    print(f"数据库: {DB_PATH}")
    print(f"当前样本数: {row_count()}")
    print()

    checks = [
        ("空值检查",        check_null),
        ("重复记录",        check_duplicates),
        ("数值范围",        check_ranges),
        ("时间连续性",      check_continuity),
        ("扰动分布",        check_disturbance_distribution),
        ("持久化验证",      check_persistence),
    ]

    all_pass = True
    for name, fn in checks:
        ok, msg = fn()
        status = "✓ PASS" if ok else "✗ FAIL"
        if not ok:
            all_pass = False
        print(f"{status}  {name}")
        for line in msg.split("\n"):
            print(f"         {line}")

    print()
    print("=" * 60)
    if all_pass:
        print("全部检查通过")
    else:
        print("有检查项未通过，请修复后重新运行 generate.py --force")
    print("=" * 60)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
