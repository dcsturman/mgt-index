"""Stage 3c: ship specifications.

A full specification of a vessel -- the stat block giving hull tonnage, drives, power plant,
weapons and cost -- is a different and far more useful thing than a passing mention of the
ship's name, and it is what a reader hunts for. Mongoose prints 111 of them across the Core
Rulebook, High Guard and the two Aliens volumes.

Two jobs, and note which one the model does NOT do:

  TONNAGE is read deterministically. Every stat block starts `Hull 100 tons, Streamlined` /
  `Hull 50,000 tons, Dispersed Structure`, and no page carries two different tonnages. So the
  number is a regex, never a generation -- the same invariant that keeps page citations
  trustworthy (see generate.py). The model is not permitted to emit it.

  THE SHIP'S NAME is the judgement call, and that is all the model is asked for. Stage 2 left
  three headwords for HG p217's single vessel -- 'Leviathan merchant cruiser', 'Leviathan-class
  merchant cruiser' and 'Merchant Cruiser' -- so a reader scanning the index sees three ships
  where there is one. It also tagged `jump drive` and `bridge` as ships on CRB p223, because
  they appear in a stat block. One call per spec page settles both: what is this vessel called,
  what else is it called, and is the candidate term actually a ship at all.

Results are cached per page, so a re-run costs nothing.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from mgtindex.backends import Vertex
from mgtindex.canon import SIGLA

# NB: master.py imports apply() from here, so `load` is imported lazily inside run() --
# a module-level import would close the cycle.

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "ships"
OUT = ROOT / "build" / "ships.json"

HULL = re.compile(r"\bHull\s+([\d,]+)\s*tons?\b", re.I)
CAP = 2500          # chars of page text per call
BUDGET = 1.50       # hard ceiling; abort rather than overspend

SYSTEM = """You are indexing the SHIP SPECIFICATIONS in a Traveller rulebook.

You are given one printed page that contains a ship's specification: a stat block listing its
hull, drives, power plant, weapons and cost. You are also given the candidate index terms an
earlier pass pulled off this page, which are noisy.

Identify THE VESSEL this page specifies.

`canonical` — the name a reader would look it up under. Prefer the SPECIFIC name over the
generic class: 'Beowulf-class Free Trader', not 'Free Trader'. 'Arakoine-class Strike Cruiser',
not 'Strike Cruiser'. Use normal book capitalisation, not the page's ALL CAPS heading.

`aliases` — every other name this page gives the same vessel: the generic class ('Free Trader'),
the hull code or type designation ('Type S', 'Class: XT', 'Type A2'), and any nickname the prose
offers (a launch that is "also called a lifeboat" gets the alias 'lifeboat').

`is_ship` — false if this page turns out not to specify a vessel at all. Some pages carry a stat
block for something else (a set of cutter MODULES, a worked design example). Say so rather than
inventing a ship.

DO NOT report the tonnage, the cost, or any other number. Those are read from the page directly.
A stat block lists a jump drive, a power plant, sensors, a bridge, staterooms — those are
COMPONENTS of the ship, not ships, and not names for it. Never put one in `canonical` or
`aliases`."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_ship": {"type": "boolean"},
        "canonical": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_ship", "canonical"],
}


def spec_pages(chunks: dict) -> dict[tuple, dict]:
    """(book, page) -> {tons, chunks} for every page carrying a stat block."""
    by_page: dict[tuple, list] = collections.defaultdict(list)
    for c in chunks.values():
        by_page[(c["book_id"], c["page"])].append(c)

    out = {}
    for k, cs in by_page.items():
        tons = {int(m.group(1).replace(",", ""))
                for c in cs for m in HULL.finditer(c["text"])}
        if not tons:
            continue
        # Every real spec page carries exactly one tonnage. If one ever carries two, the
        # page is not what we think it is -- skip it rather than guess which ship gets which.
        if len(tons) > 1:
            print(f"  {SIGLA[k[0]]} {k[1]}: {sorted(tons)} -- ambiguous, skipped")
            continue
        out[k] = {"tons": tons.pop(), "chunks": sorted(cs, key=lambda c: c["bbox"][1])}
    return out


def _prompt(key, page, cands: list[str]) -> str:
    body, n = [], 0
    for c in page["chunks"]:
        t = c["text"][: max(0, CAP - n)]
        if not t:
            break
        n += len(t)
        body.append(f'[{" > ".join(c["path"][1:]) or "?"}] {t}')
    return (
        f"Book: {SIGLA[key[0]]}   Printed page: {key[1]}\n\n"
        f"Candidate terms from this page (noisy — some are components, not ships):\n"
        f"  {', '.join(cands) if cands else '(none)'}\n\n"
        f"Page text:\n" + "\n\n".join(body)
    )


def identify(backend: Vertex, key, page, cands) -> tuple[dict, float]:
    h = hashlib.sha256(
        (SYSTEM + _prompt(key, page, cands)).encode()
    ).hexdigest()[:16]
    cached = CACHE / f"{h}.json"
    if cached.exists():
        return json.loads(cached.read_text()), 0.0

    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": _prompt(key, page, cands)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "maxOutputTokens": 2000,
        },
    }
    last = None
    for attempt in range(3):
        try:
            r = requests.post(
                backend.url,
                headers={"Authorization": f"Bearer {backend.token()}",
                         "Content-Type": "application/json"},
                json=body,
                timeout=120,
            )
            r.raise_for_status()
            d = r.json()
            txt = "".join(p.get("text", "")
                          for p in d["candidates"][0]["content"]["parts"])
            out = json.loads(txt)
            u = d.get("usageMetadata", {})
            cost = backend.cost({
                "input_tokens": u.get("promptTokenCount", 0),
                "output_tokens": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
            })
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(out, indent=2))
            return out, cost
        except Exception as exc:
            last = exc
            time.sleep(2 * 2**attempt)
    print(f"  {SIGLA[key[0]]} {key[1]}: failed 3x ({last})")
    return {"is_ship": False, "canonical": "", "aliases": []}, 0.0


def run():
    from mgtindex.master import load

    vocab, entries, chunks = load()
    pages = spec_pages(chunks)
    print(f"{len(pages)} ship specification pages")

    cands: dict[tuple, list[str]] = collections.defaultdict(list)
    for e in entries:
        if not e.get("parent"):
            cands[(e["book_id"], e["page"])].append(e["term"])

    backend = Vertex("gemini-3.5-flash", 1.5, 9.0)
    spend = 0.0
    ships: dict[str, dict] = {}

    def one(item):
        key, page = item
        out, cost = identify(backend, key, page, sorted(set(cands.get(key, []))))
        return key, page, out, cost

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, page, out, cost in pool.map(one, pages.items()):
            spend += cost
            if spend > BUDGET:
                raise SystemExit(f"ABORT: spend ${spend:.2f} exceeded budget ${BUDGET:.2f}")
            if not out.get("is_ship") or not out.get("canonical", "").strip():
                continue
            # tonnage comes from the page, NEVER from the model
            ships[f"{key[0]}|{key[1]}"] = {
                "book": key[0],
                "page": key[1],
                "tons": page["tons"],
                "canonical": out["canonical"].strip(),
                "aliases": [a.strip() for a in out.get("aliases", []) if a.strip()],
            }

    OUT.write_text(json.dumps(ships, indent=2))
    print(f"\n{len(ships)} vessels identified ({len(pages)-len(ships)} pages were not ships)")
    print(f"${spend:.3f} spent (budget ${BUDGET:.2f})")
    print(f"-> {OUT}")
    return ships


# 'Beowulf-class Free Trader', 'Type S Scout/Courier', 'Free Trader (Type A)' -- three ways
# Mongoose names a design. All of them are <class> of <family>, and the reader shopping for a
# ship thinks in FAMILIES ("I want a far trader"), not in class names ("I want a Hero").
CLASS_OF = (
    re.compile(r"^(?P<cls>.+?)[-\s]class\s+(?P<fam>.+)$", re.I),
    re.compile(r"^(?P<cls>Type[-\s][A-Z0-9]+)\s+(?P<fam>.+)$", re.I),
    re.compile(r"^(?P<fam>.+?)\s*\((?P<cls>Type\s*[A-Z0-9]+)\)$", re.I),
)


def _split_class(name: str):
    for rx in CLASS_OF:
        if m := rx.match(name):
            return m.group("cls").strip(), m.group("fam").strip()
    return None, name.strip()


def _fold(v: dict, into: dict):
    """Merge headword `v`'s references into `into`, keeping one sense."""
    seen = {(r["book"], r["page"]) for s in into["senses"] for r in s["refs"]}
    for s in v["senses"]:
        for r in s["refs"]:
            if (r["book"], r["page"]) not in seen:
                seen.add((r["book"], r["page"]))
                into["senses"][0]["refs"].append(dict(r))


def apply(vocab: dict, entries: list[dict]) -> int:
    """Fold the identified vessels into the vocabulary. Free -- reads build/ships.json.

    Three things happen here, and the second is the one that makes the index readable:

      1. A specified ship's headword gains its displacement: 'Beowulf-class Free Trader
         (200 tons)'. Its PRIMARY reference is forced to the stat-block page, because that
         is the page the reader wants; every other appearance is a mention.

      2. The duplicates collapse. Stage 2 left three headwords for HG p217's one vessel.
         Any headword that is merely another NAME for a specified ship, and whose own
         definition page is that ship's stat block, is folded into the ship and its refs
         carried over. A headword that also lives elsewhere in the corpus is left alone --
         'Free Trader' is a real generic concept as well as a Beowulf-class hull.

      3. Ships Stage 2 missed entirely (HG's Corsair, Destroyer, Fleet Escort ...) are
         added, since the stat block proves they exist.
    """
    from mgtindex.canon import blocking_key

    if not OUT.exists():
        return 0
    recs = list(json.loads(OUT.read_text()).values())

    # A name that several vessels answer to is not a name -- it is a category. 'small craft',
    # 'scout', 'pinnace'. And an alias that IS another ship's proper name would send the
    # reader to the wrong vessel, which is worse than no cross-reference at all.
    proper = {blocking_key(r["canonical"]) for r in recs}
    used = collections.Counter(blocking_key(a) for r in recs for a in r["aliases"])

    def clean(aliases, me: str) -> list[str]:
        return sorted({
            a.strip() for a in aliases
            if (k := blocking_key(a)) and k != me and k not in proper and used[k] <= 1
        })

    # keep the hull code ('Type A', 'Type S') before alias hygiene eats it -- two ships may
    # legitimately share one, so `used[k] <= 1` drops it, but it is the best label there is
    # for a design whose family has other, named classes.
    TYPECODE = re.compile(r"^Type[-\s]?[A-Z0-9]+$", re.I)
    for r in recs:
        r["code"] = next((a.strip() for a in r["aliases"] if TYPECODE.match(a.strip())), None)
        r["aliases"] = clean(r["aliases"], blocking_key(r["canonical"]))

    # One vessel may be specified in several books (the Light Fighter is in CRB and HG);
    # that is ONE headword with two references. But two different vessels may share a name
    # at different displacements (AL2's 200t and 700t Siyoparttwi scouts) -- and there the
    # tonnage in the headword is exactly what tells them apart.
    groups: dict[tuple, list] = collections.defaultdict(list)
    for r in recs:
        groups[(blocking_key(r["canonical"]), r["tons"])].append(r)

    added, folded = 0, {}
    for (base, tons), rs in groups.items():
        rs.sort(key=lambda r: (r["book"], r["page"]))
        head = rs[0]
        label = f"{head['canonical']} ({tons:,} tons)"
        specs = [{"book": r["book"], "page": r["page"]} for r in rs]
        # distinct key when one name covers two different vessels
        key = base if len({t for b, t in groups if b == base}) == 1 else f"{base} {tons}"

        v = vocab.get(key)
        if v is None:
            vocab[key] = {
                "canonical": label,
                "aliases": sorted({a for r in rs for a in r["aliases"]}),
                "senses": [{"qualifier": "", "primary": dict(specs[0]),
                            "refs": [dict(s) for s in specs]}],
                "ship": True,
                "code": next((r["code"] for r in rs if r.get("code")), None),
                "tons": tons,
                "spec": [dict(s) for s in specs],
            }
            added += 1
            v = vocab[key]
        else:
            v["canonical"] = label
            v["ship"] = True
            v["code"] = next((r["code"] for r in rs if r.get("code")), None)
            v["tons"] = tons
            v["spec"] = [dict(s) for s in specs]
            # the vocabulary's own aliases get the same hygiene as the model's: an alias
            # equal to the headword ('light fighter' -> 'Light Fighter') is a dead
            # cross-reference, and one equal to ANOTHER vessel's name is a wrong turn.
            v["aliases"] = clean(set(v.get("aliases", [])) | {a for r in rs for a in r["aliases"]},
                                 base)
            # the stat block is the page the reader wants -- make it the primary
            s = v["senses"][0]
            s["primary"] = dict(specs[0])
            have = {(r["book"], r["page"]) for r in s["refs"]}
            s["refs"] += [dict(x) for x in specs if (x["book"], x["page"]) not in have]

        # fold in the aliases that Stage 2 left as headwords of their own
        for a in list(v["aliases"]):
            ak = blocking_key(a)
            other = vocab.get(ak)
            if other is None or ak == key or other.get("ship"):
                continue
            # only if this really is the same thing: its definition page IS the stat block
            if all((s["primary"]["book"], s["primary"]["page"]) in
                   {(x["book"], x["page"]) for x in specs} for s in other["senses"]):
                _fold(other, v)
                v["aliases"] = sorted(set(v["aliases"]) | {other["canonical"]})
                del vocab[ak]
                folded[ak] = v["canonical"]

    # A folded headword takes its subentries with it. 'Merchant Cruiser' no longer exists,
    # so anything Stage 2 filed under it would be dropped on the floor by subents.attach --
    # repoint those at the vessel they actually belong to.
    if folded:
        for e in entries:
            p = (e.get("parent") or "").strip()
            if p and (nk := folded.get(blocking_key(p))):
                e["parent"] = nk

    for v in vocab.values():
        if v.get("ship"):
            v["senses"][0]["refs"].sort(key=lambda r: (r["book"], r["page"]))

    added += _families(vocab)
    return added


def _families(vocab: dict) -> int:
    """Gather 'X-class Y' designs under a headword for Y, with the classes as subentries.

    A reader shopping for a ship thinks 'I want a far trader', not 'I want a Hero'. Scattered
    across the alphabet, the three dreadnoughts are unfindable unless you already know they
    are called Kokirrak, Plankwell and Tigress -- which is precisely the knowledge an index
    is supposed to supply.

    The class ships keep their own headwords too. Double-posting is normal in a printed index
    and it is what lets both lookups work: by family and by class name.
    """
    from mgtindex.canon import blocking_key

    TYPECODE = re.compile(r"^Type[-\s]?[A-Z0-9]+$", re.I)

    fam: dict[str, list] = collections.defaultdict(list)
    for key, v in vocab.items():
        if not v.get("ship"):
            continue
        cls, base = _split_class(v["canonical"].split(" (")[0])
        # A design with no class name is STILL a member of its family: the Core Rulebook's
        # plain 'Free Trader' and High Guard's 'Beowulf-class Free Trader' are two 200-ton
        # free traders, and a reader wants to see both. Label it by its type code if the
        # book gives one, otherwise 'standard'.
        named = cls is not None
        if cls is None:
            cls = v.get("code") or "standard"
        fam[blocking_key(base)].append((base, cls, key, v, named))

    made = 0
    for fkey, members in fam.items():
        # A family of one is just a ship. And a "family" whose members are ALL unnamed is not
        # a family at all -- AL2's two Siyoparttwi scouts (200t and 700t) merely share a name,
        # and the 200/400-ton system defence boats are unrelated designs. Their tonnages
        # already tell them apart as separate headwords; grouping them invents a lineage.
        if len(members) < 2 or not any(m[4] for m in members):
            continue
        label = min((m[0] for m in members), key=len)

        refs, seen, subs = [], set(), []
        for base, cls, key, v, _named in sorted(members, key=lambda m: (not m[4], m[1].lower())):
            tons = v["canonical"].split("(")[-1].rstrip(") ")
            prim = v["senses"][0]["primary"]
            subs.append({
                "t": f"{cls} ({tons})",
                "refs": {(x["book"], x["page"]): (x["book"], x["page"]) == (prim["book"], prim["page"])
                         for x in v["senses"][0]["refs"]},
            })
            for x in v["senses"][0]["refs"]:
                if (x["book"], x["page"]) not in seen:
                    seen.add((x["book"], x["page"]))
                    refs.append(dict(x))

        v = vocab.get(fkey)
        if v is None:
            vocab[fkey] = v = {"canonical": label, "aliases": [],
                               "senses": [{"qualifier": "", "primary": dict(refs[0]),
                                           "refs": refs}]}
            made += 1
            si = 0
        elif v.get("ship"):
            # The family name is ALSO a bare design ('Far Trader' is both the family and a
            # 200-ton ship). The family wins the headword; the bare design is one of its
            # members, which is what it always was.
            v["canonical"] = label
            v["senses"] = [{"qualifier": "", "primary": dict(refs[0]), "refs": refs}]
            si = 0
        else:
            # The family name already exists as an ordinary concept -- 'scout' is a CAREER
            # in the Core Rulebook. Give the designs their own sense rather than trample it.
            v["senses"].append({"qualifier": "spacecraft designs",
                                "primary": dict(refs[0]), "refs": refs})
            si = len(v["senses"]) - 1
        v["ship_members"] = (v.get("ship_members") or []) + [(si, subs)]
    return made


if __name__ == "__main__":
    run()
