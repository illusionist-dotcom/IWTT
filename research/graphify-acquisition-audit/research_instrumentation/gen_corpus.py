"""Stream a synthetic node-link corpus to disk without holding it in memory.

Used only to prepare a 1M-node input file for the Graphify scale measurement,
so that the loader's own memory cost is what gets measured (not the generator's).
"""
import json
import random
import sys

WORDS = [
    "enemies", "learn", "fight", "combat", "memory", "thought", "trick", "illusion",
    "graveyard", "resurrect", "experiment", "prototype", "project", "chimera",
    "scape", "world", "engine", "render", "shader", "physics", "ai", "behaviour",
    "persona", "narrative", "idea", "revise", "cluster", "link", "recall",
]

n_nodes = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
path = sys.argv[2] if len(sys.argv) > 2 else "corpus_1m.json"
rng = random.Random(7)
n_hubs = max(1, n_nodes // 40)

# Node ids are interned-free short strings; labels are built on the fly.
with open(path, "w", encoding="utf-8") as fh:
    fh.write('{"directed": false, "multigraph": false, "graph": {}, "nodes": [')
    for i in range(n_nodes):
        if i:
            fh.write(",")
        label = f"{rng.choice(WORDS)} {rng.choice(WORDS)} {i}"
        fh.write(json.dumps({
            "id": f"n{i}", "label": label, "source_file": f"vault/{i // 500}.md",
            "source_location": f"L{(i % 400) + 1}", "file_type": "document",
            "community": i % max(1, n_nodes // 200), "norm_label": label.lower(),
        }))
    fh.write('], "links": [')
    first = True
    for i in range(n_nodes):
        rng_local = rng
        for _ in range(rng_local.randint(1, 3)):
            j = min(n_nodes - 1, max(0, int(rng_local.gauss(i, 8))))
            if j == i:
                continue
            if not first:
                fh.write(",")
            first = False
            fh.write(json.dumps({
                "source": f"n{i}", "target": f"n{j}", "relation": "references",
                "confidence": "EXTRACTED", "source_file": f"vault/{i // 500}.md",
                "weight": 1.0,
            }))
        if rng_local.random() < 0.04:
            if not first:
                fh.write(",")
            first = False
            fh.write(json.dumps({
                "source": f"n{i}", "target": f"n{rng_local.randrange(0, n_hubs)}",
                "relation": "related_to", "confidence": "INFERRED",
                "source_file": f"vault/{i // 500}.md", "weight": 1.0,
            }))
    fh.write("]}")
print(f"wrote {path} for {n_nodes} nodes")
