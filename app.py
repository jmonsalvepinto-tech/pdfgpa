import streamlit as st
import os
import io
import zipfile
import csv
import time
from typing import List

from core.pdf_extractor import extract_raw_pdf_data
from core.ocr import apply_ocr_if_needed
from core.cleaner import clean_text_content
from core.structure_detector import extract_metadata_and_structure, format_headings
from core.table_extractor import extract_tables_as_markdown
from core.markdown_writer import generate_markdown_output
from core.txt_writer import generate_txt_output
from core.validator import validate_extraction_quality
from core.token_estimator import estimate_tokens
from ui.components import render_sidebar, render_results_table

st.set_page_config(
    page_title="PDF → AI Knowledge Converter", 
    layout="wide", 
    page_icon="Logo.png"
)

def sanitize_filename(filename: str) -> str:
    """Garantiza compatibilidad con el sistema de archivos de Windows."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")
    return filename

def format_size(size_in_bytes: int) -> str:
    """Convierte bytes a formato legible (KB, MB)."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

def main():
    if os.path.exists("logo.png"):
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image("logo.png", width=300)
    st.title("📄 PDF → AI Knowledge Converter")
    st.caption("Procesamiento local y por lote de documentos PDF a Markdown/TXT optimizados para IA")

    config = render_sidebar()

    uploaded_files = st.file_uploader(
        "Cargar documentos PDF", 
        type=["pdf"], 
        accept_multiple_files=True,
        help="Arrastra o selecciona múltiples archivos PDF para procesar localmente."
    )

    if uploaded_files:
        st.info(f"📁 **Archivos cargados:** {len(uploaded_files)}")
        
        if st.button("🚀 Iniciar Procesamiento en Lote", type="primary"):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            summary_logs = []
            zip_buffer = io.BytesIO()
            csv_buffer = io.StringIO()
            
            csv_writer = csv.writer(csv_buffer)
            csv_writer.writerow([
                "Archivo Original", "Archivo Generado", "Páginas", "Caracteres", 
                "Palabras", "OCR Utilizado", "Advertencias", "Estado", "Tiempo (s)"
            ])

            processed_count = 0
            success_count = 0
            warning_count = 0
            error_count = 0

            # Crear estructura de carpetas local
            out_dir = "output"
            md_dir = os.path.join(out_dir, "markdown")
            txt_dir = os.path.join(out_dir, "txt")
            rep_dir = os.path.join(out_dir, "reportes")

            for d in [md_dir, txt_dir, rep_dir]:
                os.makedirs(d, exist_ok=True)

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for idx, file_obj in enumerate(uploaded_files):
                    start_time = time.time()
                    filename = sanitize_filename(file_obj.name)
                    base_name = os.path.splitext(filename)[0]
                    
                    status_text.text(f"Procesando ({idx+1}/{len(uploaded_files)}): {filename}")
                    
                    try:
                        pdf_bytes = file_obj.read()
                        
                        # 1. Extraer datos primarios
                        pages_data, num_pages = extract_raw_pdf_data(pdf_bytes)
                        
                        # 2. Aplicar OCR si corresponde
                        ocr_pages = 0
                        if config["use_ocr"]:
                            pages_data, ocr_pages = apply_ocr_if_needed(pdf_bytes, pages_data)
                        
                        # 3. Limpieza de contenido
                        cleaned_text = clean_text_content(pages_data, config)
                        
                        # 4. Formatear encabezados/estructuras
                        if config["detect_headings"]:
                            cleaned_text = format_headings(cleaned_text)

                        # 5. Tablas
                        if config["convert_tables"]:
                            tables = extract_tables_as_markdown(pdf_bytes)
                            if tables:
                                cleaned_text += "\n\n### TABLAS DETECTADAS\n" + "\n\n".join(tables)

                        # 6. Metadatos
                        metadata = extract_metadata_and_structure(cleaned_text, filename)
                        metadata["PÁGINAS"] = str(num_pages)

                        # 7. Generar Salidas
                        modes_to_process = ["Fidelidad máxima", "Optimizado para IA"] if config["mode"] == "Ambos" else [config["mode"]]
                        
                        generated_files = []
                        for m in modes_to_process:
                            md_output = generate_markdown_output(metadata, cleaned_text, m)
                            
                            suffix = "_fid" if m == "Fidelidad máxima" and config["mode"] == "Ambos" else ""
                            
                            if config["output_format"] in ["Markdown (.md)", "Ambos"]:
                                md_filename = f"{base_name}{suffix}.md"
                                md_path = os.path.join(md_dir, md_filename)
                                with open(md_path, "w", encoding="utf-8") as f:
                                    f.write(md_output)
                                zip_file.writestr(f"markdown/{md_filename}", md_output)
                                generated_files.append(md_filename)

                            if config["output_format"] in ["TXT (.txt)", "Ambos"]:
                                txt_output = generate_txt_output(md_output)
                                txt_filename = f"{base_name}{suffix}.txt"
                                txt_path = os.path.join(txt_dir, txt_filename)
                                with open(txt_path, "w", encoding="utf-8") as f:
                                    f.write(txt_output)
                                zip_file.writestr(f"txt/{txt_filename}", txt_output)
                                generated_files.append(txt_filename)

                        # 8. Validaciones y Estimación
                        metrics = validate_extraction_quality(num_pages, cleaned_text, ocr_pages)
                        proc_time = round(time.time() - start_time, 2)
                        
                        status = "OK"
                        if metrics["warning_count"] > 0:
                            status = "Advertencia"
                            warning_count += 1
                        else:
                            success_count += 1

                        csv_writer.writerow([
                            filename,
                            ", ".join(generated_files),
                            num_pages,
                            metrics["characters"],
                            metrics["words"],
                            "Sí" if ocr_pages > 0 else "No",
                            metrics["warning_count"],
                            status,
                            proc_time
                        ])

                        summary_logs.append({
                            "Archivo": filename,
                            "Estado": status,
                            "Páginas": num_pages,
                            "Palabras": f"{metrics['words']:,}",
                            "Tokens Est.": f"~{estimate_tokens(cleaned_text):,}",
                            "OCR": "Sí" if ocr_pages > 0 else "No",
                            "Advertencias": metrics["warning_count"]
                        })

                    except Exception as e:
                        error_count += 1
                        st.error(f"❌ ERROR procesando {filename}: {str(e)}")
                        csv_writer.writerow([filename, "N/A", 0, 0, 0, "No", 1, f"Error: {str(e)}", 0])
                        summary_logs.append({
                            "Archivo": filename,
                            "Estado": "Error",
                            "Páginas": 0,
                            "Palabras": "0",
                            "Tokens Est.": "0",
                            "OCR": "No",
                            "Advertencias": 1
                        })

                    processed_count += 1
                    progress_bar.progress(processed_count / len(uploaded_files))

                # Guardar el reporte CSV dentro del ZIP antes de cerrarlo
                csv_content = csv_buffer.getvalue()
                zip_file.writestr("reportes/conversion_log.csv", csv_content)

            # Guardar reporte CSV en el disco local
            with open(os.path.join(rep_dir, "conversion_log.csv"), "w", encoding="utf-8") as f:
                f.write(csv_content)

            status_text.text("¡Procesamiento finalizado!")
            
            # Resumen Ejecutivo
            st.success(f"**Procesados:** {processed_count} | **Exitosos:** {success_count} | **Con advertencias:** {warning_count} | **Errores:** {error_count}")
            
            render_results_table(summary_logs)

            # Descargas
            st.subheader("📥 Descargar Resultados")
            col1, col2 = st.columns(2)
            
            with col1:
                st.download_button(
                    label="📦 Descargar Paquete ZIP Completo",
                    data=zip_buffer.getvalue(),
                    file_name="pdf_knowledge_export.zip",
                    mime="application/zip"
                )
            with col2:
                st.download_button(
                    label="📊 Descargar Reporte CSV",
                    data=csv_content,
                    file_name="conversion_log.csv",
                    mime="text/csv"
                )

if __name__ == "__main__":
    main()