"""B04 Step 5: 回答 + 引用（使用检索命中的 chunk 作为上下文）"""
import sys
sys.path.insert(0, "d:/GreenMate")

from B04.SPEC import KB_QUESTIONS, CHUNKS
from B04.retrieve import retrieve

import os
from pathlib import Path

# Load B01/.env directly
_env_path = Path(__file__).resolve().parent.parent / "B01/.env"
for line in _env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI

TOP_K = 5

client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ["LLM_BASE_URL"],
    timeout=60.0,
)


def build_context(question: str, results: list) -> str:
    """把检索结果渲染成回答可引用的上下文块。"""
    lines = [
        "【检索召回的切片】（以下内容来自真实命中的 chunk_id，请仅引用这些来源）",
        "",
    ]
    for rank, r in enumerate(results, 1):
        lines.append(f"--- Chunk {r['chunk_id']} [来源: {r['source_id']}] ---")
        lines.append(f"页面: {r['page']}")
        lines.append(f"内容: {r['content']}")
        lines.append("")
    return "\n".join(lines)


def ask_with_chunks(question: str, results: list, answer_type: str) -> str:
    """把检索上下文和问题一起发给 LLM，限制只能引用命中的 chunk。"""
    context = build_context(question, results)

    system_prompt = f"""你是一个知识库问答助手。根据提供的【检索召回切片】回答用户问题。

回答规则：
1. 只使用下方给出的切片内容，不要编造不存在的来源。
2. 引用时必须标注 chunk_id 和 source_id。
3. 答案类型：{answer_type}。
4. 用中文回答，控制在 300 字以内。
5. 输出纯文本，不要使用 Markdown 标记。
6. 如果召回切片不足以回答，明确说明哪些信息缺失。

{context}

用户问题：{question}"""

    resp = client.chat.completions.create(
        model=os.environ["LLM_MODEL"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )
    return resp.choices[0].message.content.strip()


# 追加边界测试问题
for _kb in [
    {"id": "KB-004", "question": "GreenMate 大棚系统内置告警音频文件名称是什么？", "answer_type": "边界测试"},
    {"id": "KB-005", "question": "大棚灯光控制 PWM 的工作频率是多少？", "answer_type": "边界测试"},
]:
    KB_QUESTIONS.append(_kb)

for kb in KB_QUESTIONS:
    qid = kb["id"]
    question = kb["question"]
    answer_type = kb["answer_type"]

    print("=" * 80)
    print(f"[{qid}] {question}")
    print(f"答案类型: {answer_type}")
    print("-" * 80)

    results = retrieve(question, top_k=TOP_K)

    print(f"\n[调用 LLM 生成回答...]")
    answer = ask_with_chunks(question, results, answer_type)

    print(f"\n--- 回答 ---\n{answer}")

    print(f"\n--- 引用切片 ---")
    for r in results:
        print(f"  [{r['chunk_id']}] {r['source_id']} | {r['section']}")

    print("\n")
