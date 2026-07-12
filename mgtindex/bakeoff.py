"""Run the same chapter through several models and compare what they index.

Usage:  python -m mgtindex.bakeoff gemini-pro gemini-flash opus
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mgtindex.backends import make
from mgtindex.generate import rubric_for

ROOT = Path(__file__).resolve().parent.parent
# The bake-off reads the Core Rulebook's Skills chapter, so it must be prompted as the
# pipeline would prompt it -- shared rules plus the 'rules' genre section, not the whole
# rubric.md with all four genres' guidance mashed together.
RUBRIC = rubric_for("rules")
WINDOW = 6
LO, HI = 59, 73  # Skills and Tasks


def render(window: list[dict]) -> str:
    return "\n\n".join(
        f"<chunk id={c['id']} page={c['page']} path=\"{' > '.join(c['path'])}\">\n{c['text']}\n</chunk>"
        for c in window
    )


def run_arm(arm: str, chunks: list[dict]) -> dict:
    backend = make(arm)
    windows = [chunks[i : i + WINDOW] for i in range(0, len(chunks), WINDOW)]
    by_id = {c["id"]: c for c in chunks}
    t0 = time.time()

    def one(w):
        try:
            return backend.generate(RUBRIC, render(w))
        except Exception as exc:
            print(f"  [{arm}] window failed: {exc}")
            return [], {"input_tokens": 0, "output_tokens": 0}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, windows))

    entries, usage = [], {"input_tokens": 0, "output_tokens": 0}
    bad_ids = 0
    for es, u in results:
        usage["input_tokens"] += u["input_tokens"]
        usage["output_tokens"] += u["output_tokens"]
        for e in es:
            chunk = by_id.get(e.get("chunk_id"))
            if chunk is None:  # invented chunk id -> would be a mis-citation. drop.
                bad_ids += 1
                continue
            entries.append({**e, "page": chunk["page"], "book_id": chunk["book_id"]})

    pages = len({c["page"] for c in chunks})
    out = {
        "arm": arm,
        "model": backend.name,
        "entries": entries,
        "n": len(entries),
        "per_page": len(entries) / pages,
        "primary": sum(1 for e in entries if e.get("role") == "primary"),
        "subentries": sum(1 for e in entries if e.get("parent")),
        "bad_chunk_ids": bad_ids,
        "cost": backend.cost(usage),
        "usage": usage,
        "seconds": time.time() - t0,
    }
    (ROOT / "build" / "bakeoff").mkdir(parents=True, exist_ok=True)
    (ROOT / "build" / "bakeoff" / f"{arm}.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    arms = sys.argv[1:] or ["gemini-pro", "gemini-3.5", "gemini-flash"]
    chunks = [json.loads(l) for l in (ROOT / "build" / "core-rulebook.chunks.jsonl").open()]
    chunks = [c for c in chunks if LO <= c["page"] <= HI]
    pages = len({c["page"] for c in chunks})
    print(f"Skills and Tasks: pp.{LO}-{HI}, {len(chunks)} chunks over {pages} pages\n")

    results = [run_arm(a, chunks) for a in arms]

    print(f"\n{'arm':<14}{'entries':>8}{'/page':>7}{'prim':>6}{'sub':>5}{'badid':>7}{'cost':>8}{'sec':>7}")
    for r in results:
        print(
            f"{r['arm']:<14}{r['n']:>8}{r['per_page']:>7.1f}{r['primary']:>6}"
            f"{r['subentries']:>5}{r['bad_chunk_ids']:>7}${r['cost']:>7.3f}{r['seconds']:>7.0f}"
        )

    # Where do they agree? Overlap on normalised top-level term.
    def terms(r):
        return {(e.get("parent", "") + "|" + e["term"]).lower().strip() for e in r["entries"]}

    print("\nterm overlap (Jaccard):")
    for i, a in enumerate(results):
        for b in results[i + 1 :]:
            ta, tb = terms(a), terms(b)
            j = len(ta & tb) / max(1, len(ta | tb))
            print(f"  {a['arm']:>12} vs {b['arm']:<12} {j:.2f}   shared={len(ta & tb)}")

    # full-corpus projection: this chapter is 15 of 1772 pages
    print(f"\nprojected full-corpus cost (x{1772/pages:.0f}):")
    for r in results:
        print(f"  {r['arm']:<14} ${r['cost'] * 1772 / pages:>7.2f}  (batch API: ${r['cost'] * 1772 / pages / 2:.2f})")
