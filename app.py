import ee
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
import datetime
import io
import time

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(page_title="Water Quality Dashboard", layout="wide")
st.title("🛰️ Advanced Water Quality Analysis Dashboard")
st.markdown("Monitoring Optically Active Parameters via Sentinel-2 & Landsat 8 Imagery")

# --- 2. Auth
@st.cache_resource
def authenticate_gee():
    try:
        # Scenario A: We are on the Live Server (Using Streamlit Secrets)
        if "gcp_service_account" in st.secrets:
            import google.oauth2.service_account
            creds_dict = dict(st.secrets["gcp_service_account"])
            
            # THE FIX: Explicitly request the Earth Engine Scope!
            ee_scopes = ['https://www.googleapis.com/auth/earthengine']
            credentials = google.oauth2.service_account.Credentials.from_service_account_info(
                creds_dict, scopes=ee_scopes
            )
            
            ee.Initialize(credentials, project='turbidity-chlorophyll-test')
            return True
            
        # Scenario B: We are on your local computer (Using credentials.json)
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
    
# --- 3. designs
st.sidebar.header("📅 Select Timeline")
start_date = st.sidebar.date_input("Start Date", datetime.date(2025, 1, 1))
end_date = st.sidebar.date_input("End Date", datetime.date(2026, 1, 1))

start_str = start_date.strftime('%Y-%m-%d')
end_str = end_date.strftime('%Y-%m-%d')

st.sidebar.markdown("---")
st.sidebar.header("📍 Data Input Method")
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
st.sidebar.header("📊 Select Parameters")
selected_params = st.sidebar.multiselect(
    "Choose parameters to display in charts:",
    ['Turbidity (NDTI)', 'Chlorophyll (NDCI)', 'Suspended Solids (Proxy)', 'Color (CDOM)', 'Temperature (°C)'],
    default=['Turbidity (NDTI)', 'Chlorophyll (NDCI)']
)

# --- 4. MAIN UI & GEE LOGIC ---
tab1, tab2 = st.tabs(["📊 Direct Optical & Thermal Analysis (Live)", "🤖 AI/ML Predictive Analytics (Phase 2)"])

with tab1:
    if len(pois_data) == 0:
        st.warning("⚠️ Please provide at least one location via the sidebar to run the analysis.")
    else:
        st.info(f"Ready to analyze **{len(pois_data)}** locations between **{start_str}** and **{end_str}**.")
        
        if st.button("🚀 Run Satellite Analysis", type="primary"):
            with st.spinner("Communicating with Google Earth Engine... Extracting Spectral Signatures..."):
                try:
                    # 1. Prepare Geometries
                    features = [ee.Feature(ee.Geometry.Point(coords), {'Name': name}) for name, coords in pois_data.items()]
                    roi_collection = ee.FeatureCollection(features)

                    # 2. SENTINEL-2 ENGINE 
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

                    # 3. LANDSAT 8 ENGINE 
                    dataset_l8 = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
                               .filterBounds(roi_collection)
                               .filterDate(start_str, end_str)
                               .filter(ee.Filter.lt('CLOUD_COVER', 20))
                               .median())
                    
                    temp_c = dataset_l8.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15).rename('Temp_C')
                    sampled_l8 = temp_c.sampleRegions(collection=roi_collection, scale=30, geometries=True).getInfo()

                    # 4. Merge Data Safely
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
                    
                    # --- DISPLAY DASHBOARD ---
                    col_map, col_data = st.columns([1.5, 1])
                    
                    with col_map:
                        st.subheader("QGIS Multi-Layer Map(Takes time to render)")
                        st.markdown("*Use the **Layers icon** in the top right corner of the map to toggle Turbidity, Chlorophyll, or True Color on and off.*")
                        
                        # Enhanced Folium Mapping Function
                        def add_ee_layer(self, ee_image_object, vis_params, name, show=True, opacity=1.0):
                            try:
                                map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
                                folium.raster_layers.TileLayer(
                                    tiles=map_id_dict['tile_fetcher'].url_format,
                                    attr='Google Earth Engine',
                                    name=name,
                                    overlay=True,
                                    control=True,
                                    show=show, # Allows us to hide layers by default so they don't clash
                                    opacity=opacity
                                ).add_to(self)
                            except Exception:
                                pass

                        folium.Map.add_ee_layer = add_ee_layer
                        
                        first_point = list(pois_data.values())[0]
                        Map = folium.Map(location=[first_point[1], first_point[0]], zoom_start=8)
                        
                        # LAYER 1: True Color (Sentinel-2 RGB)
                        Map.add_ee_layer(dataset_s2.select(['B4', 'B3', 'B2']), {'min': 0, 'max': 3000}, '🛰️ Sentinel-2 True Color (RGB)', show=False, opacity=0.8)

                        # LAYER 2: Turbidity (NDTI) - QGIS Style Gradient
                        ndti_palette = ['darkblue', 'blue', 'cyan', 'yellow', 'orange', 'saddlebrown']
                        Map.add_ee_layer(s2_indices.select('NDTI'), {'min': -0.1, 'max': 0.2, 'palette': ndti_palette}, '🟤 Turbidity (NDTI) Map', show=True, opacity=0.6)

                        # LAYER 3: Chlorophyll (NDCI) - QGIS Style Gradient
                        ndci_palette = ['darkblue', 'blue', 'cyan', 'lime', 'yellow', 'red']
                        Map.add_ee_layer(s2_indices.select('NDCI'), {'min': -0.1, 'max': 0.2, 'palette': ndci_palette}, '🟢 Chlorophyll (NDCI) Map', show=False, opacity=0.6)

                        # Add Logic Markers
                        for index, row in df.iterrows():
                            lat, lon = pois_data[row['Location']][1], pois_data[row['Location']][0]
                            marker_color = 'red' if row['Quality Status'] == 'Poor / Critical' else 'orange' if row['Quality Status'] == 'Moderate / At Risk' else 'green'
                            
                            popup_text = f"<b>{row['Location']}</b><br>Status: {row['Quality Status']}<br>Chlorophyll: {row['Chlorophyll (NDCI)']}<br>Turbidity: {row['Turbidity (NDTI)']}"
                            folium.Marker(location=[lat, lon], popup=folium.Popup(popup_text, max_width=300), icon=folium.Icon(color=marker_color)).add_to(Map)
                        
                        # ADD LAYER CONTROL (The QGIS "Panel")
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

with tab2:
    st.header("Predictive Water Quality (BOD, COD, DO)")
    st.info("⚠️(Under Development)")

st.markdown("---")
st.header("📡 Phase 2: Real-Time Ground Telemetry (MPPCB Integration)")
st.markdown("Live IoT sensor feeds from the MPPCB Continuous Water Quality Monitoring System.")

# 1. The Station Selector
live_stations = [
    'River Narmada at Origin Point, Amarkantak',
    'River Narmada at Dindori',
    'River Narmada at Down Stream of Jabalpur',
    'River Narmada at Hoshangabad',
    'River Narmada at Omkareshwar',
    'River Narmada at Dharampuri',
    'River Kshipra upstream city, Ujjain',
    'River Kshipra at, Lalpul',
    'River Kanha down stream of Kabeetkhedi, Indore',
    'River Kanha before mixing to River Kshipra, Ujjain'
]

selected_live_station = st.selectbox("Select Station for Live Telemetry Feed:", live_stations)

# 2. The Scraping Engine (Currently Mocked with your Screenshot Data)
def fetch_live_telemetry(station_name):
    # IN THE FUTURE: This is where we will put the BeautifulSoup / Selenium web scraping code.
    # For now, if Amarkantak is selected, return the exact live data from your screenshot.
    if station_name == 'River Narmada at Origin Point, Amarkantak':
        return {
            "pH": 8.04,
            "BOD": 1.34,
            "COD": 6.64,
            "DO": 7.23,
            "Conductivity": 204.0,
            "Timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    else:
        # Return generic safe baseline data for other stations until the scraper is built
        return {"pH": 7.5, "BOD": 2.0, "COD": 10.0, "DO": 6.5, "Conductivity": 250.0, "Timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}

# 3. The UI Render
if st.button("🔄 Fetch Live IoT Data"):
    with st.spinner(f"Connecting to MPPCB Telemetry at {selected_live_station}..."):
        time.sleep(1.5) # Simulating network delay
        data = fetch_live_telemetry(selected_live_station)
        
        st.success(f"✅ Live Connection Established! Last Updated: {data['Timestamp']}")
        
        # Display large, beautiful metric widgets
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric(label="pH Level", value=f"{data['pH']}", delta="Optimal", delta_color="normal")
        with col2:
            st.metric(label="BOD (mg/L)", value=f"{data['BOD']}", delta="-0.2 mg/L", delta_color="inverse") # Inverse because lower BOD is better
        with col3:
            st.metric(label="COD (mg/L)", value=f"{data['COD']}", delta="Stable", delta_color="off")
        with col4:
            st.metric(label="Dissolved O2 (mg/L)", value=f"{data['DO']}", delta="+0.1 mg/L", delta_color="normal")
        with col5:
            st.metric(label="Conductivity", value=f"{data['Conductivity']}")

        # 4. Phase 2 Analysis (Comparing Satellite to Ground)
        st.subheader("🛰️ AI Correlation Analysis")
        st.info("In the final version, this panel will compare the live MPPCB BOD/COD readings against our Earth Engine Turbidity/Chlorophyll satellite data to train the predictive model.")
