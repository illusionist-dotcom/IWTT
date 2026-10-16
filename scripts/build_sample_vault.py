#!/usr/bin/env python3
"""Build a sample IWTT v0.2 vault for demos, manual testing and benchmarks.

    python3 scripts/build_sample_vault.py --data-dir /tmp/iwtt-sample
    python3 scripts/build_sample_vault.py --data-dir /tmp/iwtt-1k  --fill 1000
    python3 scripts/build_sample_vault.py --data-dir /tmp/iwtt-10k --fill 10000

This script creates its OWN vault in the directory you give it.  It never calls
``Vault.seed_demo()`` and never writes to ``~/.iwtt`` unless you point it there,
so the seven fictional demo records keep their exact documented meaning and the
``test_demo_records_are_explicit_and_removable`` contract stays intact.

The hand-authored corpus below is written to exercise every recall path v0.2
claims to support, so a reviewer can verify a behaviour by typing a phrase:

    exact phrase      "enemies should remember how the player fights"
    word form         "enemy adaptation"            (adapt → adapting → adaptation)
    typo / fuzzy      "enemys adapt to plyer"
    vague fragment    "something about difficulty feeling unfair"
    tag               "enemy ai"
    related thought   "procedural dungeon seeds"     (reached over a saved edge)
    lifecycle         "abandoned turn based combat"  (a Graveyard thought)
    time              "what did I write last week about recall"
    ambiguity         "game"                         (several near-equal matches)
    no match          "quantum chromodynamics"       (honest refusal)

Everything created here is ordinary canonical data: thoughts, revisions, tags,
relationships with real provenance, and lifecycle events.  Nothing derived is
seeded — the recall indexes build themselves the first time you search.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iwtt.store import Vault  # noqa: E402

# --------------------------------------------------------------------------
# Hand-authored corpus.  (text, type, title, tags)
# --------------------------------------------------------------------------
CORPUS: list[tuple[str, str, str, list[str]]] = [
    (
        "Enemies should remember how the player fights and adapt after three encounters. "
        "If the player keeps using the same combo, the enemy should start countering it.",
        "THOUGHT", "Enemy memory and adaptation", ["enemy ai", "game design", "combat"],
    ),
    (
        "A difficulty curve that punishes adaptation feels unfair. If the game gets harder "
        "precisely because I got better at it, I will read that as cheating rather than challenge.",
        "THOUGHT", "Adaptive difficulty can feel unfair", ["difficulty", "game design", "fairness"],
    ),
    (
        "Procedural dungeon generation needs a stable seed per floor so a player can describe "
        "a room to a friend and both of them can find it again.",
        "THOUGHT", "Seeded procedural dungeons", ["procedural", "dungeon", "seeds"],
    ),
    (
        "Old abandoned idea: turn-based combat prototype where every action costs stamina. "
        "It slowed the game down too much and I buried it.",
        "THOUGHT", "Turn-based stamina combat (abandoned)", ["combat", "prototype", "abandoned"],
    ),
    (
        "Save games should record the seed and the adaptation counters, not just position. "
        "Otherwise a reload silently resets everything the enemies learned.",
        "THOUGHT", "Persistence for learned behaviour", ["saves", "persistence", "enemy ai"],
    ),
    (
        "The recall problem in my own head is the same problem the enemies have: recognise a "
        "pattern from a fragment, then retrieve the full memory. That symmetry is worth designing around.",
        "THOUGHT", "Player memory mirrors enemy memory", ["recall", "design insight"],
    ),
    (
        "Experiment: give one enemy type a three-encounter memory and measure how often players "
        "notice the change without being told. Target: noticed by 60% within two fights.",
        "EXPERIMENT", "Three-encounter memory experiment", ["experiment", "enemy ai", "playtest"],
    ),
    (
        "Experiment: run the same combat encounter with and without adaptive difficulty and ask "
        "players which felt fair. Fairness rating matters more than win rate.",
        "EXPERIMENT", "Fairness A/B for adaptive difficulty", ["experiment", "difficulty", "fairness"],
    ),
    (
        "Invention: a decay function for enemy memory so old patterns fade unless reinforced. "
        "Half-life per pattern, not per enemy — a rarely seen trick should be forgotten faster.",
        "INVENTION", "Half-life decay for enemy memory", ["invention", "enemy ai", "decay"],
    ),
    (
        "Invention: show the player a small diegetic hint when an enemy has learned something, "
        "so adaptation is legible instead of feeling like the game cheated.",
        "INVENTION", "Legible adaptation hints", ["invention", "game design", "feedback"],
    ),
    (
        "Project: build a vertical slice with one dungeon, two enemy types, seeded generation, "
        "and the three-encounter memory. Four weeks, no new mechanics after week two.",
        "PROJECT", "Vertical slice: seeded dungeon", ["project", "vertical slice", "dungeon"],
    ),
    (
        "Project: the recall system for my own notes. Local-first, no cloud, no embeddings. "
        "If I cannot explain why a result matched, the result is not good enough.",
        "PROJECT", "Local-first recall system", ["project", "recall", "privacy"],
    ),
    (
        "Deterministic ranking matters more than clever ranking. Given the same notes and the "
        "same fragment, I want the same order every time, and I want to be able to ask why.",
        "THOUGHT", "Determinism over cleverness", ["recall", "determinism", "design principle"],
    ),
    (
        "Refusing to answer is a feature. When three notes match equally well, the honest output "
        "is 'I found three likely memories', not a confident guess at one of them.",
        "THOUGHT", "Ambiguity refusal as a feature", ["recall", "honesty", "ambiguity"],
    ),
    (
        "Never let a derived index become the truth. If deleting every index loses a single "
        "original word, the architecture is wrong.",
        "THOUGHT", "Canonical beats derived", ["architecture", "principle", "data"],
    ),
    (
        "Character trigrams catch typos that stemming cannot: 'plyer' and 'player' share almost "
        "no morphology but share most of their trigrams.",
        "THOUGHT", "Trigrams catch what stemming misses", ["recall", "search", "typos"],
    ),
    (
        "Hub suppression: a note connected to everything is connected to nothing. Penalise very "
        "high degree nodes during graph expansion instead of deleting them.",
        "THOUGHT", "Hubs are noise, not signal", ["graph", "recall", "hubs"],
    ),
    (
        "Direction matters in a memory graph. 'A led to B' is not the same claim as 'B led to A', "
        "so a path search that ignores direction is inventing a relationship I never made.",
        "THOUGHT", "Directed edges carry meaning", ["graph", "relationships", "principle"],
    ),
    (
        "An AI suggestion is a proposal until I confirm it. Confirmation should never happen as a "
        "side effect of searching, opening, or marking a result useful.",
        "THOUGHT", "Suggestions need explicit confirmation", ["ai boundary", "provenance", "principle"],
    ),
    (
        "Graveyard thoughts are not failures, they are paused. Burying an idea must keep its "
        "original text and its history, and bringing it back must not pretend it never left.",
        "THOUGHT", "The Graveyard is a pause, not a delete", ["graveyard", "lifecycle", "principle"],
    ),
    (
        "Time context in recall should never fabricate precision. 'Last spring' is a range I "
        "actually remember; a timestamp I do not remember is a lie I would rather not be told.",
        "THOUGHT", "Time hints without false precision", ["recall", "time", "honesty"],
    ),
    (
        "Query-outcome learning: remember which phrasing led me to the right note, decay it over "
        "months, and let me reset it. Useful, but it must stay derived and inspectable.",
        "THOUGHT", "Learning from my own searches", ["recall", "learning", "query memory"],
    ),
    (
        "Explanations should be structured reasons with evidence, not generated prose. Prose "
        "sounds confident even when the retrieval was a coin flip.",
        "THOUGHT", "Structured reasons over prose", ["explanations", "honesty", "design principle"],
    ),
    (
        "Coffee, oats, bananas, and the good olive oil. Also ask about the ethernet cable.",
        "THOUGHT", "Shopping list", ["personal", "shopping"],
    ),
    (
        "The bug where the save file loads but adaptation counters reset is really the same bug "
        "as forgetting to persist the seed. One fix, two symptoms.",
        "THOUGHT", "One persistence bug, two symptoms", ["bug", "saves", "persistence"],
    ),
    (
        "If the enemy learns too fast the player feels hunted; too slow and nothing feels alive. "
        "The interesting band is narrow and probably per-enemy, not global.",
        "THOUGHT", "Learning rate is a feel problem", ["enemy ai", "balance", "game design"],
    ),
    (
        "Write down the fragment, not the conclusion. Half-remembered phrases are the hook that "
        "brings the whole thought back later, so they are worth capturing verbatim.",
        "THOUGHT", "Capture fragments verbatim", ["recall", "habits", "writing"],
    ),
    (
        "A graph view is a browsing aid, not a world. Sixteen nodes I can read beats six hundred "
        "I cannot, and I should never need the whole graph in memory to answer one question.",
        "THOUGHT", "Bounded views over infinite worlds", ["graph", "principle", "scope"],
    ),
]

#: Relationships: (source index, target index, type, provenance)
LINKS: list[tuple[int, int, str, str]] = [
    (0, 1, "LED_TO", "USER_CONFIRMED"),
    (0, 4, "DEPENDS_ON", "USER_CONFIRMED"),
    (0, 6, "TESTS", "USER_CONFIRMED"),
    (1, 7, "TESTS", "USER_CONFIRMED"),
    (2, 10, "PART_OF", "USER_CONFIRMED"),
    (3, 0, "SUPERSEDES", "USER_CONFIRMED"),
    (5, 11, "INSPIRED_BY", "USER_CONFIRMED"),
    (11, 12, "LED_TO", "USER_CONFIRMED"),
    (12, 13, "SUPPORTS", "USER_CONFIRMED"),
    (14, 11, "SUPPORTS", "USER_CONFIRMED"),
    (15, 12, "SUPPORTS", "AI_SUGGESTED"),
    (16, 17, "RELATED_TO", "USER_CONFIRMED"),
    (16, 27, "SUPPORTS", "USER_CONFIRMED"),
    (18, 17, "SUPPORTS", "USER_CONFIRMED"),
    (8, 0, "IMPLEMENTED_BY", "AI_SUGGESTED"),
    (9, 1, "RELATED_TO", "AI_SUGGESTED"),
    (19, 3, "REVIVES", "USER_CONFIRMED"),
    (20, 13, "SUPPORTS", "USER_CONFIRMED"),
    (21, 11, "PART_OF", "USER_CONFIRMED"),
    (22, 13, "SUPPORTS", "AI_SUGGESTED"),
    (24, 4, "CONTRADICTS", "AI_SUGGESTED"),
    (25, 0, "RELATED_TO", "USER_CONFIRMED"),
    (26, 20, "SUPPORTS", "USER_CONFIRMED"),
    (10, 11, "RELATED_TO", "USER_CONFIRMED"),
]

#: Indices that go to the Graveyard after creation.
GRAVEYARD = {3}
#: Indices that get edited, so the revision history is real.
EDITS = {
    0: "Enemies should remember how the player fights and adapt after three encounters. "
       "If the player keeps using the same combo, the enemy should start countering it. "
       "Counter once, then telegraph — never punish on the first repetition.",
    12: "Deterministic ranking matters more than clever ranking. Given the same notes and the "
        "same fragment, I want the same order every time, I want to be able to ask why, and I "
        "want the tie-break to be explicit rather than accidental.",
}
#: Indices promoted to a different type after creation.
TYPE_CHANGES = {10: "PROJECT", 6: "EXPERIMENT"}

# --------------------------------------------------------------------------
# Deterministic filler for scale benchmarks
# --------------------------------------------------------------------------
SUBJECTS = [
    "pathfinding", "lighting", "audio occlusion", "save compression", "input remapping",
    "loot tables", "dialogue branching", "animation blending", "networking", "profiling",
    "memory pooling", "shader caching", "localisation", "accessibility", "telemetry",
    "controller support", "texture streaming", "physics tunnelling", "quest state", "tutorial pacing",
]
VERBS = [
    "needs a bounded fallback", "should degrade gracefully", "must stay deterministic",
    "is cheaper than it looks", "breaks under repetition", "deserves a diagnostic view",
    "should never block the main loop", "is a caching problem in disguise",
    "needs an explicit budget", "should be measurable before it is optimised",
]
QUALIFIERS = [
    "under load", "on low-end hardware", "after a long session", "when the vault grows",
    "with partial data", "across a restart", "while offline", "for a first-time player",
    "in the worst case", "with a cold cache",
]


def filler_text(seed_index: int) -> str:
    """Deterministic pseudo-note.  Same index → same text, in any run, on any machine."""
    digest = hashlib.sha256(f"iwtt-sample-filler-{seed_index}".encode("utf-8")).hexdigest()
    pick = random.Random(int(digest[:8], 16))
    subject = pick.choice(SUBJECTS)
    verb = pick.choice(VERBS)
    qualifier = pick.choice(QUALIFIERS)
    second = pick.choice(SUBJECTS)
    return (
        f"Note {seed_index}: {subject} {verb} {qualifier}. "
        f"Worth comparing against {second} before committing to a design. "
        f"Keep the reasoning short enough to re-read in six months."
    )


def build(data_dir: Path, *, fill: int = 0, seed_tags: bool = True) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    vault = Vault(data_dir / "thought-vault.sqlite3")
    vault.initialize()
    if vault.stats()["totalThoughts"]:
        raise SystemExit(
            f"{data_dir / 'thought-vault.sqlite3'} already contains thoughts. "
            "Choose an empty directory, or delete the derived/canonical data first. "
            "This script refuses to append to an existing vault so a sample stays reproducible."
        )

    created: list[str] = []
    for index, (text, thought_type, title, tags) in enumerate(CORPUS):
        thought = vault.create_thought(text, thought_type, title, tags if seed_tags else [])
        created.append(thought["id"])
        if index in EDITS:
            vault.update_thought(thought["id"], current_text=EDITS[index])
        if index in TYPE_CHANGES:
            vault.update_thought(thought["id"], thought_type=TYPE_CHANGES[index])
        if index in GRAVEYARD:
            vault.move_to_graveyard(thought["id"])

    for source, target, rel_type, provenance in LINKS:
        vault.create_relationship(created[source], created[target], rel_type, provenance=provenance)

    filler_created = 0
    for offset in range(fill):
        thought = vault.create_thought(
            filler_text(offset),
            "THOUGHT",
            "",
            [SUBJECTS[offset % len(SUBJECTS)]] if seed_tags else [],
        )
        # A sparse deterministic web of edges, so graph expansion and clustering
        # have something real to walk at scale without becoming a hairball.
        if offset and offset % 7 == 0:
            vault.create_relationship(
                thought["id"], created[offset % len(created)], "RELATED_TO",
                provenance="USER_CONFIRMED" if offset % 14 else "AI_SUGGESTED",
            )
        filler_created += 1

    stats = vault.stats()
    report = {
        "dataDir": str(data_dir),
        "database": str(vault.db_path),
        "handAuthoredThoughts": len(created),
        "fillerThoughts": filler_created,
        "relationships": stats["relationships"],
        "suggestedRelationships": stats["suggestedRelationships"],
        "graveyardThoughts": stats["graveyardThoughts"],
        "tags": stats["tags"],
        "revisions": stats["revisions"],
        "events": stats["events"],
        "totalThoughts": stats["totalThoughts"],
        "canonicalFingerprint": vault.canonical_fingerprint(),
        "derivedNote": (
            "No derived data was seeded. The recall indexes build themselves on first use."
        ),
        "demoDataUntouched": "Vault.seed_demo() was never called by this script.",
        "tryTheseQueries": [
            "enemies should remember how the player fights",
            "enemy adaptation",
            "enemys adapt to plyer",
            "something about difficulty feeling unfair",
            "procedural dungeon seeds",
            "abandoned turn based combat",
            "game",
            "quantum chromodynamics",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="Directory for the sample vault (must not already contain thoughts)")
    parser.add_argument("--fill", type=int, default=0, help="Additional deterministic filler thoughts for scale benchmarks")
    parser.add_argument("--no-tags", action="store_true", help="Create thoughts without tags")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON only")
    args = parser.parse_args()

    report = build(Path(args.data_dir).expanduser().resolve(), fill=max(0, args.fill), seed_tags=not args.no_tags)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"Sample vault ready at {report['dataDir']}")
    print(f"  hand-authored thoughts : {report['handAuthoredThoughts']}")
    print(f"  filler thoughts        : {report['fillerThoughts']}")
    print(f"  relationships          : {report['relationships']} "
          f"({report['suggestedRelationships']} still unconfirmed AI suggestions)")
    print(f"  graveyard / tags / revisions / events : "
          f"{report['graveyardThoughts']} / {report['tags']} / {report['revisions']} / {report['events']}")
    print(f"  canonical fingerprint  : {report['canonicalFingerprint']}")
    print("\nRun it with:")
    print(f"  python3 -m iwtt.server --data-dir {report['dataDir']}")
    print("\nTry these queries in Find My Thought:")
    for query in report["tryTheseQueries"]:
        print(f"  · {query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
