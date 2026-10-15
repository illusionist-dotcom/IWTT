# LICENSE_AUDIT.md

**Audit 11 — License.** A repository license audit of Graphify, and the
obligations that would attach to four different reuse postures for IWTT.

> **This is not legal advice.** It is a factual audit of files in the repository
> plus a plain-language description of what the licenses say. Anything marked
> **[LEGAL REVIEW]** should be confirmed by qualified counsel before it is relied
> upon.

Verified against commit `10130e79ed9caf8f60d998073e097531eaa1a395`.

---

## 1. Verified license facts

| Item | Finding | Evidence |
|---|---|---|
| Main license | **Apache License 2.0** — verified verbatim | `LICENSE` opens "Apache License / Version 2.0, January 2004" |
| Secondary license | **MIT**, retained for pre-relicensing contributions | `LICENSE-MIT`: "MIT License / Copyright (c) 2026 Safi Shamsi" |
| NOTICE file | **Present**, includes the relicensing statement | `NOTICE` |
| Package metadata | `license = "Apache-2.0"`; `license-files = ["LICENSE", "LICENSE-MIT", "NOTICE"]` | `pyproject.toml` |
| Copyright holder | "Safi Shamsi and the Graphify contributors" | `NOTICE` |
| Version / date | 0.9.91, 2026-10-14 | `pyproject.toml`, git tag `v0.9.91` |
| Submodules | **None** (no `.gitmodules`) | `git submodule status` |
| Vendored third-party trees | **None found** | package-wide grep for vendoring markers |
| File-level license headers | **None** — 0 of 95 `graphify/*.py` files carry an SPDX tag or copyright header | grep over `graphify/` |
| Generated/derived components | None in the license sense; skill bodies are generated from templates by `tools/skillgen/gen.py` but are project-authored | `tools/skillgen/` |

### The NOTICE text (full)

> Graphify
> Copyright 2026 Safi Shamsi and the Graphify contributors.
>
> This product is licensed under the Apache License, Version 2.0 (see LICENSE).
>
> Portions of this software were contributed under the MIT License prior to the
> relicensing and remain available under those terms. The original MIT license
> text is retained in LICENSE-MIT.

**Practical reading:** the project moved from MIT to Apache-2.0 and kept the MIT
text for earlier contributions. A downstream user taking code from a pre-relicensing
commit may be dealing with MIT-only terms; code taken at this commit is Apache-2.0
for the project's own contributions. **[LEGAL REVIEW]** if IWTT ever copies a
specific file, confirm that file's provenance under the relicensing.

### One nuance worth flagging

`graphify/_minhash.py` describes itself as a **"datasketch-compatible drop-in (no
scipy)"** whose "Hash family (Mersenne-prime permutations) and LSH band structure
are equivalent to datasketch so dedup quality is unchanged." `datasketch` is
MIT-licensed. The module is written as an independent implementation exposing the
same small API surface (MinHash set-sketching and band-LSH are public-domain
algorithmic literature), and no datasketch source text appears in it. **This looks
like an independent reimplementation, which carries no attribution obligation** —
but "equivalent to" is the kind of phrase that invites a provenance question.
**[LEGAL REVIEW]** if IWTT copies that file specifically; the safe alternative is
to depend on `datasketch` directly or reimplement from the literature.

## 2. Dependency licenses (the real practical exposure)

Graphify's runtime dependencies, with licenses read from package metadata:

**Core (always installed):**

| Package | License |
|---|---|
| `networkx` | BSD-3-Clause |
| `numpy` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `rapidfuzz` | MIT |
| `tree-sitter` + ~28 grammar packages | MIT |
| `tomli` (py<3.11) | MIT |

**Optional extras:**

| Extra | Packages | License notes |
|---|---|---|
| `mcp` | `mcp` MIT, `starlette` BSD-3-Clause | permissive |
| `leiden` | `graspologic` / `graspologic-native` | MIT |
| `neo4j` / `falkordb` | Apache-2.0 AND Python-2.0 / MIT | permissive |
| `pdf` | `pypdf` BSD-3-Clause, `markdownify` MIT | permissive |
| `watch` | `watchdog` Apache-2.0 | permissive |
| `svg` | `matplotlib` (PSF-based), `pillow` MIT-CMU | permissive |
| `office` / `google` | `python-docx` MIT, `openpyxl` MIT | permissive |
| `video` | `faster-whisper` MIT, `yt-dlp` Unlicense | permissive |
| LLM clients | `anthropic` MIT, `openai` Apache-2.0, `boto3` Apache-2.0, `tiktoken` MIT | permissive |
| `postgres` | **`psycopg[binary]` — LGPL-3.0-only** | **copyleft — see below** |
| `robot` | `robotframework` Apache-2.0 | permissive |
| `chinese` | `jieba` MIT | permissive |

**Dev-only (not shipped):** `hypothesis` **MPL-2.0** (file-level copyleft),
`bandit` Apache-2.0, `pip-audit`, `nuitka`, `pyright`, `ruff`, `pytest`,
`pre-commit`.

**The only copyleft in a shipped configuration is `psycopg` / `psycopg-binary`
(LGPL-3.0-only), and only via the optional `postgres` extra.** Using it as a
library dependency is the normal LGPL case (dynamic linking, no obligation on
IWTT's own code); relinking/replacement obligations arise if a *modified* psycopg
is redistributed, or if a static bundle prevents replacement.

**Transitive exposure is larger than the direct list.** `uv.lock` contains **210
package blocks**; a full transitive audit was not performed here
**[LEGAL REVIEW]** before any redistribution, especially for a bundled desktop app.

## 3. Obligations by reuse posture

### A. Merely studying an architectural idea

**Obligations: none.**

Copyright protects expression, not ideas, algorithms, or architectures. Reading
Graphify and designing an IWTT subsystem that uses a similar approach — a tiered
lexical scorer, hub-suppressed BFS, corroboration-gated promotion, sidecar-based
derived layers — creates no license obligations, provided no source text is copied.

Practical caution: an *independent implementation* is safest when written from an
understanding of the behaviour and the algorithms, not by transcribing
Graphify's code with renames. Retaining this audit's `SOURCE_EVIDENCE.md` helps
document that the work was design-level.

### B. Independently implementing a similar technique

**Obligations: none from Graphify** (again, ideas are not copyrightable), with two
caveats:

1. If the technique's *implementation* is copied in substance — same structure,
   same sequence, same comments paraphrased — that is a derivative work regardless
   of license posture. Apache-2.0 permits derivative works, but then obligations
   from case C/D apply.
2. IWTT's own licensing should be settled first: if IWTT is itself licensed
   Apache-2.0 or MIT, independent reimplementation is uncomplicated. If IWTT is to
   remain unlicensed/proprietary, independent implementation with documentary
   separation (this audit being part of that record) is the correct posture.

Arguably the highest-value items in the matrix — the scoring model, seed
guarantees, hub suppression, the provenance vocabulary, the canonical/derived
split — are all in category B: **studied and reimplemented, not copied.**

### C. Copying or modifying Apache-2.0 source code from Graphify into IWTT

Apache-2.0 §4 conditions for redistribution of modified source:

| Obligation | What it means for IWTT |
|---|---|
| Include a copy of the License | Ship/keep Graphify's `LICENSE` (Apache-2.0 text) with IWTT |
| State significant changes | Mark modified files with a notice of change — the idiomatic practice is a file header: "This file is derived from Graphify (commit SHA, path); modified by IWTT on DATE: <what changed>" |
| Retain notices | Keep copyright, patent, trademark and attribution notices from the source. Graphify has **no per-file headers**, so the effective notices are `LICENSE` + `NOTICE` |
| Include `NOTICE` if present | Graphify **has** a NOTICE file. If IWTT distributes a work containing Graphify code, the NOTICE content (the "Graphify / Copyright 2026 Safi Shamsi and the Graphify contributors" attribution) must be reproduced in IWTT's own NOTICE or equivalent documentation |
| Attribution in the UI, if applicable | Apache-2.0 §4(d): if the source distribution has a NOTICE, the derived distribution must display those attributions "in at least one of the following places: within a NOTICE text file …; within the source form or documentation …; or within a display generated by the Derivative Works, if and wherever such third-party notices normally appear" |
| Trademark | Apache-2.0 §6 grants **no** trademark rights. IWTT must not imply endorsement by, or affiliation with, Graphify |

Note the clause titled *"You may add Your own copyright statement to Your
modifications"* — IWTT can and should assert its own copyright on its changes to
copied files.

### D. Redistributing modified Graphify components

Same as C, plus:

| Obligation | Detail |
|---|---|
| Preserve the MIT option for pre-relicensing portions | If the copied file's code originated before the relicensing, `LICENSE-MIT` terms may apply to that portion — retain both license texts **[LEGAL REVIEW]** |
| Dependency obligations travel | Redistributing Graphify's code plus resolving its declared dependencies pulls in the dependency licenses above (LGPL-3.0 for `psycopg` if the `postgres` extra is bundled) |
| Bundle/packaging | If IWTT ships as a frozen binary (Graphify itself supports a `nuitka` build), the LGPL replacement requirement becomes materially harder to satisfy — dynamic linking or excluding the extra is the safer route |

## 4. Patent implications (Apache-2.0 §3)

Apache-2.0 grants contributors' patent claims **needed to use the licensed work**,
implicitly (no separate patent license required), and terminates that grant for
anyone who initiates patent litigation asserting the work infringes. This is the
principal advantage of Apache-2.0 over MIT for a user, and the reason to prefer
taking code under Apache-2.0 where both are available.

Nothing in the repository suggests patent-protected subject matter, and no
patent-assertion notices were found. **[LEGAL REVIEW]** if IWTT's use is
commercial and patent exposure matters.

## 5. Attribution requirements — the concrete checklist

If IWTT ever copies Graphify code (posture C/D), the minimum work is:

1. Keep a copy of the Apache-2.0 `LICENSE`.
2. Keep `LICENSE-MIT` if any pre-relicensing portions are involved.
3. Add a NOTICE (or extend an existing one) reproducing:
   `Graphify — Copyright 2026 Safi Shamsi and the Graphify contributors.`
4. Add a change notice per modified file (derived-from, commit SHA, date, summary).
5. Do not use the Graphify name/marks to imply endorsement.
6. Record the exact upstream commit SHA for every copied file.
7. If IWTT ships binaries, ensure third-party notices are displayed where such
   notices normally appear.

If IWTT only *studies* Graphify (posture A) or *independently implements* similar
techniques (posture B), none of 1–7 applies — though keeping this audit as a
record of the distinction is prudent.

## 6. Explicit flags for professional legal review

1. **[LEGAL REVIEW]** The MIT→Apache relicensing split: which portions remain
   MIT-only at the commit inspected, and whether `LICENSE-MIT` retention suffices.
2. **[LEGAL REVIEW]** `graphify/_minhash.py`'s relationship to MIT-licensed
   `datasketch` (self-described as "equivalent"), if that file is ever copied.
3. **[LEGAL REVIEW]** Full transitive dependency license sweep from `uv.lock`
   (210 packages) before any redistribution or bundled desktop build.
4. **[LEGAL REVIEW]** LGPL-3.0 implications of `psycopg`/`psycopg-binary` in any
   frozen/bundled distribution.
5. **[LEGAL REVIEW]** IWTT's own license posture and whether copied Apache-2.0
   code could be absorbed into it (Apache-2.0 code cannot be relicensed more
   restrictively, only combined with compatible terms).
6. **[LEGAL REVIEW]** Whether IWTT's planned distribution model (local app, source
   available or not) triggers any §4(d) UI-attribution requirement.

## 7. Bottom line

- **The repository's own licensing is clean and standard: Apache-2.0 with a
  retained MIT file and a NOTICE.** No submodules, no vendored trees, no
  file-level headers to reconcile.
- **The safest and highest-value strategy is posture B** — study the architecture
  and reimplement the small transferable pieces (scoring, seed selection, hub
  suppression, provenance vocabulary, sidecar separation, corroboration/decay)
  in IWTT's own idiom. That is also what IWTT needs anyway, because Graphify's
  implementations are calibrated for code graphs.
- **If code is copied**, the compliance work is small but non-optional: license
  text, NOTICE reproduction, per-file change notices, and no implied endorsement.
- **No claim in this file should be treated as legal advice.** Every
  **[LEGAL REVIEW]** item above should be confirmed by counsel before IWTT ships
  anything derived from Graphify source.

## 8. Evidence anchors

`LICENSE` (Apache-2.0 text) · `LICENSE-MIT` · `NOTICE` · `pyproject.toml`
(`license`, `license-files`, dependency and extra declarations) · `uv.lock`
(210 package blocks) · `git submodule status` + absence of `.gitmodules` ·
package-wide grep for SPDX/Copyright headers (0 in `graphify/*.py`) ·
`graphify/_minhash.py` docstring ("datasketch-compatible drop-in", "equivalent to
datasketch") · PyPI metadata for each dependency listed in §2 ·
`graphify/install.py` (bundling/packaging surface).
