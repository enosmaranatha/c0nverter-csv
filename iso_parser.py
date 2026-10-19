"""
iso_parser.py
Parser dokumen standar ISO (PDF dengan text layer) -> sekumpulan DataFrame "tidy"
yang siap diekspor ke CSV dan dibaca di R / RStudio.

Dirancang & diuji pada ISO 15118-8:2020, tetapi pola yang dipakai (nomor klausul,
kode requirement [XXX-000], Terms and definitions, Abbreviated terms, Normative
references, Table/Figure caption, NOTE) umum di standar ISO/IEC lain.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import BinaryIO

import pandas as pd
import pdfplumber

# --------------------------------------------------------------------------- #
# Konstanta & regex
# --------------------------------------------------------------------------- #
_ZW = {0x200B: None, 0x200C: None, 0x200D: None, 0xFEFF: None, 0x00AD: None}

FOOTER_RE = re.compile(r"©\s*(?:ISO|IEC)(?:/IEC)?\s*\d{4}\s*[–-]\s*All rights reserved", re.I)
DOCHDR_RE = re.compile(r"^(?:ISO|IEC)(?:/IEC)?\s*[\d\-]+(?::\d{4})?(?:/[A-Za-z0-9\.]+)?\s*\((?:E|F|E/F)\)\s*$")
PAGENO_RE = re.compile(r"^(?:\d{1,3}|[ivxlc]{1,6})$", re.I)

HEADING_RE = re.compile(
    r"^(\d{1,2}(?:\.\d{1,2}){0,5}|[A-Z]\.\d{1,2}(?:\.\d{1,2}){0,4})\s+(\S.*)$"
)
ANNEX_RE = re.compile(r"^Annex\s+([A-Z])$")
ANNEX_KIND_RE = re.compile(r"^\((informative|normative)\)$", re.I)
UNNUMBERED_HEADINGS = {"Bibliography", "Foreword", "Introduction", "Index"}

REQ_RE = re.compile(r"^\[([A-Z0-9]{2,10}-\d{1,4})\]\s*(.*)$")
NOTE_RE = re.compile(r"^NOTE(?:\s+\d+)?\s+(.*)$")
EXAMPLE_RE = re.compile(r"^EXAMPLE(?:\s+\d+)?\s+(.*)$")
CAPTION_RE = re.compile(r"^(Table|Figure)\s+((?:[A-Z]\.)?\d+)\s*[—–-]\s*(.*)$")
LISTITEM_RE = re.compile(r"^(?:—|•|-|[a-z]\)|\d+\))\s+(.*)$")
TERM_ID_RE = re.compile(r"^(\d{1,2}\.\d{1,3}(?:\.\d{1,3})?)$")
REF_START_RE = re.compile(
    r"^((?:ISO|IEC|IEEE|EN|ITU|ETSI|SAE|ASTM|DIN|UL|CISPR)\b[^,]{0,70}?\d[^,]{0,30}),\s*(.+)$"
)

MODAL_RE = re.compile(r"\b(shall not|shall|should not|should|may|can|must)\b", re.I)
REQ_REF_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{3}\b")
TABLE_REF_RE = re.compile(r"\b(?:Table|Figure)\s+(?:[A-Z]\.)?\d+")
CLAUSE_REF_RE = re.compile(r"\((\d{1,2}(?:\.\d{1,3}){1,4})\)")

STOPWORDS = set("""
a about above after again all also an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how
i if in into is it its itself just me more most my no nor not now of off on once only or other our out over own same
she should so some such than that the their theirs them then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your shall may must e g eg ie etc
see iso iec ieee annex clause table figure note example
""".split())


# --------------------------------------------------------------------------- #
# Util teks
# --------------------------------------------------------------------------- #
def clean(s: str) -> str:
    s = s.translate(_ZW)
    s = (s.replace("\u2011", "-").replace("\u2010", "-").replace("\u00a0", " ")
           .replace("\u2009", " ").replace("\u202f", " "))
    return re.sub(r"[ \t]+", " ", s).strip()


def _inside(line: dict, bbox: tuple, pad: float = 2.0) -> bool:
    x0, top, x1, bottom = bbox
    return (line["top"] >= top - pad and line["bottom"] <= bottom + pad
            and line["x0"] >= x0 - pad and line["x1"] <= x1 + pad)


def _is_bold(chars: list[dict]) -> bool:
    if not chars:
        return False
    b = sum(1 for c in chars if "bold" in c.get("fontname", "").lower())
    return b / len(chars) >= 0.6


# --------------------------------------------------------------------------- #
# Tahap 1: kumpulkan baris per halaman (tanpa tabel, tanpa header/footer)
# --------------------------------------------------------------------------- #
def _collect(pdf, pages: list[int], strip_margins: bool):
    page_lines, page_tables = {}, {}
    for pno in pages:
        page = pdf.pages[pno - 1]
        tables = page.find_tables()
        bboxes = [t.bbox for t in tables]
        lines = []
        for l in page.extract_text_lines(return_chars=True):
            txt = clean(l["text"])
            if not txt:
                continue
            if any(_inside(l, b) for b in bboxes):
                continue
            lines.append(dict(text=txt, x0=l["x0"], x1=l["x1"], top=l["top"], bottom=l["bottom"],
                              bold=_is_bold(l.get("chars", [])), page=pno, page_h=page.height))
        page_lines[pno] = lines
        page_tables[pno] = tables

    if strip_margins:
        # header/footer: teks di tepi atas/bawah yang berulang antar halaman
        cnt = Counter()
        for pno, lines in page_lines.items():
            for l in lines:
                if l["top"] < 60 or l["bottom"] > l["page_h"] - 55:
                    cnt[re.sub(r"\d+|\b[ivxlc]{1,5}\b", "#", l["text"].lower())] += 1
        thr = max(3, int(0.4 * len(pages)))
        for pno, lines in page_lines.items():
            keep = []
            for l in lines:
                margin = l["top"] < 60 or l["bottom"] > l["page_h"] - 55
                if margin:
                    key = re.sub(r"\d+|\b[ivxlc]{1,5}\b", "#", l["text"].lower())
                    if (cnt[key] >= thr or FOOTER_RE.search(l["text"]) or DOCHDR_RE.match(l["text"])
                            or PAGENO_RE.match(l["text"])):
                        continue
                keep.append(l)
            page_lines[pno] = keep
    return page_lines, page_tables


def _find_body_start(page_lines: dict[int, list[dict]], pages: list[int]) -> int:
    """Halaman pertama yang memuat heading '1 Scope' (bukan baris daftar isi)."""
    for pno in pages:
        for l in page_lines[pno]:
            if re.match(r"^1\s+(Scope|Domaine d.application)$", l["text"], re.I):
                return pno
    return pages[0]


# --------------------------------------------------------------------------- #
# Tahap 2: mesin status untuk klausul / requirement / note / dst.
# --------------------------------------------------------------------------- #
class _Builder:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.blocks: list[dict] = []
        self.clauses: dict[str, dict] = {}
        self.terms: list[dict] = []
        self.abbrs: list[dict] = []
        self.refs: list[dict] = []
        self.captions: list[dict] = []
        self.clause_id = "front"
        self.clause_title = ""
        self.mode = None
        self.cur: dict | None = None
        self.term_id: str | None = None
        self.term_lines: list[str] = []
        self.term_page = None
        self.abbr_x0: float | None = None

    # -- blok teks ---------------------------------------------------------- #
    def open_block(self, btype, text, line, req_id=None):
        self.close_block()
        self.cur = dict(doc_id=self.doc_id, clause_id=self.clause_id, clause_title=self.clause_title,
                        block_type=btype, req_id=req_id, text=text, page=line["page"], _x0=line["x0"])

    def close_block(self):
        if self.cur and self.cur["text"].strip():
            self.cur["text"] = re.sub(r"\s+", " ", self.cur["text"]).strip()
            self.blocks.append(self.cur)
        self.cur = None

    # -- klausul ------------------------------------------------------------ #
    def new_clause(self, cid: str, title: str, page: int):
        self.flush_term()
        self.close_block()
        if cid.startswith("Annex"):
            parent, level = None, 1
        elif re.match(r"^[A-Z]\.", cid):
            parts = cid.split(".")
            parent = "Annex " + parts[0] if len(parts) == 2 else ".".join(parts[:-1])
            level = len(parts)
        else:
            parts = cid.split(".")
            parent = ".".join(parts[:-1]) or None
            level = len(parts)
        self.clause_id, self.clause_title = cid, title
        self.clauses[cid] = dict(doc_id=self.doc_id, clause_id=cid, level=level, parent_id=parent,
                                 title=title, page_start=page)
        t = title.lower()
        self.mode = ("terms" if "terms and definitions" in t else
                     "abbr" if ("abbreviat" in t or t.startswith("symbols")) else
                     "refs" if "normative references" in t else None)
        self.abbr_x0 = None

    # -- terms -------------------------------------------------------------- #
    def flush_term(self):
        if not self.term_id:
            return
        lines = [x for x in self.term_lines if x]
        term = lines[0] if lines else ""
        rest = lines[1:]
        abbr = ""
        if rest and re.match(r"^[A-Za-z0-9/\-\.]{1,10}$", rest[0]) and re.search(r"[A-Z]", rest[0]):
            abbr, rest = rest[0], rest[1:]
        d, n = [], []
        in_note = False
        for x in rest:
            if re.match(r"^(Note \d+ to entry|EXAMPLE|Source:)", x):
                in_note = True
            (n if in_note else d).append(x)
        definition = " ".join(d)
        self.terms.append(dict(doc_id=self.doc_id, term_id=self.term_id, term=term, abbreviation=abbr,
                               definition=definition, notes=" ".join(n),
                               refs_terms=";".join(CLAUSE_REF_RE.findall(definition)), page=self.term_page))
        self.term_id, self.term_lines = None, []

    # -- satu baris --------------------------------------------------------- #
    def feed(self, L: dict, prev: dict | None):
        text = L["text"]
        gap = (L["top"] - prev["bottom"]) if (prev and prev["page"] == L["page"]) else None
        if gap is None and prev is not None:  # pindah halaman
            para_break = not (text[:1].islower() and prev["text"][-1:] not in ".:;")
        else:
            para_break = gap is not None and gap > 3.0

        # ---- terms: kumpulkan sampai id berikutnya
        if self.mode == "terms":
            m = TERM_ID_RE.match(text)
            if m:
                self.close_block()
                self.flush_term()
                self.term_id, self.term_page = m.group(1), L["page"]
                return
            if self.term_id:
                self.term_lines.append(text)
                return

        # ---- abbreviations
        if self.mode == "abbr" and not text.endswith((":", ".")) and " " in text:
            if self.abbr_x0 is None:
                self.abbr_x0 = L["x0"]
            if abs(L["x0"] - self.abbr_x0) < 10 or not self.abbrs:
                a, m = text.split(" ", 1)
                self.abbrs.append(dict(doc_id=self.doc_id, abbreviation=a, meaning=m.strip(),
                                       clause_id=self.clause_id, page=L["page"]))
            else:
                self.abbrs[-1]["meaning"] += " " + text
            return

        # ---- normative references
        if self.mode == "refs":
            m = REF_START_RE.match(text)
            if m:
                self.refs.append(dict(doc_id=self.doc_id, reference=m.group(1).strip(),
                                      title=m.group(2).strip(), page=L["page"]))
                return
            if self.refs and not para_break:
                self.refs[-1]["title"] += " " + text
                return

        # ---- caption tabel / gambar
        m = CAPTION_RE.match(text)
        if m:
            self.close_block()
            self.captions.append(dict(kind=m.group(1), number=m.group(2), caption=m.group(3).strip(),
                                      page=L["page"], top=L["top"], clause_id=self.clause_id))
            self._cap_open = True
            return
        if getattr(self, "_cap_open", False):
            if not para_break and self.captions and self.captions[-1]["page"] == L["page"] \
                    and not REQ_RE.match(text):
                self.captions[-1]["caption"] += " " + text
                return
            self._cap_open = False

        # ---- requirement
        m = REQ_RE.match(text)
        if m:
            self.open_block("requirement", m.group(2), L, req_id=m.group(1))
            return
        if self.cur and self.cur["block_type"] == "requirement":
            if L["x0"] > self.cur["_x0"] + 20 and not NOTE_RE.match(text):
                self.cur["text"] += " " + text
                return

        # ---- note / example / list item
        m = NOTE_RE.match(text)
        if m:
            self.open_block("note", m.group(1), L)
            return
        m = EXAMPLE_RE.match(text)
        if m:
            self.open_block("example", m.group(1), L)
            return
        m = LISTITEM_RE.match(text)
        if m and (para_break or not self.cur or self.cur["block_type"] != "list_item"):
            self.open_block("list_item", m.group(1), L)
            return

        # ---- lanjutan paragraf / blok sebelumnya
        if self.cur and self.cur["block_type"] in ("paragraph", "note", "example", "list_item") \
                and not para_break:
            self.cur["text"] += " " + text
            return
        self.open_block("paragraph", text, L)


# --------------------------------------------------------------------------- #
# Tabel
# --------------------------------------------------------------------------- #
def _tables_to_frames(doc_id, page_tables, captions, pages):
    index, long_rows, frames = [], [], {}
    prev = None  # untuk tabel yang berlanjut ke halaman berikutnya
    seq = 0
    for pno in pages:
        caps = sorted([c for c in captions if c["page"] == pno and c["kind"] == "Table"], key=lambda c: c["top"])
        for t in page_tables.get(pno, []):
            rows = [[clean(c or "").replace("\n", " ") for c in r] for r in t.extract()]
            rows = [r for r in rows if any(r)]
            if not rows:
                continue
            above = [c for c in caps if c["top"] <= t.bbox[1] + 2]
            cap = above[-1] if above else None
            if cap:
                caps.remove(cap)
            if cap is None and prev and t.bbox[1] < 140 and len(rows[0]) == prev["ncols"]:
                # lanjutan tabel sebelumnya
                if rows[0] == prev["header"]:
                    rows = rows[1:]
                prev["rows"].extend(rows)
                prev["pages"].append(pno)
                continue
            seq += 1
            tid = f"Table {cap['number']}" if cap else f"Table_p{pno}_{seq}"
            prev = dict(table_id=tid, caption=cap["caption"] if cap else "", header=rows[0],
                        rows=rows[1:], ncols=len(rows[0]), pages=[pno])
            index.append(prev)
    idx_rows = []
    for t in index:
        hdr = [h or f"col{i+1}" for i, h in enumerate(t["header"])]
        # nama kolom unik
        seen, cols = Counter(), []
        for h in hdr:
            seen[h] += 1
            cols.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
        df = pd.DataFrame([r + [""] * (len(cols) - len(r)) for r in t["rows"]], columns=cols)
        df.insert(0, "table_id", t["table_id"])
        df.insert(0, "doc_id", doc_id)
        frames[t["table_id"]] = df
        idx_rows.append(dict(doc_id=doc_id, table_id=t["table_id"], caption=t["caption"],
                             page_start=t["pages"][0], page_end=t["pages"][-1],
                             n_rows=len(t["rows"]), n_cols=t["ncols"], columns=" | ".join(cols)))
        for ri, r in enumerate(t["rows"], 1):
            for ci, v in enumerate(r):
                long_rows.append(dict(doc_id=doc_id, table_id=t["table_id"], row=ri, col=cols[ci] if ci < len(cols) else f"col{ci+1}",
                                      value=v))
    return pd.DataFrame(idx_rows), pd.DataFrame(long_rows), frames


# --------------------------------------------------------------------------- #
# Kata kunci (gaya Biblioshiny: word frequency untuk TreeMap / word cloud)
# --------------------------------------------------------------------------- #
def keyword_frequency(texts: list[str], doc_id: str, top_n: int = 300):
    uni, bi = Counter(), Counter()
    for t in texts:
        toks = [w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", t.lower())]
        words = [w for w in toks if w not in STOPWORDS]
        uni.update(words)
        bi.update(f"{a} {b}" for a, b in zip(toks, toks[1:]) if a not in STOPWORDS and b not in STOPWORDS)

    def mk(c, col):
        tot = sum(c.values()) or 1
        return pd.DataFrame([dict(doc_id=doc_id, **{col: w}, n=n, pct=round(100 * n / tot, 3))
                             for w, n in c.most_common(top_n)])
    return mk(uni, "word"), mk(bi, "bigram")


# --------------------------------------------------------------------------- #
# API utama
# --------------------------------------------------------------------------- #
def parse_iso_pdf(file: str | BinaryIO, source_name: str = "", first_page: int | None = None,
                  last_page: int | None = None, strip_margins: bool = True,
                  skip_front_matter: bool = True) -> dict[str, pd.DataFrame]:
    with pdfplumber.open(file) as pdf:
        n = len(pdf.pages)
        pages = list(range(first_page or 1, (last_page or n) + 1))
        page_lines, page_tables = _collect(pdf, pages, strip_margins)

        first_text = " ".join(l["text"] for l in page_lines[pages[0]][:15])
        m = re.search(r"((?:ISO|IEC)(?:/IEC)?\s*\d+(?:-\d+)?(?::\d{4})?)", first_text)
        doc_id = (m.group(1) if m else (source_name or "document")).replace("  ", " ")

        start = _find_body_start(page_lines, pages) if (skip_front_matter and not first_page) else pages[0]
        body_pages = [p for p in pages if p >= start]

        flat = [l for p in body_pages for l in page_lines[p]]
        b = _Builder(doc_id)
        prev, i = None, 0
        while i < len(flat):
            L = flat[i]
            t = L["text"]

            m_annex = ANNEX_RE.match(t)
            if m_annex:  # "Annex A" / "(informative)" / judul
                j, kind = i + 1, ""
                if j < len(flat) and ANNEX_KIND_RE.match(flat[j]["text"]):
                    kind = flat[j]["text"].strip("()").lower()
                    j += 1
                title = []
                while j < len(flat) and flat[j]["bold"] and not HEADING_RE.match(flat[j]["text"]) \
                        and (not title or flat[j]["top"] - flat[j - 1]["bottom"] < 6):
                    title.append(flat[j]["text"])
                    j += 1
                cid = f"Annex {m_annex.group(1)}"
                b.new_clause(cid, " ".join(title) + (f" ({kind})" if kind else ""), L["page"])
                prev, i = flat[j - 1], j
                continue

            if t in UNNUMBERED_HEADINGS and L["bold"]:
                b.new_clause(t, t, L["page"])
                prev, i = L, i + 1
                continue

            mh = HEADING_RE.match(t)
            if (mh and mh.group(2)[0].isupper() and not t.endswith((".", ",", ";", ":"))
                    and len(t) <= 120 and (L["bold"] or "." in mh.group(1))
                    and not (b.cur and b.cur["block_type"] == "requirement" and L["x0"] > b.cur["_x0"] + 20)):
                b.new_clause(mh.group(1), mh.group(2).strip(), L["page"])
                prev, i = L, i + 1
                continue

            b.feed(L, prev)
            prev, i = L, i + 1
        b.flush_term()
        b.close_block()

        # ---- DataFrame ------------------------------------------------------
        blocks = pd.DataFrame([{k: v for k, v in x.items() if not k.startswith("_")} for x in b.blocks])
        blocks.insert(1, "block_id", range(1, len(blocks) + 1))
        blocks["n_words"] = blocks["text"].str.split().str.len()

        clauses = pd.DataFrame(b.clauses.values())
        if len(clauses):
            agg = blocks[blocks.block_type != "note"].groupby("clause_id")["text"].apply(" ".join)
            clauses["text"] = clauses["clause_id"].map(agg).fillna("")
            clauses["n_requirements"] = clauses["clause_id"].map(
                blocks[blocks.block_type == "requirement"].groupby("clause_id").size()).fillna(0).astype(int)
            clauses["n_notes"] = clauses["clause_id"].map(
                blocks[blocks.block_type == "note"].groupby("clause_id").size()).fillna(0).astype(int)

        req = blocks[blocks.block_type == "requirement"].copy()
        req["modality"] = req["text"].apply(lambda s: (MODAL_RE.search(s).group(1).lower() if MODAL_RE.search(s) else ""))
        req["refs_requirements"] = [";".join(sorted(set(REQ_REF_RE.findall(t)) - {rid}))
                                    for t, rid in zip(req["text"], req["req_id"])]
        req["refs_tables_figures"] = req["text"].apply(lambda s: ";".join(sorted(set(TABLE_REF_RE.findall(s)))))
        req = req[["doc_id", "req_id", "clause_id", "clause_title", "text", "modality",
                   "refs_requirements", "refs_tables_figures", "page"]]

        tbl_index, tbl_long, tbl_frames = _tables_to_frames(doc_id, page_tables, b.captions, body_pages)
        figs = pd.DataFrame([dict(doc_id=doc_id, figure_id=f"Figure {c['number']}", caption=c["caption"],
                                  clause_id=c["clause_id"], page=c["page"])
                             for c in b.captions if c["kind"] == "Figure"])

        kw_uni, kw_bi = keyword_frequency(blocks["text"].tolist(), doc_id)
        meta = pd.DataFrame([dict(doc_id=doc_id, source_file=source_name, pdf_pages=n,
                                  body_start_page=start, n_clauses=len(clauses), n_blocks=len(blocks),
                                  n_requirements=len(req), n_terms=len(b.terms), n_tables=len(tbl_index),
                                  n_figures=len(figs))])

    out = {
        "document_info": meta, "clauses": clauses, "blocks": blocks, "requirements": req,
        "terms": pd.DataFrame(b.terms), "abbreviations": pd.DataFrame(b.abbrs),
        "normative_references": pd.DataFrame(b.refs), "tables_index": tbl_index,
        "tables_long": tbl_long, "figures": figs, "keywords_unigram": kw_uni, "keywords_bigram": kw_bi,
    }
    out["_tables"] = tbl_frames  # dict[str, DataFrame] -> satu CSV per tabel
    return out
