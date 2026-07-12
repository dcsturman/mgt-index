"""Stage 2: candidate index-entry generation.

Feeds windows of consecutive chunks to a model and collects candidate index entries.
The model NEVER emits a page number -- it emits terms tagged with a chunk id, and the
page is joined back on from Stage 1 metadata. That is what makes the citations
trustworthy: the worst failure mode is designed out rather than mitigated.

Results are cached on disk keyed by (chunk ids + rubric + model), so re-running after
an unrelated change costs nothing, and adding a book only indexes that book. Editing
the rubric correctly invalidates everything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from mgtindex.backends import make

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "entries"

ARM = os.environ.get("MGT_ARM", "gemini-3.5")
WINDOW = 6  # chunks per request -- gives the model local context for primary-vs-mention


def _rubric() -> tuple[str, dict[str, str]]:
    """rubric.md -> (shared rules, {genre: section}).

    What a reader hunts for in a catalogue is not what they hunt for in a bestiary, and a
    single prompt written around the Core Rulebook under-indexes both. The shared rules are
    everything before the first '# Genre:' heading.
    """
    raw = (ROOT / "mgtindex" / "rubric.md").read_text()
    parts = re.split(r"^# Genre:[ \t]*(\w+)[ \t]*$", raw, flags=re.M)
    return parts[0].rstrip(), dict(zip(parts[1::2], (p.strip() for p in parts[2::2])))


SHARED, GENRES = _rubric()


def rubric_for(genre: str) -> str:
    """The prompt a given book is actually indexed with."""
    if genre not in GENRES:
        raise KeyError(f"unknown genre {genre!r}; rubric.md defines {sorted(GENRES)}")
    return f"{SHARED}\n\n# This book\n\n{GENRES[genre]}\n"


def render(window: list[dict]) -> str:
    return "\n\n".join(
        f"<chunk id={c['id']} page={c['page']} path=\"{' > '.join(c['path'])}\">\n{c['text']}\n</chunk>"
        for c in window
    )


def _key(window: list[dict], arm: str, rubric: str) -> str:
    """Cache key = these chunks + this model + THIS BOOK'S rubric.

    Hashing the book's effective rubric rather than the whole file is what keeps a change
    to the bestiary section from invalidating the Core Rulebook's cache -- and therefore
    from silently re-billing ~$2 the next time CRB is run for some unrelated reason. A book
    is only re-indexed when the prompt IT was indexed with actually changed.
    """
    raw = "|".join(c["id"] for c in window) + arm + hashlib.sha256(rubric.encode()).hexdigest()[:8]
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def windows_for(chunks: list[dict], lo: int = 0, hi: int = 10**6) -> list[list[dict]]:
    """Window boundaries are always computed over the WHOLE book, then filtered.

    If they were computed over a page slice instead, running pp.59-73 and then running
    the full book would produce different windows for the same chunks -- different cache
    keys, and the sample run's results thrown away. Deterministic windows mean the
    sample is genuinely a prefix of the full run.
    """
    allw = [chunks[i : i + WINDOW] for i in range(0, len(chunks), WINDOW)]
    return [w for w in allw if any(lo <= c["page"] <= hi for c in w)]


def run(windows, chunks: list[dict], genre: str, arm: str = ARM, workers: int = 6,
        budget: float | None = None):
    """budget: hard ceiling in dollars. Stop rather than overspend -- a runaway here is real
    money, and every window already written to the cache is kept, so an abort is resumable."""
    backend = make(arm)
    rubric = rubric_for(genre)
    by_id = {c["id"]: c for c in chunks}
    spend = 0.0
    lock = Lock()

    def one(w):
        nonlocal spend
        cached = CACHE / f"{_key(w, arm, rubric)}.json"
        if cached.exists():
            return json.loads(cached.read_text()), 0.0
        with lock:
            if budget is not None and spend >= budget:
                return [], 0.0
        try:
            entries, usage = backend.generate(rubric, render(w))
        except Exception as exc:
            print(f"  window failed: {exc}")
            return [], 0.0
        cost = backend.cost(usage)
        with lock:
            spend += cost
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(entries, indent=2))
        return entries, cost

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, windows))
    if budget is not None and spend >= budget:
        print(f"  !! STOPPED at the ${budget:.2f} budget; some windows were skipped")
    spend = 0.0  # recounted below from the per-window costs

    out, dropped = [], 0
    for entries, cost in results:
        spend += cost
        for e in entries:
            chunk = by_id.get(e.get("chunk_id"))
            if chunk is None:  # invented chunk id would become a mis-citation
                dropped += 1
                continue
            out.append({**e, "page": chunk["page"], "book_id": chunk["book_id"]})
    if dropped:
        print(f"  dropped {dropped} entries citing unknown chunks")
    return out, spend


if __name__ == "__main__":
    book = sys.argv[1] if len(sys.argv) > 1 else "core-rulebook"
    lo, hi = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 10**6)

    reg = tomllib.loads((ROOT / "books.toml").read_text())["book"]
    meta = next(b for b in reg if b["id"] == book)
    genre = meta["genre"]

    chunks = [json.loads(l) for l in (ROOT / "build" / f"{book}.chunks.jsonl").open()]
    windows = windows_for(chunks, lo, hi)
    hit = sum((CACHE / f"{_key(w, ARM, rubric_for(genre))}.json").exists() for w in windows)
    print(f"{ARM}: {book} [{genre}], pp.{lo}-{hi} -> {len(windows)} windows "
          f"({hit} cached, {len(windows)-hit} to generate)")

    entries, spend = run(windows, chunks, genre, budget=float(os.environ.get('MGT_BUDGET', 3.0)))
    out = ROOT / "build" / f"{book}.entries.jsonl"
    with out.open("w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")

    pages = len({e["page"] for e in entries})
    print(f"{len(entries)} entries / {pages} pages = {len(entries)/pages:.1f} per page   ${spend:.3f}")
    print(f"-> {out}")
