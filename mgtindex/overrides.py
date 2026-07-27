"""Manual, version-controlled corrections applied after the automated stages.

Some errors are not worth re-running paid stages to fix at the root -- most often a ship whose
title is in a font that book's extraction profile does not recognise, so the automated
identifier borrows a neighbouring ship's name. overrides.toml records the fix; this module
applies it. Everything keys to a stable, human-checkable anchor: book siglum + printed page
for a ship, exact canonical for an index headword. See overrides.toml for the format.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "overrides.toml"


def _load() -> dict:
    return tomllib.loads(_PATH.read_text()) if _PATH.exists() else {}


def apply_ships(recs: list[dict], siglum_of) -> tuple[list[dict], int]:
    """Patch ship records (from ships.json) in place. `siglum_of` maps a book_id -> siglum.

    Returns (records, count) with dropped ships removed. Called before ship-name hygiene and
    the fold into the vocabulary, so a corrected canonical propagates to the headword and the
    Ships tab.
    """
    ov = {(o["book"], o["page"]): o for o in _load().get("ship", [])}
    if not ov:
        return recs, 0
    out, n = [], 0
    for r in recs:
        o = ov.get((siglum_of(r["book"]), r["page"]))
        if not o:
            out.append(r)
            continue
        n += 1
        if o.get("drop"):
            continue
        for f in ("canonical", "tons", "aliases"):
            if f in o:
                r[f] = o[f]
        out.append(r)
    return out, n


def apply_vocab(vocab: dict) -> int:
    """Patch index headwords in place, matched by canonical (case-insensitive)."""
    ovs = _load().get("headword", [])
    if not ovs:
        return 0
    by_canon: dict[str, list] = {}
    for k, v in vocab.items():
        by_canon.setdefault(v["canonical"].strip().lower(), []).append((k, v))
    n = 0
    for o in ovs:
        for k, v in by_canon.get(o["match"].strip().lower(), []):
            if o.get("drop"):
                vocab.pop(k, None)
            else:
                if "rename" in o:
                    v["canonical"] = o["rename"]
                if "aliases_add" in o:
                    v["aliases"] = sorted(set(v.get("aliases", [])) | set(o["aliases_add"]))
                if "aliases_drop" in o:
                    drop = {a.strip().lower() for a in o["aliases_drop"]}
                    v["aliases"] = [a for a in v.get("aliases", []) if a.strip().lower() not in drop]
            n += 1
    return n
