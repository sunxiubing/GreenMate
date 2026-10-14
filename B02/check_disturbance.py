"""查询 environment_samples 表中的扰动数据"""
import sqlite3
import sys
from pathlib import Path

_self_path = Path(__file__).resolve().parent
DB_PATH = _self_path / "greenmate.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.execute(
    "SELECT id, timestamp, temperature, humidity, lux, sim_scenario "
    "FROM environment_samples WHERE sim_scenario != 'normal' LIMIT 20;"
)

print(f"{'id':>3}  {'timestamp':<25}  {'temp':>5}  {'humid':>5}  {'scenario'}")
print("-" * 70)
for r in cur.fetchall():
    print(f"{r[0]:>3}  {r[1]:<25}  {r[2]:>5.1f}  {r[3]:>5.1f}  {r[5]}")

conn.close()
