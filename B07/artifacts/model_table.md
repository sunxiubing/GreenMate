# 跨阶段 LLM 模型对照表

> 本表由 `python B07/s4_report.py` 从 `B07/artifacts/s4_raw_result.json` 自动生成（模型名来自各 run 存档的 `llm_meta.model`，引用率来自现场统计），**没有手工填写**；S5 行来自 `B07/artifacts/s5_raw_result.json`（同样是实测存档）。

| 阶段 | LLM 模型 | 提示词 sha256 | 问题 sha256 | 备注 |
|---|---|---|---|---|
| S1 | `qwen3.5-27b` | `aa1400cf8568` | `d3cc394bfcbe` |  |
| S2 | `qwen3.5-27b` | `aa1400cf8568` | `d3cc394bfcbe` |  |
| S3 | `qwen3.5-27b` | `aa1400cf8568` | `d3cc394bfcbe` | 引用率 14/16 统计基于此模型 |
| S4 | `qwen-max` | `aa1400cf8568` | `d3cc394bfcbe` | 原模型免费额度耗尽（HTTP 403 insufficient_quota），S4 起换用 |
| S5 | `qwen-max` | `aa1400cf8568` | `d3cc394bfcbe` | 证据退化：天气不可用 |

S1–S3 的 LLM 解释与引用率统计均由 `qwen3.5-27b` 产生并已落盘；
S4 起改用 `qwen-max`，原因是原模型免费额度耗尽。
S5（证据退化：天气不可用）由 `run_079` 等实测存档产生；
跨阶段的 LLM 措辞不可直接比较；
但 B06 预测模型（`b06-20261020-v1`）、核心提示词指纹、固定问题指纹
全程未变，S1–S5 的程序层结论（结构化判断、`combined_attention`、
`evidence_divergence`）不受 LLM 更换影响。
