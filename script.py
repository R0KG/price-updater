import streamlit as st
import fitz  # PyMuPDF
import re
import io

def extract_prices_from_page(page, page_num):
    """
    Scans a page for price patterns and returns their coordinates and values.
    """
    # This Regex looks for:
    # 1. Optional "Стоимость" or "Стоимость -" (captured as 'prefix')
    # 2. Numbers (including spaces like 35 000) (captured as 'value')
    # 3. The Euro symbol (captured as 'currency')
    price_pattern = re.compile(r"(?P<prefix>Стоимость\s*[-–]?\s*)?(?P<value>\d{1,3}(?:[\. ]\d{3})*|\d+)\s*(?P<currency>€)", re.IGNORECASE)
    
    found_items = []
    blocks = page.get_text("dict")["blocks"]
    
    for b in blocks:
        if "lines" in b:
            for line in b["lines"]:
                for span in line["spans"]:
                    found_items.extend(process_span(span, page_num, price_pattern))
    return found_items

def process_span(span, page_num, price_pattern):
    """
    Scans a text span for price matches and returns extracted items.
    """
    found = []
    text = span["text"]
    matches = price_pattern.finditer(text)
    for match in matches:
        found.append({
            "page": page_num + 1,
            "original_text": match.group(0),
            "prefix": match.group("prefix") or "",
            "value": match.group("value"),
            "currency": match.group("currency"),
            "bbox": span["bbox"],
            "origin": span["origin"],
            "font_size": span["size"],
            "color": span["color"]
        })
    return found

def apply_markup(text, original_prefix, original_currency, multiplier=1.05):
    """
    Parses a number from text, applies a markup, and formats it back.
    """
    # Fix: Added (?!\d) to ensure we don't match 160 inside 1600
    val_match = re.search(r"(\d{1,3}(?:[\. ]\d{3})*(?!\d)|\d+)", text)
    if not val_match:
        return text
    
    raw_val = val_match.group(1).replace(" ", "").replace(".", "")
    try:
        final_val = int(round(float(raw_val) * multiplier))
        formatted_val = f"{final_val:,}".replace(",", " ")
        return f"{original_prefix}{formatted_val} {original_currency}"
    except ValueError:
        return text

def main():
    st.set_page_config(layout="wide")
    st.title("Обновление цен в PDF")
    st.write("Загрузите каталог, отредактируйте цены в таблице и скачайте результат.")

    uploaded_file = st.file_uploader("Загрузить PDF", type="pdf")

    if uploaded_file is not None:
        # Load PDF
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        
        all_prices = []
        
        # 1. Extraction Phase
        with st.spinner("Сканирование PDF на наличие цен..."):
            for i, page in enumerate(doc):
                items = extract_prices_from_page(page, i)
                all_prices.extend(items)
        
        if not all_prices:
            st.warning("Цены не найдены (формат: '35 000 €').")
            return

        # Display editable data editor
        st.subheader(f"Найдено {len(all_prices)} ценников")
        
        markup_percent = st.number_input(
            "Процент наценки (%)", 
            min_value=0.0, 
            value=5.0, 
            step=0.1,
            format="%.1f"
        )
        markup_multiplier = 1 + (markup_percent / 100)

        # Create a container to hold editable data
        data_for_df = []
        for idx, item in enumerate(all_prices):
            # Pre-calculate the new price with custom markup
            new_price_text = apply_markup(
                item["original_text"], 
                item["prefix"], 
                item["currency"], 
                multiplier=markup_multiplier
            )
            data_for_df.append({
                "ID": idx,
                "Page": item["page"],
                "Original Text": item["original_text"],
                "New Text": new_price_text
            })
            
        # Display editable data editor
        edited_rows = st.data_editor(
            data_for_df,
            column_config={
                "ID": st.column_config.NumberColumn(disabled=True),
                "Page": st.column_config.NumberColumn("Страница", disabled=True),
                "Original Text": st.column_config.TextColumn("Исходный текст", disabled=True),
                "New Text": st.column_config.TextColumn("Новый текст", help="Отредактируйте это значение")
            },
            hide_index=True,
            width="stretch"
        )

        # 3. Generation Phase
        if st.button("Создать обновленный PDF"):
            # Create a clean copy for modification
            uploaded_file.seek(0)
            doc_new = fitz.open(stream=uploaded_file.read(), filetype="pdf")
            
            # Iterate through edits - PASS 1: Whiteout (Cleaning)
            for row in edited_rows:
                original_idx = row["ID"]
                new_text_val = row["New Text"]
                original_item = all_prices[original_idx]
                
                # Only process if text changed
                if new_text_val != original_item["original_text"]:
                    page = doc_new[original_item["page"] - 1]
                    rect = fitz.Rect(original_item["bbox"])
                    
                    # A. "Whiteout" the old text
                    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)

            # Iterate through edits - PASS 2: Insert New Text
            for row in edited_rows:
                original_idx = row["ID"]
                new_text_val = row["New Text"]
                original_item = all_prices[original_idx]
                
                if new_text_val != original_item["original_text"]:
                    page = doc_new[original_item["page"] - 1]
                    rect = fitz.Rect(original_item["bbox"])

                    # Use multiplier=1.0 because the markup is already applied in the UI
                    display_text = apply_markup(
                        new_text_val, 
                        original_item['prefix'], 
                        original_item['currency'],
                        multiplier=1.0
                    )

                    # B. Insert new text
                    # Use bundled font to ensure Euro symbol support across platforms (macOS/Linux/Cloud)
                    font_name = "dejavu"
                    font_path = "DejaVuSans.ttf"
                    
                    if font_name not in page.get_fonts():
                        # Try loading local font file
                        try:
                            # Use language="en" (Latin) but DejaVu covers much more
                            page.insert_font(fontname=font_name, fontfile=font_path, fontbuffer=None)
                        except Exception:
                            # Fallback if font file missing (shouldn't happen if deployed correctly)
                            font_name = "helv"

                    page.insert_text(
                        (rect.x0, original_item["origin"][1]), 
                        display_text, 
                        fontsize=original_item["font_size"],
                        fontname=font_name,
                        color=0  # Black
                    )
            
            # Save to buffer
            output_buffer = io.BytesIO()
            doc_new.save(output_buffer)
            doc_new.close()
            
            st.success("PDF обработан!")
            st.download_button(
                label="Скачать обновленный PDF",
                data=output_buffer.getvalue(),
                file_name="Updated_Catalog.pdf",
                mime="application/pdf"
            )

if __name__ == "__main__":
    main()