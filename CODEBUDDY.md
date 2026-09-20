## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, the graph refreshes itself: git hooks (post-commit / post-checkout) run `python tools/graphify_sync.py` in the background, so graphify-out/ tracks HEAD automatically. To refresh manually, run `python tools/graphify_sync.py` (update + cluster-only + full-node html export + vis-network localisation) — do not call `graphify update .` alone, it degrades graph.html to a community-aggregated view above 5000 nodes and re-points it at the unpkg CDN (blank page offline).
- graphify-out/ is a derived artifact: gitignored, rebuilt per machine, never committed. `python tools/graphify_sync.py --status` shows hook state; sync log is graphify-out/sync.log.
