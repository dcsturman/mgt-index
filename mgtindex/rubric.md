You are building a professional back-of-book index for a tabletop RPG rulebook.

You will be given a WINDOW of consecutive text chunks from the book. Each chunk has an
id, a printed page number, and the heading path it sits under. Emit index entries for
the window.

# What makes this index different

The book already ships an index. It is a dump of section headings — "Opposed Checks 62",
"Multiple Tasks 62". It is nearly useless, because the thing a reader actually needs is
almost never the heading. Your job is to index what a reader will LOOK UP, not what the
author chose to put in bold.

Concretely, the rule for what happens when an opposed check TIES lives inside an unheaded
paragraph. Today it is unfindable. You should emit `opposed check, tied result` and
`standstill`. That is the entire point of this exercise. Hunt for these.

# What you can and cannot see

You see six chunks — roughly two printed pages. You CANNOT see the rest of the book. So do
not try to answer questions about the book as a whole; you will only guess, and a later
stage answers them properly with every passage in view. Two rules below exist because of
this, and they are the ones indexers most often get wrong. Read them carefully.

# Rules

1. NEVER emit a page number. Page numbers come from the chunk metadata, not from you.
   Emit only terms, tagged with the chunk id they came from.

2. `primary` vs `mention` is a question about THIS PASSAGE, not about the book.

   Ask only: does this passage DEFINE the term, or does it USE one it assumes you know?

   - primary: the passage introduces the thing and tells you what it is or how it works —
     it gives the rule, the procedure, the statistics, the table.
   - mention: the passage uses the term while taking its meaning for granted, applies a
     rule stated elsewhere, or merely lists the thing in passing.

   The Skills chapter USES the Dice Modifier on nearly every page and DEFINES it nowhere.
   Every DM reference there is a mention.

   You are NOT being asked to pick the book's single best page for a term. You cannot know
   that from six chunks, and it is not your job — a later stage compares all the passages
   and elects one. So do not agonise, and do not hedge by marking everything primary. Just
   report honestly what THIS passage does. If it defines, say primary. If it assumes, say
   mention.

   Be strict about mentions too: most passing uses should simply be DROPPED, not tagged
   `mention`. Reserve `mention` for a genuinely useful secondary reference — a rule
   modified elsewhere, a table that also lists the item, a worked example of it.

3. DENSITY. Count, because counting is something you CAN do.

   Every chunk is tagged with the printed page it came from. COUNT THE DISTINCT PAGES in
   this window, then aim for 8-15 entries PER DISTINCT PAGE.

   So a window covering 3 pages wants roughly 24-45 entries; a window that sits entirely on
   one page wants 8-15. Work it out from the page tags — do not assume a window is any
   fixed number of pages, because it is not.

   Fewer than 8 per page means you are skimming, and a reader will look something up and
   not find it. More than 15 means you are cataloguing sentences rather than indexing
   concepts. Neither bound is a quota to game: if a page is genuinely fiction or artwork,
   emit nothing for it.

4. Use the reader's vocabulary, not the author's.
   - Index the concept, not the sentence. "the referee may allow a second attempt" is
     `retrying a failed check`.
   - Prefer natural lookup order. `jump drive, fuel requirements` not
     `fuel requirements of the jump drive`.
   - Give `aliases` for anything a reader might plausibly look up under another name
     ("J-drive", "misjump", "Dice Modifier" / "DM").

5. Subentries. If a term is a facet of a bigger concept, set `parent` to the bigger
   concept and `term` to the facet. `parent: "opposed check"`, `term: "tied result"`.
   Leave `parent` empty for top-level entries.

   Every term you name as a `parent` MUST also be emitted as an entry in its own right,
   with parent empty. A headword with subentries but no page of its own is broken.

6. NEVER emit a bare term that is meaningless out of context. A reader scanning the
   index sees only the term, not the page it came from. "Philosophy", "Cosmology",
   "Economics" are specialities of the Science skill — indexed alone they are noise.
   Set parent: "Science (skill)" and term: "Philosophy". Same for every speciality,
   option, and sub-choice of a larger thing. If a term cannot stand alone, give it a
   parent or drop it.

7. DO NOT INDEX EXAMPLE USES OF A SKILL. This is the single most common way to bloat
   an RPG index. A skill description lists the sort of thing the skill is good for —
   "argue a case in court", "gather rumours in a bar", "bypass a guard", "palm an
   object", "find a buyer". These are ILLUSTRATIONS, not rules. Nobody opens the index
   hunting for "palming objects". Drop every one of them.

   The distinction that matters:
   - A SPECIALITY is a formal rules construct, written Skill (Speciality). A character
     has a level in it: Drive (Wheel) 2, Science (Astronomy) 1, Athletics (Dexterity) 3.
     INDEX THESE as subentries of the skill.
   - An EXAMPLE USE is prose describing what you might do with the skill. DO NOT INDEX.

   Athletics has exactly three specialities — Dexterity, Endurance, Strength. It does
   NOT have "climbing", "jumping", "sprinting" or "gravity operations"; those are
   example uses. If you cannot write the term as Skill (Term) and have it be something
   a character could have a level in, it is not a speciality — drop it.

8. NEVER drop the system's core vocabulary. Defined game terms — the ones the rules
   themselves use over and over — MUST be indexed wherever they are defined, even when
   the passage looks like plumbing rather than a rule. Dice Modifier (DM), Effect,
   characteristic, task chain, skill level, Boon, Bane, difficulty, and their kin are
   what a confused player looks up first. Missing them is a worse failure than missing
   ten interesting edge cases. If a term is defined in this window, index it.

9. Tables get indexed as objects ("Task Difficulty table") AND, where a reader would
   plausibly look up a row, by their contents. Don't do this exhaustively — only where
   the row is something someone would search for.

10. SHIP SPECIFICATIONS. A full specification of a vessel — a stat block giving its hull
    tonnage, drives, power plant, weapons and cost — is a DIFFERENT and far more valuable
    thing than a passing mention of the ship's name, and readers hunt for it constantly.

    When a window contains a ship's specification:
    - emit the ship with kind `ship` and role `primary`. This must be the ONLY page you
      call primary for that vessel; wherever else the ship is merely named, it is a
      `mention` or is dropped.
    - use the ship's own name as the term, in the form a reader would look it up under —
      the specific name, not the generic class: `Beowulf-class Free Trader`, not
      `Free Trader`; `Arakoine-class Strike Cruiser`, not `Strike Cruiser`. Put every other
      name the page gives it in `aliases` (the generic class, the hull code — `Type S`,
      `Class: XT` — and any nickname the prose offers, e.g. a launch "also called a
      lifeboat").
    - DO NOT emit the components of the stat block as ships. A specification lists a jump
      drive, a power plant, sensors, a bridge, staterooms. Those are components; they are
      defined elsewhere in the rules and must not be tagged `ship`, and on a specification
      page they should usually not be indexed at all.
    - do not emit the tonnage yourself. It is read from the stat block deterministically,
      exactly as page numbers are. Rule 1 applies: no numbers.

# Entry types

rule | table | example | procedure | term | equipment | ship | career | skill | world | creature | alien

# What to skip

Flavour text, fiction, section transitions, "as described above" pointers, and anything
that is purely an artifact of layout.

<!-- Everything below is appended per book, selected by its `genre` in books.toml. The
     seven rules above are shared; these sections say what a reader HUNTS FOR in this
     particular kind of book, because that differs enormously and the shared rules were
     written with a rules manual in mind. -->

# Genre: rules

This is a core rules manual. The reader is at the table mid-game with a question, and the
question is almost always procedural: how does X work, what do I roll, what modifies it.

Index above all: the resolution procedures, the modifiers, the defined game terms, the
edge cases buried in unheaded paragraphs (a tie, a failure by more than 6, what happens on
a natural 2). Index the careers, their assignments and their tables. Index the skills and
their formal specialities.

The unheaded exception buried mid-paragraph is the most valuable thing in the book and the
hardest to find. Hunt for it.

# Genre: catalogue

This is an equipment catalogue. Nearly every entry in it IS a definition — that is what a
catalogue is — so do not be shy about `primary`, and do not worry that you are indexing a
lot. A reader comes here to find one specific item, so EVERY purchasable item is worth a
headword: every weapon, every suit of armour, every drug, every tool, every augment.

Index each item by the name a reader would say aloud, and give the manufacturer's or
formal name as an alias where they differ. Group variants as subentries of the family
(`battle dress` with `Battle Dress (standard)`, `(heavy assault)`, `(noble/command)`).

Also index the rules that ride along with the gear — how armour layering works, what
encumbrance does, TL availability, how a drug's addiction rules work — because those are
genuinely hard to find and a reader hunting for them has nowhere else to look.

# Genre: construction

This is a design/construction manual: the reader is BUILDING something (a ship, a robot,
a vehicle) and needs a component, a rule, or a number.

Index every component that can be fitted, every hull option, every modification, every
drive, every weapon mount — each as a headword or as a subentry of its family. Index the
construction steps themselves as procedures. Index the tables heavily: a reader building a
ship lives in the tables, and "what page is the Armour Tonnage table on" is a real
question.

Index the design CONSTRAINTS too — tonnage limits, power requirements, TL minimums,
what may not be combined with what. These are scattered through the prose and are exactly
what a builder cannot find.

# Genre: bestiary

This is a book of peoples, creatures and worlds. The reader wants a specific alien, a
specific world, or a specific piece of culture.

Index every named species, every named creature, every named world, sector, subsector and
polity, and every named organisation. Index each species' distinctive traits, its careers
and its castes as subentries of the species — `parent: "K'kree"`, `term: "merchant caste"`.

Index the species-specific RULES hardest of all: the modified characteristics, the unique
skills, the psionic talents, the special careers, the traits that change how a character
plays. Those are the reason someone opens this book, and they are buried in cultural prose.

Do not index the fiction, the history for its own sake, or the flavour of a passage. A date
or a battle only earns an entry when the reader could plausibly look it up by name.
