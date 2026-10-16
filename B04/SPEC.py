"""B04 Step 1&2 — 知识库问题 + 可信资料

Step 1: 3 个必须依赖 GreenMate 资料才能回答的问题
Step 2: 为每个问题匹配可信资料
"""

KB_QUESTIONS = [
    {
        "id": "KB-001",
        "question": "GreenMate 系统中，SIMULATED 和 PUBLIC 两种数据来源的本质区别是什么？在 A/B 对照实验里应该用哪一种？",
        "answer_type": "概念解释",
    },
    {
        "id": "KB-002",
        "question": "B02 的 drought 扰动场景中，humidity_delta 的偏移量是多少？这个偏移量修改后需要重启哪个服务才能生效？",
        "answer_type": "精确数值 + 操作指令",
    },
    {
        "id": "KB-003",
        "question": "当 B03 的 Open-Meteo API 返回 HTTP 400 错误（纬度超出 -90~90°）时，系统为什么不返回上次缓存的成功数据？这样做的好处是什么？",
        "answer_type": "设计决策解释",
    },
]


TRUSTED_SOURCES = [
    {
        "id": "SRC-001",
        "provider": "B01 / B03 项目设计文档",
        "date": "2026",
        "url": "./docs/b01_b03_design.md",
        "scope": "系统数据源定义，SIMULATED、PUBLIC 的区分规则，A/B 实验选取规范",
        "why_trusted": "项目原始设计文档，权威定义两类数据源，专门用于回答 KB-001",
        "supports_kb": ["KB-001"],
    },
    {
        "id": "SRC-002",
        "provider": "B02/simulator/engine.py",
        "date": "2026",
        "url": "./B02/simulator/engine.py",
        "scope": "drought 扰动配置，humidity_delta 参数定义、服务重启要求",
        "why_trusted": "项目模拟器源码，存放扰动偏移量配置，专门回答 KB-002",
        "supports_kb": ["KB-002"],
    },
    {
        "id": "SRC-003",
        "provider": "B03 weather service 代码文档",
        "date": "2026",
        "url": "./docs/b03_weather.md",
        "scope": "接口异常、缓存策略，HTTP 400 失败处理逻辑",
        "why_trusted": "B03 模块官方设计文档，描述接口报错缓存取舍规则，专门回答 KB-003",
        "supports_kb": ["KB-003"],
    },
]


# --------------------------------------------------------------------------
# Step 3: 切片 + 元数据
# --------------------------------------------------------------------------

CHUNKS = [
    # ===== SRC-001: B01 / B03 项目设计文档 =====
    {
        "chunk_id": "CH-001-01",
        "source_id": "SRC-001",
        "section": "SIMULATED vs PUBLIC 区别",
        "page": "./docs/b01_b03_design.md",
        "content": "SIMULATED 数据来源：B02 GreenhouseSimulator 本地模拟器，source_type='simulated'，页面标注'SIMULATED'。PUBLIC 数据来源：B03 Open-Meteo 天气 API，WeatherEvidence 带 _weather 元数据，页面标注'PUBLIC'",
    },
    {
        "chunk_id": "CH-001-02",
        "source_id": "SRC-001",
        "section": "A/B 实验数据源选取规则",
        "page": "./docs/b01_b03_design.md",
        "content": "A/B 对照实验必须使用 SIMULATED 数据，原因：1)可控性——固定种子seed=42生成，336点序列每次相同；2)隔离性——不依赖外部API，避免网络抖动；3)可复现——同一状态任意时刻注入返回相同值",
    },
    {
        "chunk_id": "CH-001-03",
        "source_id": "SRC-001",
        "section": "PUBLIC 不用于 A/B 实验的原因",
        "page": "./docs/b01_b03_design.md",
        "content": "PUBLIC 数据不用于 A/B 对照：Open-Meteo 每次返回实时预报不同，间隔内值可能变化；1分钟粒度的天气预报分辨率不是真正瞬时状态；HTTP 400/429 时 PUBLIC 返回 None，无法保证 A/B 两边有效状态",
    },
    {
        "chunk_id": "CH-001-04",
        "source_id": "SRC-001",
        "section": "format_state 来源标注差异",
        "page": "./docs/b01_b03_design.md",
        "content": "format_state() 根据 data_source 参数区分：data_source='PUBLIC' 时追加 '_weather：该数据来自 Open-Meteo 实时天气 API，非模拟数据'；data_source='SIMULATED' 时不追加任何标注（默认即为模拟）",
    },

    # ===== SRC-002: B02/simulator/engine.py =====
    {
        "chunk_id": "CH-002-01",
        "source_id": "SRC-002",
        "section": "drought 扰动配置",
        "page": "./B02/simulator/engine.py",
        "content": "drought 扰动参数：temp_delta=+6.0，humidity_delta=-10.0，duration_hours=24，transition_hours=6，temp_ceil=44.0，humidity_floor=15.0，noise_std=0.3",
    },
    {
        "chunk_id": "CH-002-02",
        "source_id": "SRC-002",
        "section": "humidity_delta 参数定义位置",
        "page": "./B02/simulator/engine.py",
        "content": "humidity_delta 定义在 Disturbance dataclass 的第 77 行，drought 场景的 humidity_delta=-10.0 定义在 _build_disturbances() 函数第 118 行的 scenarios 数组中",
    },
    {
        "chunk_id": "CH-002-03",
        "source_id": "SRC-002",
        "section": "服务重启要求",
        "page": "./B02/simulator/engine.py",
        "content": "修改扰动偏移量（如 drought 的 humidity_delta）后需要重启 B01 服务才能生效：.env 和模拟器参数在进程启动时读取，运行时修改对运行中的服务无效",
    },
    {
        "chunk_id": "CH-002-04",
        "source_id": "SRC-002",
        "section": "drought 扰动完整生命周期",
        "page": "./B02/simulator/engine.py",
        "content": "drought 扰动完整生命周期：进入阶段 6 小时（smoothstep 过渡）→ 稳定持续 12 小时（humidity_delta=-10.0 全量生效）→ 恢复阶段 6 小时（smoothstep 过渡）→ 回到正常基线",
    },

    # ===== SRC-003: B03 weather service 设计文档 =====
    {
        "chunk_id": "CH-003-01",
        "source_id": "SRC-003",
        "section": "HTTP 400 不返回旧缓存的原因",
        "page": "./docs/b03_weather.md",
        "content": "HTTP 400 不返回旧缓存：400 是请求参数错误（纬度格式错误），不是临时性故障。重新发请求（修正参数后）一定能成功，所以不需要降级到过期数据。Fail Loudly 原则要求显式失败而非静默降级",
    },
    {
        "chunk_id": "CH-003-02",
        "source_id": "SRC-003",
        "section": "静默返回旧数据的危害",
        "page": "./docs/b03_weather.md",
        "content": "静默返回旧数据（phantom data）危害：下游系统以为拿到最新有效数据；温室调控决策基于过时信息可能导致作物受损；问题隐蔽，潜伏数天才被发现；违反 API 隐含契约：成功响应代表数据完整",
    },
    {
        "chunk_id": "CH-003-03",
        "source_id": "SRC-003",
        "section": "B03 缓存策略",
        "page": "./docs/b03_weather.md",
        "content": "B03 缓存策略：成功时缓存到 self._cache；HTTP 400/429/timeout 时不缓存，直接返回 None。不缓存失败结果防止永久失败状态。None vs 空列表：None 表示获取失败，[] 表示数据为空（有效状态）",
    },
    {
        "chunk_id": "CH-003-04",
        "source_id": "SRC-003",
        "section": "标准化错误响应",
        "page": "./docs/b03_weather.md",
        "content": "标准化错误响应结构：{ code, message, retryable, traceId }。retryable=False 表示 400 参数错误不可重试。关键区分：None vs 空，0 vs 不可用。温度 0°C 是有效值，None 表示不可用",
    },
]


if __name__ == "__main__":
    print("=" * 75)
    print("B04 Step 1：知识库问题")
    print("=" * 75)
    for q in KB_QUESTIONS:
        print(f"\n[{q['id']}] {q['question']}")
        print(f"  答案类型: {q['answer_type']}")

    print("\n" + "=" * 75)
    print("B04 Step 2：可信资料清单")
    print("=" * 75)
    for s in TRUSTED_SOURCES:
        print(f"\n[{s['id']}] {s['provider']}")
        print(f"  日期: {s['date']}")
        print(f"  URL:  {s['url']}")
        print(f"  范围: {s['scope']}")
        print(f"  可信理由: {s['why_trusted']}")
        print(f"  支持问题: {', '.join(s['supports_kb'])}")
