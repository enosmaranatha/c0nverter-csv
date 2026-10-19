"""
app.py  -  Streamlit: Konversi dokumen standar ISO (PDF) -> CSV untuk R / RStudio

Jalankan:
    pip install -r requirements.txt
    streamlit run app.py
"""
import io
import re
import zipfile

import pandas as pd
import streamlit as st

from iso_parser import parse_iso_pdf

st.set_page_config(page_title="ISO PDF → CSV", page_icon="📄", layout="wide")
st.title("📄 ISO Standard PDF → CSV (siap untuk R / RStudio)")
st.caption("Unggah satu atau beberapa PDF standar ISO (PDF harus punya text layer, bukan hasil scan).")

DESCRIPTIONS = {
    "document_info": "Metadata dokumen & ringkasan jumlah elemen",
    "clauses": "Struktur klausul (id, level, parent, judul, teks gabungan)",
    "blocks": "Semua blok teks: paragraph / requirement / note / example / list_item",
    "requirements": "Requirement berkode [XXX-000] + modalitas (shall/should/may/can) + referensi silang",
    "terms": "Istilah & definisi (klausul 3)",
    "abbreviations": "Singkatan (klausul 4)",
    "normative_references": "Rujukan normatif (klausul 2)",
    "tables_index": "Daftar tabel (id, caption, halaman, kolom)",
    "tables_long": "Isi semua tabel format panjang (tidy): table_id, row, col, value",
    "figures": "Daftar gambar & caption",
    "keywords_unigram": "Frekuensi kata (untuk word cloud / TreeMap di R)",
    "keywords_bigram": "Frekuensi frasa dua kata (untuk co-occurrence / network di R)",
}


@st.cache_data(show_spinner=False)
def run_parser(data: bytes, name: str, first, last, strip, skip):
    return parse_iso_pdf(io.BytesIO(data), name, first_page=first, last_page=last,
                         strip_margins=strip, skip_front_matter=skip)


def to_csv_bytes(df: pd.DataFrame, bom: bool) -> bytes:
    return df.to_csv(index=False, encoding="utf-8-sig" if bom else "utf-8").encode(
        "utf-8-sig" if bom else "utf-8")


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")


with st.sidebar:
    st.header("Opsi")
    strip = st.checkbox("Hapus header/footer berulang", True)
    skip = st.checkbox("Lewati front matter (cover, daftar isi, foreword)", True,
                       help="Parsing dimulai dari halaman yang memuat '1 Scope'.")
    use_range = st.checkbox("Batasi rentang halaman PDF", False)
    first = last = None
    if use_range:
        first = st.number_input("Halaman awal", 1, 2000, 1)
        last = st.number_input("Halaman akhir", 1, 2000, 50)
    bom = st.checkbox("Encoding UTF-8 dengan BOM (untuk dibuka di Excel)", False,
                      help="Untuk R cukup UTF-8 biasa: read_csv() / read.csv(fileEncoding='UTF-8').")
    per_table = st.checkbox("Sertakan 1 CSV per tabel di ZIP", True)

files = st.file_uploader("Pilih PDF standar ISO", type="pdf", accept_multiple_files=True)

if not files:
    st.info("Belum ada file. Contoh: ISO 15118-8:2020.")
    st.stop()

all_results, combined = {}, {}
for f in files:
    with st.spinner(f"Memproses {f.name} ..."):
        try:
            res = run_parser(f.getvalue(), f.name, first, last, strip, skip)
        except Exception as e:  # noqa: BLE001
            st.error(f"Gagal memproses {f.name}: {e}")
            continue
    all_results[f.name] = res
    for k, df in res.items():
        if k != "_tables" and len(df):
            combined.setdefault(k, []).append(df)

if not all_results:
    st.stop()

# ----------------------------------------------------------------------- UI
tabs = st.tabs(["Ringkasan"] + [k for k in DESCRIPTIONS if k != "document_info"])
with tabs[0]:
    info = pd.concat(combined["document_info"], ignore_index=True)
    st.dataframe(info, use_container_width=True)

for tab, key in zip(tabs[1:], [k for k in DESCRIPTIONS if k != "document_info"]):
    with tab:
        st.caption(DESCRIPTIONS[key])
        if key in combined:
            df = pd.concat(combined[key], ignore_index=True)
            st.write(f"{len(df):,} baris × {df.shape[1]} kolom")
            st.dataframe(df, use_container_width=True, height=420)
            st.download_button(f"⬇️ {key}.csv", to_csv_bytes(df, bom), f"{key}.csv", "text/csv", key=f"dl_{key}")
        else:
            st.warning("Tidak ada data terdeteksi untuk bagian ini.")

# ----------------------------------------------------------------------- ZIP
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for key, dfs in combined.items():
        z.writestr(f"{key}.csv", to_csv_bytes(pd.concat(dfs, ignore_index=True), bom))
    if per_table:
        for name, res in all_results.items():
            for tid, tdf in res["_tables"].items():
                z.writestr(f"tables/{safe(res['document_info'].loc[0, 'doc_id'])}_{safe(tid)}.csv",
                           to_csv_bytes(tdf, bom))
    z.writestr("read_in_R.R", """library(readr)
library(dplyr)

dir <- "."   # folder hasil ekstrak ZIP
clauses      <- read_csv(file.path(dir, "clauses.csv"))
requirements <- read_csv(file.path(dir, "requirements.csv"))
terms        <- read_csv(file.path(dir, "terms.csv"))
kw           <- read_csv(file.path(dir, "keywords_unigram.csv"))

requirements %>% count(modality)                       # shall / should / may / can
requirements %>% count(clause_id, sort = TRUE)         # sebaran requirement per klausul
# word cloud / treemap seperti di jurnal Gupta & Kumar (2022):
# wordcloud::wordcloud(kw$word, kw$n, max.words = 100)
""")
st.divider()
st.download_button("📦 Unduh semua CSV (ZIP)", buf.getvalue(), "iso_csv_output.zip", "application/zip",
                   type="primary")

with st.expander("Cara membaca di R / RStudio"):
    st.code('''library(readr)
req <- read_csv("requirements.csv")        # UTF-8
table(req$modality)

# banyak dokumen sekaligus: kolom doc_id sudah ada di setiap CSV, jadi bisa digabung / di-group_by
''', language="r")
