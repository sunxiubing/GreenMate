"""B04 Step 4: 向量检索（TF-IDF + Cosine Similarity，纯 Python，无需大模型 API）"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "d:/GreenMate")

from B04.SPEC import KB_QUESTIONS, CHUNKS
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

TOP_K = 5

# 1. 构建语料库
corpus = [c["content"] for c in CHUNKS]
chunk_ids = [c["chunk_id"] for c in CHUNKS]

# 2. TF-IDF 向量化（character n-gram，支持中文）
vectorizer = TfidfVectorizer(
    analyzer="char",
    ngram_range=(1, 3),
    token_pattern=r"(?u)\b\w+\b",
)
tfidf_matrix = vectorizer.fit_transform(corpus)

# 3. 检索函数
def retrieve(question: str, top_k: int = TOP_K):
    q_vec = vectorizer.transform([question])
    scores = cosine_similarity(q_vec, tfidf_matrix).flatten()
    top_indices = np.argsort(scores)[::-1][:top_k]
    results = []
    for idx in top_indices:
        c = CHUNKS[idx]
        results.append({
            "chunk_id": c["chunk_id"],
            "source_id": c["source_id"],
            "section": c["section"],
            "page": c["page"],
            "score": float(scores[idx]),
            "content": c["content"],
        })
    return results


# 4. 对每个 KB 问题执行检索
for kb in KB_QUESTIONS:
    qid = kb["id"]
    question = kb["question"]
    print("=" * 80)
    print(f"[{qid}] {question}")
    print(f"答案类型: {kb['answer_type']}")
    print("-" * 80)

    results = retrieve(question)
    for rank, r in enumerate(results, 1):
        print(f"\n  #{rank}  {r['chunk_id']}  score={r['score']:.4f}")
        print(f"  来源: {r['source_id']} | {r['section']}")
        print(f"  页面: {r['page']}")
        print(f"  内容: {r['content']}")

    print("\n")
