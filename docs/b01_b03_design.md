# B01 / B03 系统设计文档：数据源定义

## SIMULATED 与 PUBLIC 的本质区别

| 属性 | SIMULATED | PUBLIC |
|------|-----------|--------|
| 数据来源 | B02 GreenhouseSimulator 本地模拟器 | B03 Open-Meteo 天气 API |
| 用途 | A/B 对照实验的对照组（可控基线）| A/B 对照实验的处理组（真实外部证据）|
| 标注方式 | 页面显示 "SIMULATED"，数据库 source_type="simulated" | 页面显示 "PUBLIC"，WeatherEvidence 带 _weather 元数据 |
| 适用场景 | 调试、回归测试、Prompt 工程 | 真实环境监测、农业决策支持 |

## A/B 对照实验的数据源选取规则

**A/B 对照实验必须使用 SIMULATED 数据作为状态注入来源，原因：**

1. **可控性**：SIMULATED 数据由 GreenhouseSimulator 固定种子（seed=42）生成，每次运行产生完全相同的 336 点序列，便于 A/B 对比时控制变量。

2. **隔离性**：SIMULATED 数据不依赖外部 API（A/B 两侧都不调用 Open-Meteo），避免了因网络抖动、API 限流导致的数据不一致。

3. **可复现**：同一状态注入在任意时刻执行都返回相同值，实验结果可复现。

**PUBLIC 数据不用于 A/B 对照的原因：**
- Open-Meteo API 每次返回的实时预报不同，两次 A/B 请求间隔内值可能变化
- API 有 1 分钟粒度的天气预报分辨率，不是真正的瞬时状态
- HTTP 400/429 时 PUBLIC 数据返回 None，无法保证 A/B 两边都有有效状态

## 状态注入的格式差异

当 format_state() 检测到数据源为 PUBLIC 时，会自动在上下文末尾追加：
> "_weather：该数据来自 Open-Meteo 实时天气 API，非模拟数据"

当数据源为 SIMULATED 时，不追加任何标注（默认即为模拟）。

## LLM 调用时的来源标注

B01/llm.py 的 format_state() 函数根据传入的 data_source 参数区分：
- `data_source="SIMULATED"` → 不追加标注
- `data_source="PUBLIC"` → 追加 _weather 标注行

用户可在页面右上角切换数据源，切换后 A/B 对照结果会自动反映来源差异。
