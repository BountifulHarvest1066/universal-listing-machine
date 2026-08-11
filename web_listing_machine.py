import streamlit as st
import csv
import re
import os
import json
import time
import io
import google.generativeai as genai

# --- Settings Management ---
CONFIG_FILE = "efds_aw_settings.json"

def save_settings(api_key, brand, f_prefix, s_prefix, qty, margin):
    data = {
        "api_key": api_key,
        "brand": brand,
        "f_prefix": f_prefix,
        "s_prefix": s_prefix,
        "qty": qty,
        "margin": margin
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f)
    except Exception: 
        pass

def load_settings():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception: 
            pass
    return {}

# --- Deterministic Data Handlers ---
COUNTRY_MAP = {
    'CHN': 'China', 'IND': 'India', 'NPL': 'Nepal', 'IDN': 'Indonesia', 
    'GBR': 'United Kingdom', 'UK': 'United Kingdom', 'USA': 'United States', 
    'MAR': 'Morocco', 'ESP': 'Spain', 'ITA': 'Italy', 'FRA': 'France', 
    'DEU': 'Germany', 'THA': 'Thailand', 'VNM': 'Vietnam', 'TUR': 'Turkey'
}

def get_full_country_name(code):
    return COUNTRY_MAP.get(str(code).upper().strip(), str(code).strip())

def extract_pattern(unit_name, max_len):
    if not unit_name: return "Item"
    clean_name = re.sub(r'\b\d+x\d+cm\b', '', unit_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\b\d+g\b', '', clean_name, flags=re.IGNORECASE)
    parts = [p.strip() for p in re.split(r'[-|]', clean_name) if p.strip()]
    target = parts[1] if len(parts) > 1 else parts[0]
    words = re.findall(r'[A-Za-z]+', target)[:3]
    if not words: return "Item"
    full_pattern = "".join(w.capitalize() for w in words)
    if len(full_pattern) <= max_len: return full_pattern
    chars_per_word = max(2, max_len // len(words))
    abbreviated = "".join(w[:chars_per_word].capitalize() for w in words)
    return abbreviated[:max_len]

def parse_dims(dim_str):
    if not dim_str: return "", "", "", "centimeters"
    dim_str = str(dim_str).replace(" ", "")
    match_3 = re.search(r'([\d.]+)[xX*]([\d.]+)[xX*]([\d.]+)', dim_str)
    match_2 = re.search(r'([\d.]+)[xX*]([\d.]+)', dim_str)
    l, w, h = "", "", ""
    if match_3: l, w, h = match_3.groups()
    elif match_2:
        l, w = match_2.groups()
        h = w 
    return l, w, h, "centimeters"

def to_grams(weight_str):
    try: return str(int(float(weight_str) * 1000))
    except: return weight_str

def clean_json_response(response_text):
    cleaned = response_text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)

# --- Web UI ---
st.set_page_config(page_title="Universal Listing Machine", layout="wide")
st.title("Universal Listing Machine")

# Load existing settings
settings = load_settings()

# Sidebar for Settings
with st.sidebar:
    st.header("Configuration")
    
    with st.form("settings_form"):
        api_key = st.text_input("Gemini API Key", value=settings.get("api_key", ""), type="password")
        brand = st.text_input("Brand", value=settings.get("brand", ""))
        f_prefix = st.text_input("F-Prefix", value=settings.get("f_prefix", ""))
        s_prefix = st.text_input("S-Prefix", value=settings.get("s_prefix", ""))
        qty = st.number_input("Default Quantity", value=settings.get("qty", 1), min_value=1)
        margin = st.number_input("Margin", value=settings.get("margin", 0.0), format="%.2f")
        
        if st.form_submit_button("Save Settings"):
            save_settings(api_key, brand, f_prefix, s_prefix, qty, margin)
            st.success("Settings saved successfully!")

# Main interface for processing
st.write("Upload your supplier CSV to generate Amazon.co.uk formatted listings.")
uploaded_file = st.file_uploader("Upload Supplier CSV", type=['csv'])

if st.button("Process Listings", type="primary"):
    if not uploaded_file:
        st.warning("Please upload a CSV file first.")
    elif not settings.get("api_key"):
        st.warning("Please save your Gemini API key in the sidebar.")
    else:
        st.info("Starting processing...")
        
        # Setup Gemini
        genai.configure(api_key=settings.get("api_key"))
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Read the uploaded CSV
        csv_text = uploaded_file.getvalue().decode("utf-8").splitlines()
        reader = csv.DictReader(csv_text)
        fieldnames = reader.fieldnames
        
        if not fieldnames:
            st.error("Could not read columns from the CSV.")
            st.stop()
            
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = []
        rows = list(reader)
        total_rows = len(rows)
        
        # Main Processing Loop
        for index, row in enumerate(rows):
            status_text.text(f"Processing item {index + 1} of {total_rows}...")
            
            prompt = f"""
            Generate an Amazon product listing based on this supplier data: {json.dumps(row)}
            
            Strict Constraints:
            - Exclusively use British English.
            - Adhere strictly to Amazon.co.uk listing policies.
            - Do not include prohibited promotional, subjective, or seasonal phrases in the title or bullets (e.g., no "Christmas Gift", "Best Quality", "Summer Sale").
            - The item belongs to the Brand: {settings.get('brand')}
            
            Return ONLY a valid JSON object with the following exact keys: item_name, product_description, bullet_points, search_terms.
            """
            
            try:
                response = model.generate_content(prompt)
                listing_data = clean_json_response(response.text)
                
                # Merge the new Amazon data with the original supplier row
                combined_row = {**row, **listing_data}
                results.append(combined_row)
            except Exception as e:
                st.error(f"Failed to process row {index + 1}: {e}")
                results.append(row) 
            
            progress_bar.progress((index + 1) / total_rows)
            time.sleep(1) # Simple rate limiting to prevent API blocks
            
        status_text.success("Processing complete!")
        
        # Output the finished file
        if results:
            output = io.StringIO()
            new_fields = ["item_name", "product_description", "bullet_points", "search_terms"]
            all_fields = fieldnames + [f for f in new_fields if f not in fieldnames]
            
            writer = csv.DictWriter(output, fieldnames=all_fields)
            writer.writeheader()
            writer.writerows(results)
            
            st.download_button(
                label="Download Amazon Flat File",
                data=output.getvalue().encode('utf-8'),
                file_name="amazon_listings_ready.csv",
                mime="text/csv"
            )