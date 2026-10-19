import streamlit as st
import pandas as pd
import io

# Konfigurasi Halaman
st.set_page_config(page_title="Konverter Standar ke Biblioshiny", layout="wide")
st.title("🔌 Konverter Data Standar EV ke CSV R-Studio (Biblioshiny)")
st.write("Aplikasi ini membantu memformat daftar dokumen standar (ISO/IEC/UNECE) agar kolomnya sesuai dengan format Scopus dan bisa dianalisis di R-Studio.")

# Inisialisasi DataFrame kosong di Session State
if 'df_standar' not in st.session_state:
    st.session_state.df_standar = pd.DataFrame(columns=['TI', 'AB', 'PY', 'SO', 'AU', 'DT', 'DE'])

# Membuat Tab untuk 2 metode input
tab1, tab2 = st.tabs(["✍️ Input Manual", "📂 Upload File Excel/CSV (Otomatis)"])

# --- TAB 1: INPUT MANUAL ---
with tab1:
    st.markdown("### Tambahkan Data Secara Manual")
    with st.form("form_manual"):
        col1, col2 = st.columns(2)
        with col1:
            ti = st.text_input("Judul Standar (TI) *", placeholder="Cth: ISO 15118-1: Road vehicles...")
            so = st.text_input("Organisasi/Penerbit (SO)", placeholder="Cth: ISO")
            py = st.text_input("Tahun Terbit (PY)", placeholder="Cth: 2023")
        with col2:
            au = st.text_input("Komite Teknis (AU)", placeholder="Cth: ISO/TC 22")
            de = st.text_input("Kata Kunci (DE)", placeholder="Pisahkan dengan titik koma (;). Cth: EV; Battery")
            dt = st.text_input("Tipe Dokumen (DT)", value="Standard", disabled=True)
        
        ab = st.text_area("Ruang Lingkup / Scope (AB) *", placeholder="Copy-paste paragraf scope standar di sini. R-Studio akan membaca teks ini...", height=150)
        
        submit_btn = st.form_submit_button("Tambahkan ke Tabel")
        
        if submit_btn:
            if ti and ab:
                new_data = {'TI': ti, 'AB': ab, 'PY': py, 'SO': so, 'AU': au, 'DT': 'Standard', 'DE': de}
                st.session_state.df_standar = pd.concat([st.session_state.df_standar, pd.DataFrame([new_data])], ignore_index=True)
                st.success(f"Standar '{ti}' berhasil ditambahkan!")
            else:
                st.error("Judul (TI) dan Ruang Lingkup (AB) wajib diisi!")

# --- TAB 2: UPLOAD EXCEL/CSV ---
with tab2:
    st.markdown("### Upload Data yang Sudah Ada")
    st.write("Jika Anda sudah punya daftar judul di Excel, upload di sini. Pastikan file Anda memiliki kolom untuk Judul, Scope, Tahun, dll.")
    uploaded_file = st.file_uploader("Upload Excel (.xlsx) atau CSV", type=["xlsx", "csv"])
    
    if uploaded_file:
        try:
            if uploaded_file.name.endswith('.csv'):
                df_upload = pd.read_csv(uploaded_file)
            else:
                df_upload = pd.read_excel(uploaded_file)
            
            st.write("Preview Data Anda:")
            st.dataframe(df_upload.head(3))
            
            st.markdown("#### Petakan Kolom (Mapping)")
            st.write("Pilih nama kolom di file Anda yang sesuai dengan kode Scopus:")
            
            col_options = ["(Kosong)"] + list(df_upload.columns)
            
            map_ti = st.selectbox("Kolom untuk Judul (TI):", col_options)
            map_ab = st.selectbox("Kolom untuk Scope/Abstrak (AB):", col_options)
            map_py = st.selectbox("Kolom untuk Tahun (PY):", col_options)
            map_so = st.selectbox("Kolom untuk Penerbit (SO):", col_options)
            
            if st.button("Proses dan Gabungkan Data"):
                temp_df = pd.DataFrame()
                if map_ti != "(Kosong)": temp_df['TI'] = df_upload[map_ti]
                if map_ab != "(Kosong)": temp_df['AB'] = df_upload[map_ab]
                if map_py != "(Kosong)": temp_df['PY'] = df_upload[map_py]
                if map_so != "(Kosong)": temp_df['SO'] = df_upload[map_so]
                temp_df['DT'] = "Standard"
                
                # Isi kolom yang tidak dipetakan dengan NaN
                for col in ['TI', 'AB', 'PY', 'SO', 'AU', 'DE']:
                    if col not in temp_df.columns:
                        temp_df[col] = ""
                        
                st.session_state.df_standar = pd.concat([st.session_state.df_standar, temp_df], ignore_index=True)
                st.success("Data berhasil diproses dan digabungkan!")
        except Exception as e:
            st.error(f"Terjadi kesalahan saat membaca file: {e}")

# --- TAMPILAN TABEL AKHIR & DOWNLOAD ---
st.divider()
st.markdown("### 📊 Tabel Hasil Akhir")
st.write("Data di bawah ini sudah siap diunduh dan dimasukkan ke R-Studio.")

# Tombol untuk mereset data
if st.button("Hapus Semua Data"):
    st.session_state.df_standar = pd.DataFrame(columns=['TI', 'AB', 'PY', 'SO', 'AU', 'DT', 'DE'])
    st.rerun()

st.dataframe(st.session_state.df_standar)

# Logika untuk Download CSV
if not st.session_state.df_standar.empty:
    csv = st.session_state.df_standar.to_csv(index=False, encoding='utf-8')
    st.download_button(
        label="📥 Download CSV untuk Biblioshiny",
        data=csv,
        file_name='Standar_EV_Biblioshiny.csv',
        mime='text/csv'
    )
else:
    st.info("Tabel masih kosong. Silakan isi data di atas terlebih dahulu untuk memunculkan tombol Download.")
