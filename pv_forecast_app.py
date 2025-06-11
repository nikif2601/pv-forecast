import streamlit as st
import pandas as pd
import pvlib
import requests
from pvlib.location import Location
from pvlib.modelchain import ModelChain

# --- Helper Functions ---
@st.cache_data(show_spinner=False)
def fetch_forecast(lat, lon, tz):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        'latitude': lat,
        'longitude': lon,
        'hourly': 'shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m,wind_speed_10m',
        'timezone': 'UTC',
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        st.error(f"Failed to fetch weather data: {e}")
        return pd.DataFrame()
    data = r.json().get('hourly', {})
    if not data or 'time' not in data:
        st.error("No hourly data returned by weather API.")
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.set_index('time').tz_convert(tz)
    df = df.rename(columns={
        'shortwave_radiation': 'ghi',
        'direct_normal_irradiance': 'dni',
        'diffuse_radiation': 'dhi'
    })
    tomorrow = (pd.Timestamp.now(tz) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    try:
        df = df.loc[tomorrow]
    except KeyError:
        st.error(f"No data available for {tomorrow} in timezone {tz}.")
        return pd.DataFrame()
    return df

# --- Load PVLib Tables ---
@st.cache_data(show_spinner=False)
def get_pv_tables():
    modules = pvlib.pvsystem.retrieve_sam('CECmod')
    inverters = pvlib.pvsystem.retrieve_sam('CECinverter')
    return modules, inverters

_modules, _inverters = get_pv_tables()

@st.cache_data(show_spinner=False)
def compute_pv_output(weather, lat, lon, tilt, azimuth, module_name, inverter_name, num_panels, num_inverters):
    if weather.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)
    mc_weather = weather.rename(columns={'temperature_2m': 'temp_air', 'wind_speed_10m': 'wind_speed'})
    location = Location(lat, lon, tz)
    system = pvlib.pvsystem.PVSystem(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        module_parameters=_modules[module_name],
        inverter_parameters=_inverters[inverter_name],
        temperature_model_parameters=pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    )
    mc = ModelChain(system, location, aoi_model='no_loss')
    mc.run_model(mc_weather)
    try:
        ac = mc.results.ac.rename('ac_power')
    except Exception:
        ac = mc.ac.rename('ac_power')
        # scale to plant
    ac_total_w = ac * num_panels * num_inverters  # total AC power in W
    # convert to kW
    ac_total_kw = ac_total_w / 1000  # kW
    # hourly energy remains kWh (since kW * 1h)
    hourly_kwh = ac_total_kw  # power in kW for each hour equals kWh over that hour
    daily_kwh = hourly_kwh.resample('D').sum()
    return ac_total_kw, hourly_kwh, daily_kwh
    return ac_total, hourly_kwh, daily_kwh

# --- Streamlit App Config ---
tz = "Europe/Berlin"
modules = list(_modules.keys())
inverters = list(_inverters.keys())
try:
    default_module = modules.index('Canadian_Solar_CS5P_220M___2009_')
except ValueError:
    default_module = 0
try:
    default_inverter = inverters.index('ABB__MICRO_0_25_I_OUTD_US_208__208V_')
except ValueError:
    default_inverter = 0

st.set_page_config(page_title="Next-Day PV Forecast", layout="centered")
st.title("🌞 Next-Day PV Production Forecast")
st.markdown("All times in CET. Enter details and click **Run Forecast**.")

tab1, tab2 = st.tabs(["Settings", "Results"])
with tab1:
    st.subheader("Location & Orientation")
    lat = st.number_input("Latitude", min_value=-90.0, max_value=90.0, value=51.507400, format="%.6f")
    lon = st.number_input("Longitude", min_value=-180.0, max_value=180.0, value=13.405000, format="%.6f")
    tilt = st.slider("Tilt (°)", 0.0, 90.0, 30.0)
    azimuth = st.slider("Azimuth (°)", 0.0, 360.0, 180.0)

    st.subheader("PV Components & Scale")
    col1, col2, col3 = st.columns(3)
    with col1:
        module_name = st.selectbox("Module", modules, index=default_module)
    with col2:
        inverter_name = st.selectbox("Inverter", inverters, index=default_inverter)
    with col3:
        num_panels = st.number_input("# of Panels", min_value=1, value=1, step=1)
        num_inverters = st.number_input("# of Inverters", min_value=1, value=1, step=1)

    run = st.button("Run Forecast")

with tab2:
    if not run:
        st.info("Run a forecast in Settings.")
    else:
        with st.spinner("Computing plant forecast..."):
            weather = fetch_forecast(lat, lon, tz)
            ac, hourly_kwh, daily_kwh = compute_pv_output(
                weather, lat, lon, tilt, azimuth,
                module_name, inverter_name,
                num_panels, num_inverters
            )  # ac is in kW now
        st.subheader("Hourly AC Power for Plant (kW)")
        st.line_chart(ac)
        st.subheader("Hourly Energy for Plant (kWh)")
        st.line_chart(hourly_kwh)
        st.subheader("Daily Energy (kWh)")
        st.write(daily_kwh)
        total = daily_kwh.sum() if not daily_kwh.empty else 0.0
        st.success(f"Total tomorrow (CET): {total:.2f} kWh")
        csv = hourly_kwh.to_frame().to_csv()
        st.download_button("Download Plant Hourly kWh CSV", data=csv, file_name="plant_hourly_kWh_forecast.csv")

st.markdown("---")
st.markdown("Built with PVLib & Streamlit. Scaled by panels & inverters.")





