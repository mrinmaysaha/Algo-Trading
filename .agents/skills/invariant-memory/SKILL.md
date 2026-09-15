---
name: invariant-memory
description: Long-term knowledge graph persistence and retrieval using the Memory MCP to survive conversation compactions and store repository invariants.
---

# Invariant & Architecture Memory Skill

Use this skill to persist critical repository discoveries, broker constraints, symbol configurations, and algorithmic invariants across sessions.

## Storing Invariants (Memory MCP)
When a critical decision, constraint, or bug fix is confirmed:
- Use `create_entities` with entity names like `SymbolSpec:SILVERM`, `BrokerAdapter:AngelOne`, or `PortfolioRiskLimit`.
- Use `add_observations` to attach factual rules (e.g., "SILVERM options lot size is 1 lot, margin ceiling ₹1.2L").
- Use `create_relations` to link dependencies (e.g., `GOLDM` -> `depends_on` -> `1h_EMA200`).

## Recalling Invariants
At the start of complex tasks or after a context compaction:
- Call `read_graph` or `search_nodes` to recall established facts rather than re-scanning entire directories.
