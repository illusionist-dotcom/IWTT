"""Research instrumentation: measure graphify's graph/query path on synthetic
'thought-shaped' corpora. Read-only against a clone of Graphify-Labs/graphify.

Nothing here modifies Graphify or IWTT. Output: JSONL of timings.
"""
from __future__ import annotations

import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path("/home/user/graphify_repo")
sys.path.insert(0, str(REPO))

from networkx.readwrite import json_graph  # noqa: E402

import graphify.cluster as cluster_mod  # noqa: E402
import graphify.serve as serve  # noqa: E402
from graphify.paths import restore_arc_direction  # noqa: E402

# ---------------------------------------------------------------- synthetic corpus
WORDS = [
    "enemies", "learn", "fight", "combat", "memory", "thought", "trick", "illusion",
    "graveyard", "resurrect", "experiment", "prototype", "project", "chimera",
    "scape", "world", "engine", "render", "shader", "physics", "ai", "behaviour",
    "persona", "narrative", "idea", "revise", "cluster", "link", "recall",
]


def make_corpus(n_nodes: int, seed: int = 7) -> dict:
    """Node-link dict shaped like graphify's graph.json.

    Structure: ~1/40 of nodes are hubs (thoughts with many links), the rest are
    clustered with power-law-ish degree, mirroring a note graph more than a
    code graph.
    """
    rng = random.Random(seed)
    nodes = []
    for i in range(n_nodes):
        label = f"{rng.choice(WORDS)} {rng.choice(WORDS)} {i}"
        nodes.append({
            "id": f"n{i}",
            "label": label,
            "source_file": f"vault/{i // 500}.md",
            "source_location": f"L{(i % 400) + 1}",
            "file_type": "document",
            "community": i % max(1, n_nodes // 200),
            "norm_label": label.lower(),
        })
    links = []
    n_hubs = max(1, n_nodes // 40)
    hubs = list(range(n_hubs))
    for i in range(n_nodes):
        # local cluster edges
        for _ in range(rng.randint(1, 3)):
            j = min(n_nodes - 1, max(0, int(rng.gauss(i, 8))))
            if j != i:
                links.append({
                    "source": f"n{i}", "target": f"n{j}",
                    "relation": "references", "confidence": "EXTRACTED",
                    "source_file": f"vault/{i // 500}.md", "weight": 1.0,
                })
        # occasional hub edge
        if rng.random() < 0.04:
            links.append({
                "source": f"n{i}", "target": f"n{rng.choice(hubs)}",
                "relation": "related_to", "confidence": "INFERRED",
                "source_file": f"vault/{i // 500}.md", "weight": 1.0,
            })
    return {"directed": False, "multigraph": False, "graph": {}, "nodes": nodes, "links": links}


def timed(fn, *a, **kw):
    tracemalloc.start()
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    dt = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return out, dt, peak / 1e6


def load_graph(data: dict):
    restore_arc_direction(data)
    G = json_graph.node_link_graph(data, edges="links")
    return G


def main(size: int, out):
    rec: dict = {"size": size}

    # 1. load
    data = make_corpus(size)
    G, dt, peak = timed(load_graph, data)
    rec["load_s"] = round(dt, 3)
    rec["load_peak_mb"] = round(peak, 1)
    rec["nodes"] = G.number_of_nodes()
    rec["edges"] = G.number_of_edges()
    json_size = len(json.dumps(data).encode()) / 1e6
    rec["graph_json_mb"] = round(json_size, 1)
    print(f"[{size}] loaded {rec['nodes']} nodes / {rec['edges']} edges "
          f"({json_size:.1f} MB json) in {dt:.2f}s, peak {peak:.0f} MB", flush=True)

    # 2. clustering (Leiden if graspologic present, else networkx Louvain)
    communities, dt, peak = timed(cluster_mod.cluster, G)
    rec["cluster_s"] = round(dt, 3)
    rec["cluster_peak_mb"] = round(peak, 1)
    rec["n_communities"] = len(communities)
    rec["largest_community"] = max((len(v) for v in communities.values()), default=0)
    print(f"[{size}] cluster() {dt:.2f}s -> {len(communities)} communities", flush=True)

    # 3. cohesion scoring
    scores, dt, _ = timed(cluster_mod.score_all, G, communities)
    rec["cohesion_s"] = round(dt, 3)

    # 4. trigram index build + first query
    G.graph.pop("_trigram_index", None)
    _, dt, peak = timed(serve._get_trigram_index, G)
    rec["trigram_index_s"] = round(dt, 3)
    rec["trigram_index_peak_mb"] = round(peak, 1)
    n_postings = sum(len(v) for v in G.graph["_trigram_index"]["postings"].values())
    rec["trigram_postings"] = n_postings
    print(f"[{size}] trigram index {dt:.2f}s ({n_postings} postings)", flush=True)

    # 5. query scoring (the retrieval core)
    queries = ["enemies learn fight", "graveyard resurrect experiment",
               "chimera render shader physics", "thought revive cluster"]
    times = []
    for q in queries:
        terms = serve._query_terms(q)
        _res, dt, _ = timed(serve._score_query, G, terms, collect_per_term_seeds=True)
        times.append(dt)
    rec["query_score_s_median"] = round(sorted(times)[len(times) // 2], 4)
    rec["query_score_s_max"] = round(max(times), 4)

    # 6. end-to-end query_graph text (score -> seeds -> BFS -> budgeted text)
    q = queries[0]
    terms = serve._query_terms(q)
    qs, dt_score, _ = timed(serve._score_query, G, terms, collect_per_term_seeds=True)
    scored = qs.ranked
    _, dt_seed, _ = timed(serve._pick_seeds, scored, G=G,
                          best_seed_by_term=qs.best_seed_by_term,
                          term_order=qs.term_order)
    seeds = serve._pick_seeds(scored, G=G, best_seed_by_term=qs.best_seed_by_term,
                              term_order=qs.term_order)
    _, dt_bfs, _ = timed(serve._bfs, G, seeds or [scored[0][1]], 3)
    visited, edges_seen = serve._bfs(G, seeds or [scored[0][1]], 3)
    text, dt_text, _ = timed(serve._subgraph_to_text, G, visited, edges_seen, 2000,
                             seeds=seeds)
    rec["query_total_s"] = round(dt_score + dt_seed + dt_bfs + dt_text, 4)
    rec["q_score_s"] = round(dt_score, 4)
    rec["q_seed_s"] = round(dt_seed, 4)
    rec["q_bfs_s"] = round(dt_bfs, 4)
    rec["q_render_s"] = round(dt_text, 4)
    rec["bfs_s"] = round(dt_bfs, 4)
    rec["bfs_visited"] = len(visited)
    rec["bfs_edges"] = len(edges_seen)
    rec["render_s"] = round(dt_text, 4)
    rec["render_chars"] = len(text)
    print(f"[{size}] query end-to-end {rec['query_total_s']}s "
          f"(bfs visited {len(visited)}, {len(edges_seen)} edges, "
          f"{len(text)} chars rendered)", flush=True)

    # 7. shortest path (path queries)
    src, dst = "n0", f"n{size - 1}"
    try:
        _, dt_sp, _ = timed(serve._path_search_graph, G, False)
        rec["path_search_graph_s"] = round(dt_sp, 4)
    except Exception as e:  # noqa: BLE001
        rec["path_search_graph_s"] = f"error: {e}"

    out.write(json.dumps(rec) + "\n")
    out.flush()
    return rec


if __name__ == "__main__":
    sizes = [int(s) for s in sys.argv[1:]] or [1000, 10000, 100000]
    with open("/home/user/research_instrumentation/timings.jsonl", "a") as out:
        out.write(json.dumps({"run_started": time.time(), "sizes": sizes}) + "\n")
        for s in sizes:
            try:
                main(s, out)
            except Exception as exc:  # noqa: BLE001
                out.write(json.dumps({"size": s, "error": repr(exc)}) + "\n")
                out.flush()
                print(f"[{s}] ERROR {exc!r}", flush=True)
