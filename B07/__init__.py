"""B07 — 大棚关注度分析与解释（Step 1 阶段）

本包按"环节"拆分，每一步都可独立运行、独立复核：

  stability.py   稳定历史窗口扫描 + 透明稳定判定规则（S1-1）
  evidence.py    当前状态 + 最近历史 + 时间锚点（S1-2）
  predict.py     加载 B06 已存模型做一次预测（S1-3）
  judgement.py   结构化判断（纯程序，无 LLM）（S1-4A）
  explain.py     LLM 解释 + 一致性校验（只解释，不判断）（S1-4B）
  runs.py        运行存档 + 事后回溯（S1-5）

设计原则：判断与解释分离，程序结论可脱离 LLM 复算。
"""

__all__ = ["config", "stability", "evidence", "predict", "judgement", "explain", "runs"]
