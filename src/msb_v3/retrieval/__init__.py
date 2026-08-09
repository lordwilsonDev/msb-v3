"""Semantic Retrieval Router (P0, on-stack).

Query -> weighted multi-index plan -> parallel dispatch -> RRF fusion ->
provenance-annotated context. Zero new dependencies: all three index routes
(vector / structural / temporal) are served by the existing Qdrant RAG store
in msb_v3.api.rag, and the planner is deterministic (zero LLM calls).
"""
