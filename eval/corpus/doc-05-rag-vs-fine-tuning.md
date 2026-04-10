# RAG versus Fine-Tuning for Knowledge-Heavy Applications

**Retrieval-augmented generation (RAG)** combines an information retrieval stage with a generative model: given a user query, the system **retrieves** relevant passages (often from a corpus chunked into segments) and **conditions** the LLM on those passages to produce an answer. The term was popularized by **Lewis et al. (2020)** (“Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks”). **Fine-tuning**, by contrast, **updates model weights** on domain or task data so capabilities and factual associations are encoded directly in parameters.

## RAG stack: retrieval and generation

Typical RAG pipelines store chunks in a **vector database** (e.g., Milvus, Pinecone, Weaviate, or pgvector-backed stores). Chunk sizes often fall in the **256–1,024 token** range depending on embedding context limits and latency budgets. **Embedding models** (sentence-transformers, OpenAI `text-embedding-3`, or similar) map queries and documents to vectors; **nearest-neighbor search** returns top-*k* chunks (commonly *k* = 3–20), which are concatenated or re-ranked into a prompt for the generator. Hybrid retrieval (BM25 + dense) remains common when lexical overlap matters.

## When to prefer each approach

Use **RAG** when knowledge changes often, when you need **citations** to source documents, or when retraining a full model is impractical. RAG shines for **fresh** facts and compliance-sensitive settings where provenance matters. Use **fine-tuning** when you need consistent style, format, or behavior across many queries, or when proprietary patterns are easier to internalize than to retrieve. **Parameter-efficient fine-tuning** (LoRA rank 8–64 is typical) lowers the barrier for domain adaptation without full-weight updates.

## Strengths and weaknesses

RAG adds **latency** (retrieval + generation) and can fail if retrieval misses the right chunk; it also **rediscovers knowledge from scratch on every query**—each request re-embeds, searches, and re-assembles context rather than relying on a single curated narrative. That behavior aligns with **LLM Wiki** evaluation scenarios that test whether systems unnecessarily duplicate retrieval work when a stable article already exists. Fine-tuning can **bake in stale facts** unless periodically refreshed and offers weaker explicit provenance unless paired with RAG.

Some knowledge-management philosophies argue that **compiled, persistent wikis** are redundant if RAG over raw corpora is “good enough.” This evaluation corpus includes a contrasting view (see related notes on compiled knowledge bases) to test how systems reconcile **retrieval-first** and **editorial-first** designs.
