"""Stage 4c: the HTML master index.

Why HTML beats the PDF here: in a PDF the target file is baked into the link annotation
at build time. In HTML the URL is constructed at CLICK time, so the reader can point each
book wherever it actually lives -- a different folder, an external drive, a NAS, an HTTP
host -- via a settings panel, without regenerating anything.

Links use PDF open parameters:  <url>#page=N&view=FitH,<top>
  * page=N   -- 1-based PDF page (printed page + the book's offset + 1)
  * view=FitH,<top> -- scrolls the paragraph to the top of the window. `top` is in PDF
    user space, which is measured from the BOTTOM of the page, so it is
    (page_height - chunk_y). Viewers that ignore `view` still honour `page`.

Settings persist in localStorage, so they survive reloads and are per-reader -- which is
the whole point: someone else who installs this has different paths to their books.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import fitz

from mgtindex.canon import blocking_key
from mgtindex.master import BOOKS, MONGOOSE_NOTICE, ORDER, _REG, load
from mgtindex.subents import attach, promote_orphans
from mgtindex.ships import apply as apply_ships

ROOT = Path(__file__).resolve().parent.parent


def sort_key(t: str) -> str:
    """Index sort ignores leading punctuation.

    "'trator" is a real Traveller creature, not junk -- the apostrophe is part of the
    name. Sorting on the raw first character files it under '#', where nobody will look.
    A reader looks for it under T.
    """
    s = t.lstrip("'\u2019\u2018\"/([-\u2014\u2013 .").lower()
    return s or t.lower()


def bucket(t: str) -> str:
    """The A-Z heading a term belongs under."""
    s = sort_key(t)
    c = s[:1].upper()
    return c if c.isalpha() else "#"


def build():
    vocab, entries, chunks = load()
    books = sorted(_REG, key=lambda b: b["order"])
    bidx = {b["id"]: i for i, b in enumerate(books)}

    # page heights: a PDF "top" coordinate is bottom-up, and the books are not all the
    # same height (Aliens vol 1 is 816pt, CSC 780, Robot Handbook 776)
    heights: dict[tuple[str, int], float] = {}
    for b in books:
        d = fitz.open(ROOT / b["file"])
        for i in range(d.page_count):
            heights[(b["id"], i)] = d[i].rect.height
        d.close()

    target: dict[tuple, float] = {}
    for e in entries:
        c = chunks.get(e["chunk_id"])
        if c:
            target.setdefault(
                (blocking_key(e.get("parent") or e["term"]), e["book_id"], e["page"]), c["bbox"][1]
            )

    # Headwords synthesised after Stage 2 -- ship families, promoted parents -- have no chunk
    # of their own to anchor to. Fall back to the top of the text on that page rather than to
    # a hardcoded y, so the link still lands somewhere sensible.
    page_top: dict[tuple, float] = {}
    for c in chunks.values():
        k = (c["book_id"], c["page"])
        page_top[k] = min(page_top.get(k, 1e9), c["bbox"][1])

    def ref(bid: str, page: int, key: str, primary: bool):
        """-> [bookIndex, printedPage, pdfPage, fitHTop, isPrimary]"""
        pdf_idx = page + BOOKS[bid]["offset"]
        h = heights.get((bid, pdf_idx), 792.0)
        y = target.get((key, bid, page)) or page_top.get((bid, page), 60.0)
        top = round(max(0.0, h - max(0.0, y - 14)))
        return [bidx[bid], page, pdf_idx + 1, top, 1 if primary else 0]

    # Subentries belong to a SENSE, not to a headword. 'Agent' is a career (whose subentries
    # are Corporate, Intelligence, Law Enforcement) and a disease vector (biological agents,
    # delivery method, exposure); printing those in one alphabetical pile under 'Agent' throws
    # away the split Stage 3 worked to find. promote_orphans first, so that a parent the model
    # named but never emitted still gets a headword instead of having its children discarded.
    promoted = promote_orphans(vocab, entries)
    # ships BEFORE subentries: folding 'Merchant Cruiser' into 'Leviathan-class Merchant
    # Cruiser' moves that headword's subentries too, and attach() must see the result.
    added_ships = apply_ships(vocab, entries)
    subs = attach(vocab, entries, chunks)

    # A ship family's members ARE its subentries -- 'dreadnought' with Kokirrak, Plankwell and
    # Tigress indented beneath. They are synthesised, not extracted, so attach() never saw them.
    for key, v in vocab.items():
        if not v.get("ship_members"):
            continue
        lst = subs.setdefault(key, [[] for _ in v["senses"]])
        lst += [[] for _ in range(len(v["senses"]) - len(lst))]
        for si, members in v["ship_members"]:
            lst[si] = members + lst[si]

    def sort_refs(rs):
        return sorted(rs, key=lambda r: (r[0], r[1]))

    records = []
    for key, v in vocab.items():
        mine = subs.get(key) or [[] for _ in v["senses"]]
        senses = []
        for i, s in enumerate(v["senses"]):
            pairs = {(s["primary"]["book"], s["primary"]["page"]): True}
            for r in s.get("refs", []):
                pairs.setdefault((r["book"], r["page"]), False)
            senses.append({
                "q": s["qualifier"],
                "r": sort_refs([ref(b, p, key, pr) for (b, p), pr in pairs.items()]),
                "sub": [
                    {"t": x["t"],
                     "r": sort_refs([ref(b, p, key, pr) for (b, p), pr in x["refs"].items()])}
                    for x in mine[i]
                ],
            })
        records.append({"k": "e", "id": key, "t": v["canonical"], "a": v.get("aliases", []),
                        "s": senses})

    headwords = {v["canonical"].strip().lower() for v in vocab.values()}
    headkeys, emitted = set(vocab.keys()), set()
    for key, v in vocab.items():
        for a in v.get("aliases", []):
            a, al = a.strip(), a.strip().lower()
            if not a or al in headwords or al in emitted:
                continue
            if blocking_key(a) in headkeys and blocking_key(a) != key:
                continue
            if blocking_key(a) == blocking_key(v["canonical"]):
                continue  # differs only by punctuation/case/plural -- "/bis computer"
            emitted.add(al)
            # carry the TARGET'S ID, not just its text -- a cross-reference has to resolve
            # to an anchor, and two clusters can canonicalise to the same display string
            records.append({"k": "s", "t": a, "to": v["canonical"], "toId": key})

    for r in records:
        r["L"] = bucket(r["t"])
    records.sort(key=lambda r: (sort_key(r["t"]), r["t"]))

    # Every design in one place. The A-Z answers "where is the Tigress?"; this answers the
    # question a referee actually asks -- "what have I got at around 400 tons?" -- which no
    # alphabetical index can. Only SPEC pages appear here; a mention is not a design.
    fleet = sorted(
        (
            {"n": v["canonical"].rsplit(" (", 1)[0], "t": v["tons"],
             "r": sort_refs([ref(s["book"], s["page"], key, True) for s in v["spec"]])}
            for key, v in vocab.items() if v.get("ship") and v.get("spec")
        ),
        key=lambda s: (s["t"], s["n"].lower()),
    )

    data = {
        "books": [
            {"sig": b["siglum"], "title": b["title"], "file": b["file"],
             "pages": b["pages"], "sha": b["sha256"]}
            for b in books
        ],
        "ships": fleet,
        "records": records,
        "notice": MONGOOSE_NOTICE,
        "stats": {
            "entries": len(entries),
            "headwords": len(vocab),
            "senses": sum(len(v["senses"]) for v in vocab.values()),
            "refs": sum(len(s["r"]) + sum(len(x["r"]) for x in s["sub"])
                        for r in records if r["k"] == "e" for s in r["s"]),
        },
    }

    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    out = ROOT / "MGT2 Master Index.html"
    out.write_text(html, encoding="utf-8")
    print(f"{len(records)} records, {data['stats']['refs']} links")
    print(f"-> {out}  ({out.stat().st_size/1e6:.1f} MB)")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mongoose Traveller 2e — Master Index</title>
<style>
  :root{
    --bg:#faf9f7; --fg:#16151a; --dim:#6b6a72; --line:#e2e0dc;
    --accent:#8a3324; --card:#fff; --hit:#fdf3d0; --flash:#cfe2ff;
  }
  @media (prefers-color-scheme:dark){
    :root{ --bg:#131316; --fg:#e9e8e6; --dim:#95949c; --line:#2c2c31;
           --accent:#e08a6e; --card:#1a1a1e; --hit:#3a3320; --flash:#1e3a5f; }
  }
  /* --hit and --flash are deliberately different colours doing different jobs. --hit marks
     search matches and link hovers -- there can be dozens on screen at once, so it must stay
     quiet, and a pale wash of the page colour is right. --flash fires ONCE, on the entry you
     just jumped to, and must be found by an eye that has been dragged across a smooth scroll.
     Sharing one variable made the flash as quiet as a search mark (1.06:1 against the page --
     the colour of the paper). Blue because nothing else in this index is blue: the eye locates
     it without searching. */
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:15px/1.5 ui-serif,Georgia,"Iowan Old Style",serif;}
  header{position:sticky;top:0;z-index:20;background:var(--bg);
         border-bottom:1px solid var(--line);padding:14px 20px 10px;}
  h1{margin:0 0 2px;font-size:19px;letter-spacing:.01em}
  .sub{color:var(--dim);font-size:12.5px}
  .titlerow{display:flex;align-items:flex-start;justify-content:space-between;
            gap:12px;margin-bottom:10px}
  .icons{display:flex;gap:6px;flex-shrink:0}
  button.icon{width:34px;height:34px;padding:0;font-size:16px;line-height:1;
              display:flex;align-items:center;justify-content:center;border-radius:8px}
  .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  input[type=search],input[type=text]{
    font:inherit;font-size:14px;padding:7px 10px;border:1px solid var(--line);
    border-radius:7px;background:var(--card);color:var(--fg);}
  input[type=search]{flex:1;min-width:220px}
  button{font:inherit;font-size:13px;padding:7px 12px;border:1px solid var(--line);
         border-radius:7px;background:var(--card);color:var(--fg);cursor:pointer}
  button:hover{border-color:var(--accent)}
  button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  .count{color:var(--dim);font-size:12.5px;white-space:nowrap}

  #alpha{display:flex;flex-wrap:wrap;gap:1px;margin-top:8px}
  #alpha a{font:600 11px/1 ui-sans-serif,system-ui;color:var(--dim);
           padding:4px 5px;text-decoration:none;border-radius:4px}
  #alpha a:hover{background:var(--card);color:var(--accent)}

  main{padding:16px 20px 60px;max-width:1500px;margin:0 auto}
  /* Columns must be scoped to a LETTER SECTION, not the whole list. Applied to the
     whole list, the browser balances all 4,800 entries into two columns -- so the left
     column runs A-K and the right runs L-Z, and you scroll past "M" without seeing it. */
  .sec{margin-bottom:8px}
  @media(min-width:900px){ .sec{column-count:2;column-gap:36px} }
  @media(min-width:1400px){ .sec{column-count:3} }

  .e{break-inside:avoid;margin:0 0 5px}
  .hw{font-weight:600}
  .see{color:var(--dim);font-style:italic}
  .q{font-style:italic;color:var(--fg)}
  .sub2,.sense{margin-left:14px}
  /* a subentry sitting under a sense indents past the sense's own label */
  .sense .nest .sub2{margin-left:0}
  .sense .nest{margin-left:14px}
  .sig{font-style:italic;color:var(--dim);font-size:12.5px;
       font-family:ui-sans-serif,system-ui;padding-right:1px}
  a.pg{color:var(--accent);text-decoration:none;padding:0 1px;border-radius:3px}
  a.pg:hover{text-decoration:underline;background:var(--hit)}
  a.pg.prim{font-weight:700}
  .tabs{display:flex;gap:2px;margin:8px 0 0}
  .tab{background:none;border:0;border-bottom:2px solid transparent;cursor:pointer;
       padding:5px 12px 6px;color:var(--dim);font:600 12.5px/1 ui-sans-serif,system-ui;
       letter-spacing:.03em;border-radius:4px 4px 0 0}
  .tab:hover{color:var(--fg)}
  /* A tab IS a <button>, so `button.on` above lands on it and fills it with --accent --
     while `.tab.on` (two classes, higher specificity) wins the colour and paints the label
     --accent too. Accent on accent: the selected tab reads as a blank block. These are
     underline tabs, not pills, so put the fill back to none explicitly. */
  .tab.on{background:none;color:var(--accent);border-bottom-color:var(--accent)}
  #fleet{display:none;padding:14px 20px 60px}
  #fleet.on{display:block}
  #fleet table{border-collapse:collapse;width:100%;max-width:900px;
               font:14px/1.5 ui-serif,Georgia,serif}
  #fleet th{text-align:left;font:600 11px/1 ui-sans-serif,system-ui;letter-spacing:.07em;
            text-transform:uppercase;color:var(--dim);padding:0 14px 7px 0;
            border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap;user-select:none}
  #fleet th:hover{color:var(--accent)}
  #fleet th.by{color:var(--accent)}
  #fleet td{padding:4px 14px 4px 0;border-bottom:1px solid var(--line);vertical-align:baseline}
  #fleet td.t{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--dim)}
  #fleet td.n{width:99%}
  .az{font:700 12px/1 ui-sans-serif,system-ui;color:var(--accent);letter-spacing:.08em;
      margin:20px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
  mark{background:var(--hit);color:inherit;border-radius:2px}
  a.xref{color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent)}
  a.xref:hover{background:var(--hit)}
  /* a cross-reference lands you somewhere new -- show where, or the jump is disorienting.
     The flash starts when the jump starts, but the smooth scroll to the target can run the
     best part of a second on a page this long -- so the highlight has to outlast the
     journey, or the reader arrives just in time to watch it fade. Hence 4s, held solid for
     the first 2.6 of them. */
  @keyframes flash{
    0%,65%{background:var(--flash);box-shadow:0 0 0 5px var(--flash)}
    100%{background:transparent;box-shadow:0 0 0 5px transparent}
  }
  .e.flash{animation:flash 4s ease-out;border-radius:3px}
  html{scroll-behavior:smooth}

  #settings,#about{display:none;background:var(--card);border:1px solid var(--line);
            border-radius:10px;padding:16px;margin:12px 0 0}
  #settings.open,#about.open{display:block}
  #settings h2,#about h2{margin:0 0 4px;font-size:15px}
  #settings p,#about p{margin:0 0 12px;color:var(--dim);font-size:12.5px;line-height:1.55}
  #about{max-height:70vh;overflow:auto}
  #about h3{margin:16px 0 6px;font-size:12px;letter-spacing:.07em;
            font-family:ui-sans-serif,system-ui;color:var(--accent);text-transform:uppercase}
  #about table{border-collapse:collapse;font-size:12px;margin:2px 0 8px;width:100%}
  #about td{padding:3px 12px 3px 0;vertical-align:top;border-bottom:1px solid var(--line)}
  #about code{font-family:ui-monospace,SFMono-Regular,monospace;font-size:10.5px;
              color:var(--dim);word-break:break-all}
  #about .lic{font-size:10.5px;line-height:1.6}
  .row{display:grid;grid-template-columns:52px 1fr auto;gap:10px;align-items:center;
       margin-bottom:7px}
  .row button{padding:5px 9px;font-size:11.5px}
  .ok{color:#2e7d32}
  .row b{font:700 12px/1 ui-sans-serif,system-ui}
  .row input{width:100%;font-size:12.5px;font-family:ui-monospace,SFMono-Regular,monospace}
  .row input.bad{border-color:#c0392b}
  .setbar{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}
  .setbar #base{flex:1;min-width:280px}

</style>
</head>
<body>
<header>
  <div class="titlerow">
    <div>
      <h1>Mongoose Traveller 2e — Master Index</h1>
      <div class="sub" id="tag"></div>
    </div>
    <div class="icons">
      <button id="cfg" class="icon" title="Settings — where your PDFs live" aria-label="Settings">⚙</button>
      <button id="abt" class="icon" title="About, how to use, and copyright" aria-label="About">?</button>
    </div>
  </div>
  <div class="tabs">
    <button id="tab-az" class="tab on">A–Z index</button>
    <button id="tab-sh" class="tab">Ships</button>
  </div>
  <div class="bar">
    <input type="search" id="q" placeholder="Search terms…  (press / to focus)" autocomplete="off">
    <span class="count" id="n"></span>
  </div>
  <div id="settings">
    <h2>Where are your PDFs?</h2>
    <p>Each page number links into a book. Tell the index where each book actually lives.
       A path can be a plain filename (same folder as this page), a relative path
       (<code>books/High&nbsp;Guard.pdf</code>), an absolute <code>file:///…</code> URL, or an
       <code>https://…</code> URL. Settings are stored in this browser only.</p>
    <div class="setbar" style="margin:0 0 12px">
      <button id="pickall">Choose the PDFs…</button>
      <span class="count" id="pickmsg">Select all seven at once - filenames are filled in exactly, no typing.</span>
    </div>
    <div id="rows"></div>
    <div class="setbar">
      <input type="text" id="base" placeholder="Folder the books live in, e.g. file:///Users/you/Traveller/  (blank = same folder as this page)">
      <button id="apply">Apply folder</button>
      <button id="reset">Reset</button>
      <button id="test">Test first link</button>
    </div>
    <p style="margin-top:12px"><b>Why you still have to type the folder:</b> browsers never reveal a
       file's full path to a web page - that is a deliberate security boundary, not something this
       index can work around. Picking the files gets their <i>names</i> exactly right; the folder is
       the one thing you set, once.</p>
    <p>Chrome and Adobe Acrobat honour <code>#page=</code> and jump to the exact paragraph. Safari
       and macOS Preview open the right page but may ignore the paragraph.</p>
    <input type="file" id="fileinput" accept="application/pdf,.pdf" multiple hidden>
  </div>
  <div id="about"></div>
  <div id="alpha"></div>
</header>
<main><div id="list"></div><div id="fleet"></div></main>

<script>
const DATA = __DATA__;
const KEY = "mgt2-index-paths-v1";

let paths = {};
try { paths = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch(e){ paths = {}; }
const pathFor = i => paths[DATA.books[i].sig] || DATA.books[i].file;

// PDF open parameters: page is 1-based; view=FitH,<top> scrolls the paragraph into view.
// `top` is PDF user space (measured from the bottom of the page) -- precomputed at build.
const url = r => {
  const [b, , pdfPage, top] = r;
  return pathFor(b) + "#page=" + pdfPage + "&view=FitH," + top;
};

const esc = s => s.replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function refsHTML(rs){
  let out = "", lastBook = -1;
  rs.forEach((r, i) => {
    const [b, printed, , , prim] = r;
    if (b !== lastBook) { out += (lastBook === -1 ? ", " : "; ")
      + '<span class="sig">' + DATA.books[b].sig + "</span> "; lastBook = b; }
    else out += ", ";
    // open the book in its own tab -- clicking a page ref must never navigate the
    // index away from under you
    out += '<a class="pg' + (prim ? " prim" : "") + '" target="_blank" rel="noopener"'
        + ' href="' + esc(url(r)) + '" data-b="' + b + '">' + printed + "</a>";
  });
  return out;
}

// ---- Ships: every design in one place, sortable ----
// The A-Z answers "where is the Tigress?". This answers the question actually asked at the
// table -- "what have I got at about 400 tons?" -- which no alphabetical index can.
const fleet = document.getElementById("fleet");
let shipSort = "t", shipDesc = false;

function fleetHTML(){
  const rows = DATA.ships.slice().sort((a, b) => {
    let v;
    if (shipSort === "t") v = a.t - b.t || a.n.toLowerCase().localeCompare(b.n.toLowerCase());
    else if (shipSort === "b") v = a.r[0][0] - b.r[0][0] || a.t - b.t;
    else v = a.n.toLowerCase().localeCompare(b.n.toLowerCase());
    return shipDesc ? -v : v;
  });
  const th = (k, label, cls) =>
    '<th data-k="' + k + '"' + (cls ? ' class="' + cls + (shipSort === k ? " by" : "") + '"'
                                   : (shipSort === k ? ' class="by"' : "")) + ">" + label
    + (shipSort === k ? (shipDesc ? " ↓" : " ↑") : "") + "</th>";
  let h = '<table><thead><tr>' + th("n", "Ship") + th("t", "Tons", "t")
        + th("b", "Where specified") + "</tr></thead><tbody>";
  for (const s of rows)
    h += "<tr><td class=\"n\">" + esc(s.n) + "</td>"
       + '<td class="t">' + s.t.toLocaleString() + "</td>"
       + "<td>" + refsHTML(s.r).replace(/^, /, "") + "</td></tr>";
  return h + "</tbody></table>";
}

function renderFleet(){
  fleet.innerHTML = fleetHTML();
  document.getElementById("n").textContent = DATA.ships.length + " ship designs";
  fleet.querySelectorAll("th").forEach(el => el.onclick = () => {
    const k = el.dataset.k;
    shipDesc = shipSort === k ? !shipDesc : false;
    shipSort = k;
    renderFleet();
  });
}

let view = "az";
function show(v){
  view = v;
  document.getElementById("tab-az").classList.toggle("on", v === "az");
  document.getElementById("tab-sh").classList.toggle("on", v === "sh");
  list.style.display = v === "az" ? "" : "none";
  fleet.classList.toggle("on", v === "sh");
  document.getElementById("alpha").style.display = v === "az" ? "" : "none";
  q.style.display = v === "az" ? "" : "none";
  if (v === "sh") renderFleet(); else doSearch();
}

const slug = id => "e-" + id.replace(/[^a-z0-9]+/g, "-");

function recHTML(rec){
  if (rec.k === "s")
    return '<div class="e"><span class="hw">' + esc(rec.t)
         + '</span><span class="see">, see </span>'
         + '<a class="xref" href="#' + slug(rec.toId) + '" data-to="' + esc(rec.toId) + '">'
         + esc(rec.to) + "</a></div>";
  let h = '<div class="e" id="' + slug(rec.id) + '">';
  const subs = s => s.sub.map(x =>
    '<div class="sub2">' + esc(x.t) + refsHTML(x.r) + "</div>").join("");
  // One sense, no qualifier: the headword IS the sense, so don't print a redundant label.
  if (rec.s.length === 1 && !rec.s[0].q)
    h += '<span class="hw">' + esc(rec.t) + "</span>" + refsHTML(rec.s[0].r) + subs(rec.s[0]);
  else {
    h += '<span class="hw">' + esc(rec.t) + "</span>";
    // Subentries nest INSIDE their sense -- 'Corporate' under Agent-the-career, not adrift
    // in one alphabetical pile shared with Agent-the-toxin.
    rec.s.forEach(s => h += '<div class="sense"><span class="q">' + esc(s.q || "general")
      + "</span>" + refsHTML(s.r) + '<div class="nest">' + subs(s) + "</div></div>");
  }
  return h + "</div>";
}

// ---- render (letter headings interleaved) ----
const list = document.getElementById("list");
function render(recs){
  // Group into letter sections; each section columnises independently so reading order
  // stays alphabetical down-then-across, as in a printed index.
  let html = "", letter = null, open = false;
  for (const r of recs){
    const key = r.L || "#";
    if (key !== letter){
      if (open) html += "</div>";
      letter = key;
      html += '<div class="az" id="az-' + key + '">' + key + '</div><div class="sec">';
      open = true;
    }
    html += recHTML(r);
  }
  if (open) html += "</div>";
  list.innerHTML = html;
  document.getElementById("n").textContent = recs.length.toLocaleString() + " entries";
}

// ---- search: matches headword, aliases, and subentries ----
const q = document.getElementById("q");
const hay = DATA.records.map(r => (
  r.t + " " + (r.a || []).join(" ")
  + " " + (r.s || []).map(s => s.q + " " + s.sub.map(x => x.t).join(" ")).join(" ")
).toLowerCase());

let t = null;
q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(doSearch, 90); });
function doSearch(){
  const v = q.value.trim().toLowerCase();
  if (!v) return render(DATA.records);
  const terms = v.split(/\s+/);
  render(DATA.records.filter((_, i) => terms.every(w => hay[i].includes(w))));
}
document.addEventListener("keydown", e => {
  if (e.key === "/" && document.activeElement !== q){ e.preventDefault(); q.focus(); }
  if (e.key === "Escape"){ q.value = ""; doSearch(); q.blur(); }
});

// ---- cross-references ----
// "see X" must actually go to X. Two things make this fiddly: the target may be filtered
// out of the current search, and after a jump the reader needs to see WHERE they landed.
function goTo(id){
  const land = () => {
    const el = document.getElementById(slug(id));
    if (!el) return;
    el.scrollIntoView({block: "center", behavior: "smooth"});
    el.classList.remove("flash");
    void el.offsetWidth;          // restart the animation if it is already running
    el.classList.add("flash");
    history.replaceState(null, "", "#" + slug(id));
  };
  if (q.value.trim()){           // clear the filter first, or the target is not on the page
    q.value = "";
    render(DATA.records);
    requestAnimationFrame(land);
  } else land();
}
list.addEventListener("click", e => {
  const a = e.target.closest("a.xref");
  if (!a) return;
  e.preventDefault();
  goTo(a.dataset.to);
});

// ---- A–Z jump ----
document.getElementById("alpha").innerHTML =
  ["#"].concat("ABCDEFGHIJKLMNOPQRSTUVWXYZ".split(""))
  .map(c => '<a href="#az-' + c + '">' + c + "</a>").join("");

// ---- settings ----
const rows = document.getElementById("rows");
rows.innerHTML = DATA.books.map((b, i) =>
  '<div class="row"><b>' + b.sig + '</b>'
  + '<input type="text" data-sig="' + b.sig + '" value="' + esc(pathFor(i))
  + '" title="' + esc(b.title) + '">'
  + '<button data-pick="' + i + '">Choose…</button></div>').join("");

// ---- file pickers ----
// A browser will not tell a page where a file lives -- only its name and its bytes. So the
// picker's job is to get the FILENAME exactly right (these names are long and dated, and a
// single wrong character silently breaks every link into that book). The folder is typed once.
const fileInput = document.getElementById("fileinput");
let pickTarget = null;   // book index, or null for "match them all by name"

function applyPicked(files){
  const base = document.getElementById("base").value.trim();
  const pre = base && !/[/]$/.test(base) ? base + "/" : base;
  const inputs = [...rows.querySelectorAll("input")];
  let n = 0;
  if (pickTarget !== null){
    if (files[0]){ inputs[pickTarget].value = pre + files[0].name; n = 1; }
  } else {
    // match each chosen file to a book. Prefer the registered filename; fall back to the
    // siglum's distinctive words so a renamed-but-recognisable file still lands right.
    for (const f of files){
      let i = DATA.books.findIndex(b => b.file.toLowerCase() === f.name.toLowerCase());
      if (i < 0) i = DATA.books.findIndex((b, j) =>
        !inputs[j].dataset.claimed &&
        b.title.toLowerCase().split(/\W+/).filter(w => w.length > 4)
          .some(w => f.name.toLowerCase().includes(w)));
      if (i >= 0){ inputs[i].value = pre + f.name; inputs[i].dataset.claimed = "1"; n++; }
    }
    inputs.forEach(i => delete i.dataset.claimed);
  }
  const msg = document.getElementById("pickmsg");
  msg.textContent = n + " of " + DATA.books.length + " matched"
    + (n < DATA.books.length ? " - set the rest by hand" : " - all set");
  msg.className = "count" + (n === DATA.books.length ? " ok" : "");
  save();
  pickTarget = null;
}

document.getElementById("pickall").onclick = () => { pickTarget = null; fileInput.multiple = true; fileInput.click(); };
rows.addEventListener("click", e => {
  const b = e.target.closest("button[data-pick]");
  if (!b) return;
  pickTarget = +b.dataset.pick; fileInput.multiple = false; fileInput.click();
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) applyPicked([...fileInput.files]);
  fileInput.value = "";
});

function save(){
  paths = {};
  rows.querySelectorAll("input").forEach(i => {
    if (i.value.trim()) paths[i.dataset.sig] = i.value.trim();
  });
  localStorage.setItem(KEY, JSON.stringify(paths));
  // rebuild every href -- in whichever view is showing
  if (view === "sh") renderFleet(); else doSearch();
}
rows.addEventListener("input", save);

function panel(which){
  const map = {settings: "cfg", about: "abt"};
  for (const [id, btn] of Object.entries(map)){
    const open = id === which && !document.getElementById(id).classList.contains("open");
    document.getElementById(id).classList.toggle("open", open);
    document.getElementById(btn).classList.toggle("on", open);
  }
}
document.getElementById("tab-az").onclick = () => show("az");
document.getElementById("tab-sh").onclick = () => show("sh");

document.getElementById("cfg").onclick = () => panel("settings");
document.getElementById("abt").onclick = () => panel("about");
document.getElementById("apply").onclick = () => {
  let p = document.getElementById("base").value.trim();
  if (p && !/[/]$/.test(p)) p += "/";
  rows.querySelectorAll("input").forEach((i, n) => i.value = p + DATA.books[n].file);
  save();
};
document.getElementById("reset").onclick = () => {
  localStorage.removeItem(KEY); paths = {};
  rows.querySelectorAll("input").forEach((i, n) => i.value = DATA.books[n].file);
  doSearch();
};
document.getElementById("test").onclick = () => {
  const first = DATA.records.find(r => r.k === "e" && r.s[0].r.length);
  window.open(url(first.s[0].r[0]), "_blank");
};

// ---- colophon ----
const s2 = DATA.stats;
document.getElementById("tag").textContent =
  s2.headwords.toLocaleString() + " headwords · " + s2.senses.toLocaleString()
  + " senses · " + s2.refs.toLocaleString() + " page references across "
  + DATA.books.length + " books";

document.getElementById("about").innerHTML =
    "<h2>About this index</h2>"
  + "<p>Mongoose spreads the rules for a single topic across many books. This index gathers them: "
  + "look up <i>armour</i> and you see the personal-equipment entry, the combat rule, the spacecraft "
  + "component and the robot chassis option, across five books, in one place. Nothing like it exists "
  + "in the books themselves - each ships only its own index, and those index headings, not concepts.</p>"

  + "<h3>How to use it</h3>"
  + "<p><b>Search</b> filters as you type and matches headwords, alternative names, subentries and "
  + "sense labels. Press <b>/</b> to jump to the box, <b>Esc</b> to clear.<br>"
  + "<b>Page numbers are links</b> - click one and the book opens in a new tab at that page, and in "
  + "Chrome or Acrobat at that <i>paragraph</i>.<br>"
  + "<b>Bold</b> marks the passage that <i>defines</i> the concept. Plain numbers are places it is "
  + "used or referenced.<br>"
  + "<b>Italic labels</b> under a headword separate different senses of the same word.</p>"

  + "<h3>Before the links will work</h3>"
  + "<p>Open <b>&#9881;</b> Settings and tell the index where your PDFs are. You can pick all seven "
  + "files at once - the exact filenames are filled in for you - and then give the folder they live "
  + "in. Your paths are stored in this browser only.</p>"

  + "<h3>Books indexed</h3>"
  + "<p>These page references are only valid against these exact files. If you are unsure you have "
  + "the same printing, check the SHA-256.</p><table>"
  + DATA.books.map(b =>
      "<tr><td><b>" + b.sig + "</b></td><td>" + esc(b.title)
      + "<br><code>" + esc(b.file) + "</code><br><code>sha-256 " + b.sha + "</code></td>"
      + "<td style='text-align:right;white-space:nowrap'>" + b.pages + " pp.</td></tr>").join("")
  + "</table>"
  + "<p>" + s2.headwords.toLocaleString() + " headwords, " + s2.senses.toLocaleString()
  + " senses and " + s2.refs.toLocaleString() + " page references, distilled from "
  + s2.entries.toLocaleString() + " raw source references.</p>"

  + "<h3>Copyright and permission</h3>"
  + "<p class=lic>" + esc(DATA.notice) + "</p>"
  + "<p class=lic>This index is a fan-made, non-commercial reference work. It is not sold and carries "
  + "no charge. It contains no rules text - only terms and page references pointing into books you "
  + "must already own. All rules content remains the property of Mongoose Publishing.</p>";

render(DATA.records);

// The list is built by JS, so a hash in the URL points at an element that did not exist
// when the browser first tried to scroll to it. Honour it once the entries are on the page,
// so a link like  ...#e-jump-drive  actually lands.
if (location.hash.startsWith("#e-")){
  const want = location.hash.slice(1);
  const rec = DATA.records.find(r => r.k === "e" && slug(r.id) === want);
  if (rec) requestAnimationFrame(() => goTo(rec.id));
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
