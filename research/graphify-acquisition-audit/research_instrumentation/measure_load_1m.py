"""Measure loading a pre-built corpus file the way graphify does.

Reports peak RSS at each stage of the storage path so the memory ceiling is
evidence-based rather than guessed. Runs under a soft address-space limit so it
fails cleanly (MemoryError) instead of being OOM-killed.
"""
import json
import resource
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path("/home/user/graphify_repo")
sys.path.insert(0, str(REPO))

from networkx.readwrite import json_graph  # noqa: E402
from graphify.paths import restore_arc_direction  # noqa: E402


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def stage(name, fn):
    t0 = time.perf_counter()
    try:
        out = fn()
    except MemoryError:
        print(f"  {name:28} MemoryError after {time.perf_counter()-t0:.1f}s "
              f"(peak RSS {rss_mb():.0f} MB)", flush=True)
        raise
    dt = time.perf_counter() - t0
    print(f"  {name:28} {dt:7.2f}s  peak RSS {rss_mb():8.0f} MB", flush=True)
    return out


if __name__ == "__main__":
    path = sys.argv[1]
    soft_limit_mb = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    # Cap the address space so an out-of-memory run raises instead of thrashing.
    resource.setrlimit(resource.RLIMIT_AS, (soft_limit_mb * 1024 * 1024,) * 2)
    print(f"file={path} size={Path(path).stat().st_size/1e6:.0f} MB "
          f"limit={soft_limit_mb} MB", flush=True)
    tracemalloc.start()
    raw = stage("read_text", lambda: Path(path).read_text(encoding="utf-8"))
    data = stage("json.loads", lambda: json.loads(raw))
    del raw
    stage("restore_arc_direction", lambda: restore_arc_direction(data))
    G = stage("node_link_graph", lambda: json_graph.node_link_graph(data, edges="links"))
    del data
    print(f"  RESULT nodes={G.number_of_nodes()} edges={G.number_of_edges()} "
          f"peak RSS {rss_mb():.0f} MB", flush=True)
