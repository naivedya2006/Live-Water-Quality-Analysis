import streamlit as st
import ee
import folium
import pandas as pd
from streamlit_folium import st_folium
import datetime
import io
import time
import requests
import base64
import json
import urllib3
import plotly.express as px

# --- 1. PAGE CONFIGURATION & WARNING SUPPRESSION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="Water Quality Dashboard", page_icon="🌊", layout="wide")

# --- 2. SESSION STATE MANAGEMENT FOR PHASE 2 ---
if "phase2_show_data" not in st.session_state:
    st.session_state.phase2_show_data = False

def reset_phase2():
    """Callback function to clear Phase 2 screen when inputs change."""
    st.session_state.phase2_show_data = False

# --- 3. PHASE 2 HELPER FUNCTIONS (GLENS API) ---
STATION_MAP = {
    "Amarkantak": "site_3308",
    "Dindori": "site_3309",
    "Jabalpur": "site_3310",
    "Hoshangabad": "site_3311",
    "Omkareshwar": "site_3312",
    "Dharampuri": "site_3313",
    "Ujjain": "site_3305",
    "Indore": "site_3321",
}

def generate_dynamic_payload(station_name, site_id, start_dt, end_dt):
    date_format = "%Y/%m/%d %H:%M:%S"
    payload_dict = {
        "fromDate": start_dt.strftime(date_format),
        "toDate": end_dt.strftime(date_format),
        "siteId": site_id,
        "stations": [station_name],
        "parameters": [
            f"{station_name}-COD", f"{station_name}-BOD", f"{station_name}-TSS", 
            f"{station_name}-Turbidity", f"{station_name}-Color", f"{station_name}-TOC", 
            f"{station_name}-DO", f"{station_name}-Temperature", f"{station_name}-pH"
        ],
        "criteria": "1-hours",
        "reportFormat": "tabular",
        "qualityCode": ["U"],
        "graphType": "singleParameter",
        "quickRange": False,
        "userName": "MPPCB",
        "userId": "userId_3265",
        "userType": "SuperRegulator",
        "userRole": "SuperRegulator",
        "userAccess": "site_2898",
        "domain": "esc.mp.gov.in"
    }
    json_str = json.dumps(payload_dict)
    return base64.b64encode(json_str.encode("utf-8")).decode("utf-8")

@st.cache_data(ttl=300, show_spinner=False) 
def fetch_live_data(station_name, site_id, start_dt_str, end_dt_str):
    url = "https://esc.mp.gov.in/glens/publicPortal/api/v2.0/industry-tabular"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "text/plain",
        "Origin": "https://esc.mp.gov.in",
        "Referer": "https://esc.mp.gov.in/"
    }
    
    start_dt = datetime.datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.datetime.strptime(end_dt_str, "%Y-%m-%d %H:%M:%S")
    
    payload = generate_dynamic_payload(station_name, site_id, start_dt, end_dt)
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, data=payload, verify=False, timeout=(15, 30))
            try:
                return json.loads(base64.b64decode(response.text))
            except Exception:
                return response.json()
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                return {"error": "timeout"}

def process_data(api_data):
    if not api_data or 'parameterDetails' not in api_data:
        return pd.DataFrame()
        
    raw_rows = api_data['parameterDetails'].get('bodyContent', [])
    clean_data = []
    
    for row in raw_rows:
        clean_row = {"Time": pd.to_datetime(row["Time"], format="mixed")}
        for key, value in row.items():
            if key != "Time":
                try:
                    param_name = key.split("-")[1].replace("_U", "")
                    val = value[0] 
                    clean_row[param_name] = float(val) if val != "NA" else None
                except Exception:
                    continue
        clean_data.append(clean_row)
    return pd.DataFrame(clean_data)

def get_latest_valid_metric(df, column_name):
    if column_name in df.columns:
        valid_data = df[column_name].dropna()
        if not valid_data.empty:
            return round(valid_data.iloc[-1], 2)
    return "N/A"

# --- 4. GEE AUTHENTICATION ---
@st.cache_resource
def authenticate_gee():
    try:
        if "gcp_service_account" in st.secrets:
            import google.oauth2.service_account
            creds_dict = dict(st.secrets["gcp_service_account"])
            ee_scopes = ['https://www.googleapis.com/auth/earthengine']
            credentials = google.oauth2.service_account.Credentials.from_service_account_info(
                creds_dict, scopes=ee_scopes
            )
            ee.Initialize(credentials, project='turbidity-chlorophyll-test')
            return True
        else:
            SERVICE_ACCOUNT_FILE = 'credentials.json' 
            SERVICE_ACCOUNT_EMAIL = 'gee-python-auth@turbidity-chlorophyll-test.iam.gserviceaccount.com'
            credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT_EMAIL, SERVICE_ACCOUNT_FILE)
            ee.Initialize(credentials, project='turbidity-chlorophyll-test')
            return True
    except Exception as e:
        st.error(f"❌ Authentication failed. Error: {e}")
        return False

if not authenticate_gee():
    st.stop()
    
# --- 5. APP HEADER ---
st.title("🛰️ Advanced Water Quality Analysis Dashboard")
st.markdown("Monitoring Optically Active Parameters via Sentinel-2 Imagery & Live IoT Validation")

# --- 6. PHASE 1 SIDEBAR (SATELLITE CONTROLS) ---
st.sidebar.header("🌍 Phase 1: Satellite Controls")
st.sidebar.subheader("📅 Select Timeline")
start_date = st.sidebar.date_input("Start Date", datetime.date(2025, 1, 1))
end_date = st.sidebar.date_input("End Date", datetime.date(2026, 1, 1))

start_str = start_date.strftime('%Y-%m-%d')
end_str = end_date.strftime('%Y-%m-%d')

st.sidebar.markdown("---")
st.sidebar.subheader("📍 Data Input Method")
input_method = st.sidebar.radio("How would you like to add locations?", 
                                ["Default Narmada Stations", "Manual Entry", "Upload Excel/CSV"])

pois_data = {}

if input_method == "Default Narmada Stations":
    pois_data = { 
        'Narmada at Amarkantak': [81.7591, 22.6725],
        'Narmada at Mandla': [80.3871, 22.5966],
        'Narmada at Jabalpur': [79.8747, 23.1084],
        'Narmada at Narsinghpur': [79.0224, 23.0295],
        'Narmada at Nemawar': [76.97698, 22.4912],
        'Narmada at Maheshwar': [75.545939, 22.166728]
    }
    st.sidebar.info("Using 6 predefined baseline stations.")

elif input_method == "Manual Entry":
    st.sidebar.write("Enter station details below:")
    default_df = pd.DataFrame([
        {"Location": "Custom Station 1", "Latitude": 22.7196, "Longitude": 75.8577},
        {"Location": "Custom Station 2", "Latitude": 22.7200, "Longitude": 75.8600},
        {"Location": "", "Latitude": None, "Longitude": None},
        {"Location": "", "Latitude": None, "Longitude": None}
    ])
    edited_df = st.sidebar.data_editor(default_df, num_rows="dynamic", hide_index=True)
    
    for index, row in edited_df.iterrows():
        if pd.notna(row['Location']) and str(row['Location']).strip() != "" and pd.notna(row['Latitude']) and pd.notna(row['Longitude']):
            pois_data[str(row['Location'])] = [float(row['Longitude']), float(row['Latitude'])]

elif input_method == "Upload Excel/CSV":
    st.sidebar.write("Upload a file with columns: **Location**, **Latitude**, **Longitude**")
    uploaded_file = st.sidebar.file_uploader("Choose a file", type=['csv', 'xlsx', 'xls'])
    
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'):
                df_upload = pd.read_csv(uploaded_file)
            else:
                df_upload = pd.read_excel(uploaded_file)
            
            req_cols = ['Location', 'Latitude', 'Longitude']
            if all(col in df_upload.columns for col in req_cols):
                for index, row in df_upload.iterrows():
                    if pd.notna(row['Location']) and pd.notna(row['Latitude']) and pd.notna(row['Longitude']):
                        pois_data[str(row['Location'])] = [float(row['Longitude']), float(row['Latitude'])]
                st.sidebar.success(f"✅ Loaded {len(pois_data)} locations!")
            else:
                st.sidebar.error(f"File must contain exactly these columns: {req_cols}")
        except Exception as e:
            st.sidebar.error(f"Error reading file: {e}")

st.sidebar.markdown("---")
st.sidebar.subheader("📊 Select Map Parameters")
selected_params = st.sidebar.multiselect(
    "Choose parameters to display in charts:",
    ['Turbidity (NDTI)', 'Chlorophyll (NDCI)', 'Suspended Solids (Proxy)', 'Color (CDOM)', 'Temperature (°C)'],
    default=['Turbidity (NDTI)', 'Chlorophyll (NDCI)']
)


# --- 7. MAIN TABS ARCHITECTURE ---
tab1, tab2 = st.tabs(["🌍 Direct Optical Analysis (Phase 1)", "🏛️ AI/ML Predictive Analytics & Live Telemetry (Phase 2)"])

# ==========================================
# TAB 1: GOOGLE EARTH ENGINE SATELLITE LOGIC
# ==========================================
with tab1:
    if len(pois_data) == 0:
        st.warning("⚠️ Please provide at least one location via the sidebar to run the analysis.")
    else:
        st.info(f"Ready to analyze **{len(pois_data)}** locations between **{start_str}** and **{end_str}**.")
        
        if st.button("🚀 Run Satellite Analysis", type="primary"):
            with st.spinner("Communicating with Google Earth Engine... Extracting Spectral Signatures..."):
                try:
                    features = [ee.Feature(ee.Geometry.Point(coords), {'Name': name}) for name, coords in pois_data.items()]
                    roi_collection = ee.FeatureCollection(features)

                    dataset_s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                               .filterBounds(roi_collection)
                               .filterDate(start_str, end_str)
                               .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                               .median()) 

                    ndci = dataset_s2.normalizedDifference(['B8', 'B4']).rename('NDCI')
                    ndti = dataset_s2.normalizedDifference(['B4', 'B3']).rename('NDTI')
                    tss_proxy = dataset_s2.expression('b("B4") / b("B3")').rename('TSS')
                    color_cdom = dataset_s2.expression('b("B3") / b("B2")').rename('Color_CDOM')

                    s2_indices = dataset_s2.addBands([ndci, ndti, tss_proxy, color_cdom])
                    sampled_s2 = s2_indices.select(['NDCI', 'NDTI', 'TSS', 'Color_CDOM']).sampleRegions(
                        collection=roi_collection, scale=10, geometries=True
                    ).getInfo() 

                    dataset_l8 = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                               .filterBounds(roi_collection)
                               .filterDate(start_str, end_str)
                               .filter(ee.Filter.lt('CLOUD_COVER', 20))
                               .median())
                    
                    temp_c = dataset_l8.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15).rename('Temp_C')
                    sampled_l8 = temp_c.sampleRegions(collection=roi_collection, scale=30, geometries=True).getInfo()

                    s2_data = {f['properties']['Name']: f['properties'] for f in sampled_s2.get('features', [])}
                    l8_data = {f['properties']['Name']: f['properties'] for f in sampled_l8.get('features', [])}

                    results = []
                    for name in pois_data.keys():
                        s2_props = s2_data.get(name, {})
                        l8_props = l8_data.get(name, {})

                        val_ndti = round(float(s2_props.get('NDTI') or 0.0), 4)
                        val_ndci = round(float(s2_props.get('NDCI') or 0.0), 4)
                        val_tss = round(float(s2_props.get('TSS') or 0.0), 4)
                        val_color = round(float(s2_props.get('Color_CDOM') or 0.0), 4)
                        val_temp = round(float(l8_props.get('Temp_C') or 0.0), 2)
                        
                        if val_ndti > 0.10 or val_ndci > 0.10:
                            status = 'Poor / Critical'
                        elif val_ndti < 0.00 and val_ndci < 0.05:
                            status = 'Good / Safe'
                        else:
                            status = 'Moderate / At Risk'
                            
                        results.append({
                            'Location': name, 
                            'Turbidity (NDTI)': val_ndti, 
                            'Chlorophyll (NDCI)': val_ndci, 
                            'Suspended Solids (Proxy)': val_tss,
                            'Color (CDOM)': val_color,
                            'Temperature (°C)': val_temp,
                            'Quality Status': status
                        })

                    df = pd.DataFrame(results)
                    numeric_cols = ['Turbidity (NDTI)', 'Chlorophyll (NDCI)', 'Suspended Solids (Proxy)', 'Color (CDOM)', 'Temperature (°C)']
                    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0.0)

                    st.success("✅ Analysis Complete!")
                    
                    col_map, col_data = st.columns([1.5, 1])
                    
                    with col_map:
                        st.subheader("QGIS Multi-Layer Map (Takes time to render)")
                        st.markdown("*Use the **Layers icon** in the top right corner of the map to toggle Turbidity, Chlorophyll, or True Color on and off.*")
                        
                        def add_ee_layer(self, ee_image_object, vis_params, name, show=True, opacity=1.0):
                            try:
                                map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
                                folium.raster_layers.TileLayer(
                                    tiles=map_id_dict['tile_fetcher'].url_format,
                                    attr='Google Earth Engine',
                                    name=name,
                                    overlay=True,
                                    control=True,
                                    show=show,
                                    opacity=opacity
                                ).add_to(self)
                            except Exception:
                                pass

                        folium.Map.add_ee_layer = add_ee_layer
                        
                        first_point = list(pois_data.values())[0]
                        Map = folium.Map(location=[first_point[1], first_point[0]], zoom_start=8)
                        
                        Map.add_ee_layer(dataset_s2.select(['B4', 'B3', 'B2']), {'min': 0, 'max': 3000}, '🛰️ Sentinel-2 True Color (RGB)', show=False, opacity=0.8)

                        ndti_palette = ['darkblue', 'blue', 'cyan', 'yellow', 'orange', 'saddlebrown']
                        Map.add_ee_layer(s2_indices.select('NDTI'), {'min': -0.1, 'max': 0.2, 'palette': ndti_palette}, '🟤 Turbidity (NDTI) Map', show=True, opacity=0.6)

                        ndci_palette = ['darkblue', 'blue', 'cyan', 'lime', 'yellow', 'red']
                        Map.add_ee_layer(s2_indices.select('NDCI'), {'min': -0.1, 'max': 0.2, 'palette': ndci_palette}, '🟢 Chlorophyll (NDCI) Map', show=False, opacity=0.6)

                        for index, row in df.iterrows():
                            lat, lon = pois_data[row['Location']][1], pois_data[row['Location']][0]
                            marker_color = 'red' if row['Quality Status'] == 'Poor / Critical' else 'orange' if row['Quality Status'] == 'Moderate / At Risk' else 'green'
                            
                            popup_text = f"<b>{row['Location']}</b><br>Status: {row['Quality Status']}<br>Chlorophyll: {row['Chlorophyll (NDCI)']}<br>Turbidity: {row['Turbidity (NDTI)']}"
                            folium.Marker(location=[lat, lon], popup=folium.Popup(popup_text, max_width=300), icon=folium.Icon(color=marker_color)).add_to(Map)
                        
                        folium.LayerControl(collapsed=False).add_to(Map)
                        st_folium(Map, width=800, height=500, returned_objects=[])

                    with col_data:
                        st.subheader("Parameter Distribution")
                        if selected_params:
                            chart_df = df.set_index('Location')[selected_params]
                            st.bar_chart(chart_df)
                        else:
                            st.warning("Please select parameters from the sidebar to view the chart.")
                        
                        st.subheader("Database Export")
                        st.dataframe(df)

                except Exception as e:
                    st.error(f"❌ An error occurred during GEE processing. This usually means the date range is too narrow and no imagery exists. Error details: {e}")


# ==========================================
# TAB 2: LIVE GOVERNMENT TELEMETRY (GLENS)
# ==========================================
with tab2:
    st.markdown("### 🏛️ Ground-Truth IoT Telemetry Integration")
    st.markdown("Fetch live sensor data directly from the state government database (GLENS) to validate satellite optical models and train AI/ML algorithms.")
    
    st.markdown("#### ⚙️ Query Parameters")
    
    # We put the controls directly inside the tab, split into 3 beautiful columns
    col_st, col_start, col_end = st.columns([1.2, 1, 1])
    
    with col_st:
        selected_station_p2 = st.selectbox("📍 Select MPPCB Station", list(STATION_MAP.keys()), on_change=reset_phase2)
        site_id_p2 = STATION_MAP[selected_station_p2]
        
    default_end_p2 = datetime.datetime.now()
    default_start_p2 = default_end_p2 - datetime.timedelta(days=1)
    
    with col_start:
        start_d = st.date_input("From Date", value=default_start_p2.date(), key="p2_sd", on_change=reset_phase2)
        start_t = st.time_input("From Time", value=default_start_p2.time(), key="p2_st", on_change=reset_phase2)
        
    with col_end:
        end_d = st.date_input("To Date", value=default_end_p2.date(), key="p2_ed", on_change=reset_phase2)
        end_t = st.time_input("To Time", value=default_end_p2.time(), key="p2_et", on_change=reset_phase2)

    start_datetime_p2 = datetime.datetime.combine(start_d, start_t)
    end_datetime_p2 = datetime.datetime.combine(end_d, end_t)

    # Validations & Button
    st.markdown("<br>", unsafe_allow_html=True)
    fetch_disabled = False
    if start_datetime_p2 >= end_datetime_p2:
        st.error("❌ 'From' time must be before 'To' time.")
        fetch_disabled = True

    if st.button("📡 Fetch Live Database Telemetry", type="primary", use_container_width=True, disabled=fetch_disabled):
        fetch_live_data.clear() # Wipe cache to ensure a fresh live fetch
        st.session_state.phase2_show_data = True

    st.divider()

    # --- PHASE 2 OUTPUT LOGIC ---
    if st.session_state.phase2_show_data:
        start_str_p2 = start_datetime_p2.strftime("%Y-%m-%d %H:%M:%S")
        end_str_p2 = end_datetime_p2.strftime("%Y-%m-%d %H:%M:%S")
        
        with st.spinner(f"Establishing secure connection to GLENS API for {selected_station_p2}..."):
            raw_data = fetch_live_data(selected_station_p2, site_id_p2, start_str_p2, end_str_p2)
            
            if isinstance(raw_data, dict) and raw_data.get("error") == "timeout":
                st.error("⚠️ GLENS Server is currently unreachable or timed out. Please try again later.")
                st.stop()
                
            df_p2 = process_data(raw_data)

        if not df_p2.empty:
            latest_time_str = df_p2["Time"].dropna().iloc[-1].strftime("%Y-%m-%d %H:%M:%S")
            
            st.subheader(f"Current Readings: {selected_station_p2}")
            st.caption(f"Data period: {start_str_p2} ➔ {end_str_p2} | Last valid sensor ping: {latest_time_str}")
            
            bod_val = get_latest_valid_metric(df_p2, "BOD")
            cod_val = get_latest_valid_metric(df_p2, "COD")
            do_val = get_latest_valid_metric(df_p2, "DO")
            turb_val = get_latest_valid_metric(df_p2, "Turbidity")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("💧 BOD (mg/l)", bod_val)
            col2.metric("🧪 COD (mg/l)", cod_val)
            col3.metric("🫧 Dissolved Oxygen", do_val)
            col4.metric("📊 Turbidity (NTU)", turb_val)
            
            st.markdown("<br>", unsafe_allow_html=True)
            st.subheader("📈 Historical Trends for Selected Timeframe")
            
            available_params_p2 = [col for col in df_p2.columns if col != "Time" and df_p2[col].notna().any()]
            
            if available_params_p2:
                selected_params_p2 = st.multiselect(
                    "Select parameters to graph:",
                    options=available_params_p2,
                    default=["BOD", "COD"] if "BOD" in available_params_p2 else available_params_p2[:2],
                    key="p2_multiselect"
                )
                
                if selected_params_p2:
                    fig = px.line(df_p2, x="Time", y=selected_params_p2, markers=True, 
                                  title=f"{selected_station_p2} Telemetry Trends")
                    st.plotly_chart(fig, use_container_width=True)
                    
                with st.expander("📂 Show Raw Database View"):
                    st.dataframe(df_p2)
            else:
                st.info("No numerical historical data available to plot for this specific timeframe.")

        else:
            st.warning(f"No data available for {selected_station_p2} during the selected timeframe. Sensors may be offline.")
            
    else:
        st.info("👈 Please define your query parameters above and click **'Fetch Live Database Telemetry'** to pull historical data.")
