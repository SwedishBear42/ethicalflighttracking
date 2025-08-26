import streamlit as st
import pandas as pd
import folium
from datetime import date, timedelta
import requests
import time

# --- Page Configuration ---
st.set_page_config(layout="wide", page_title="GlobalX Fleet Tracker")

# --- Helper Functions with Caching ---

@st.cache_data(ttl="6h") # Cache data for 6 hours
def load_file_data(file_path, is_excel=False):
    """Loads data from CSV or Excel, caching the result."""
    try:
        if is_excel:
            return pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)
            df.rename(columns={"latitude_deg": "latitude", "longitude_deg": "longitude"}, inplace=True, errors='ignore')
            return df
    except FileNotFoundError:
        st.error(f"Required file not found in repository: {file_path}")
        return None

@st.cache_data(ttl="6h")
def fetch_flight_data_for_aircraft(icao_code, registration):
    """Fetches and processes all flight data for a single aircraft for the year."""
    print(f"Fetching yearly data for {registration} ({icao_code})... This happens once per aircraft.")
    start_date = date(2025, 1, 1)
    end_date = date(2025, 8, 26)
    
    urls = []
    base_url_template = "https://globe.adsbexchange.com/globe_history/{year}/{month:02d}/{day:02d}/traces/{icao_folder}/trace_full_{icao_code}.json"
    delta = end_date - start_date
    for i in range(delta.days + 1):
        day = start_date + timedelta(days=i)
        urls.append(base_url_template.format(year=day.year, month=day.month, day=day.day, icao_folder=icao_code[-2:], icao_code=icao_code))

    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://globe.adsbexchange.com/'}
    all_records = []

    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                file_timestamp = pd.to_datetime(data.get("timestamp"), unit='s')
                current_callsign = None
                for record in data.get("trace", []):
                    details = record[8] if len(record) > 8 and isinstance(record[8], dict) else {}
                    if "flight" in details and details.get("flight").strip(): current_callsign = details.get("flight").strip()
                    all_records.append({"flight_callsign": current_callsign, "absolute_timestamp": file_timestamp + timedelta(seconds=record[0]),
                                        "latitude": record[1], "longitude": record[2]})
        except Exception:
            pass
    
    if not all_records:
        return pd.DataFrame()
        
    return pd.DataFrame(all_records)

def find_nearest_airport(lat, lon, airports_df):
    """Finds the closest airport from the airports DataFrame."""
    distances = ((airports_df['latitude'] - lat)**2 + (airports_df['longitude'] - lon)**2)
    airport = airports_df.loc[distances.idxmin()]
    if distances.min() < 0.5**2:
        return f"{airport['name']}, {airport['municipality']}"
    return "Unknown Airfield or Location"

# --- Main App Interface ---
st.title("✈️ GlobalX Fleet Activity Dashboard")

# Load foundational data
aircraft_df = load_file_data("GlobalX flight tracking.xlsx", is_excel=True)
airports_df = load_file_data("airports.csv")

if aircraft_df is None or airports_df is None:
    st.stop()

# Clean airport data once
airports_df.dropna(subset=['latitude', 'longitude', 'name', 'municipality'], inplace=True)

# Sidebar for aircraft selection
st.sidebar.title("Aircraft Selection")
registration_list = aircraft_df['Registration'].dropna().unique()
selected_registration = st.sidebar.selectbox("Choose an aircraft:", registration_list)

# Main panel display
if selected_registration:
    aircraft_details = aircraft_df[aircraft_df['Registration'] == selected_registration].iloc[0]
    selected_icao = aircraft_details['icao']
    
    # Fetch data for the selected aircraft (will be cached)
    with st.spinner(f"Fetching & processing yearly flight data for {selected_registration}... (This may take a few minutes on first load per aircraft)"):
        flight_df = fetch_flight_data_for_aircraft(selected_icao, selected_registration)

    if flight_df.empty:
        st.warning("No flight data found for this aircraft in 2025.")
        st.stop()
        
    st.header(f"Displaying Data for: {selected_registration}")
    
    # --- Display aircraft details ---
    col1, col2, col3 = st.columns(3)
    col1.metric("Aircraft Model", aircraft_details.get("Aircraft", "N/A"))
    col2.metric("Type", aircraft_details.get("Type", "N/A"))
    col3.metric("MSN", str(aircraft_details.get("MSN", "N/A")))
    st.info(f"**Delivery Date:** {aircraft_details.get('Delivery Date', 'N/A')}")
    st.warning(f"**Remark:** {aircraft_details.get('Remark', 'N/A')}")

    # --- Generate a flight summary DataFrame on-the-fly ---
    flight_df.sort_values(by='absolute_timestamp', inplace=True)
    flight_df['time_diff'] = flight_df['absolute_timestamp'].diff().dt.total_seconds()
    flight_segments = (flight_df['time_diff'] > (4 * 3600)) | (flight_df['flight_callsign'] != flight_df['flight_callsign'].shift())
    flight_df['flight_id'] = flight_segments.cumsum()
    
    # Create a summary of individual flights
    summary_list = []
    for flight_id, segment in flight_df.groupby('flight_id'):
        if not segment.empty:
            start_record = segment.iloc[0]
            end_record = segment.iloc[-1]
            summary_list.append({
                "departure_airport": find_nearest_airport(start_record['latitude'], start_record['longitude'], airports_df),
                "arrival_airport": find_nearest_airport(end_record['latitude'], end_record['longitude'], airports_df),
                "departure_time": start_record['absolute_timestamp']
            })
    summary_df = pd.DataFrame(summary_list)

    st.divider()
    
    # --- Display Analytics ---
    stats_col1, stats_col2 = st.columns(2)
    
    with stats_col1:
        st.subheader("Top 5 Most Visited Destinations")
        top_5 = summary_df[summary_df['arrival_airport'] != 'Unknown Airfield or Location']['arrival_airport'].value_counts().nlargest(5)
        st.dataframe(top_5)

    with stats_col2:
        st.subheader("Last 5 Unique Destinations")
        known_destinations = summary_df[summary_df['arrival_airport'] != 'Unknown Airfield or Location']
        last_five = known_destinations['arrival_airport'].unique()[-5:]
        st.markdown("\n".join([f"- {loc}" for loc in last_five]))

    st.subheader("Monthly Flight Activity")
    # Ensure months are sorted chronologically for the chart
    summary_df['month'] = pd.Categorical(
        summary_df['departure_time'].dt.strftime('%B'),
        categories=["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
        ordered=True
    )
    monthly_counts = summary_df['month'].value_counts().sort_index()
    st.line_chart(monthly_counts)

    # --- Display the Map ---
    st.divider()
    st.subheader("Interactive Flight Map")
    map_center = [flight_df['latitude'].mean(), flight_df['longitude'].mean()]
    m = folium.Map(location=map_center, zoom_start=4, tiles="CartoDB positron")
    colors = ['#3388ff', '#f58733', '#52b552', '#d43737', '#9355dc', '#333333'] # Blue, Orange, Green, Red, Purple, Black
    
    for i, segment in flight_df.groupby('flight_id'):
        points = segment[['latitude', 'longitude']].dropna().values.tolist()
        if len(points) > 1:
            color = colors[i % len(colors)]
            folium.PolyLine(points, color=color, weight=2.5, opacity=0.8, tooltip=f"Flight ID: {i}").add_to(m)

    st.components.v1.html(m._repr_html_(), height=600)