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

tz = "Europe/Berlin"

@st.cache_data(show_spinner=False)
def compute_pv_output(weather, lat, lon, tilt, azimuth, module_key, inverter_key, num_panels, num_inverters):
    if weather.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)
    mc_weather = weather.rename(columns={'temperature_2m': 'temp_air', 'wind_speed_10m': 'wind_speed'})
    location = Location(lat, lon, tz)
    system = pvlib.pvsystem.PVSystem(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        module_parameters=_modules[module_key],
        inverter_parameters=_inverters[inverter_key],
        temperature_model_parameters=pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    )
    mc = ModelChain(system, location, aoi_model='no_loss')
    mc.run_model(mc_weather)
    try:
        ac_series = mc.results.ac
    except Exception:
        ac_series = mc.ac
    # plant power total in W then convert to kW
    ac_total_kw = ac_series * num_panels * num_inverters / 1000
    hourly_kwh = ac_total_kw
    daily_kwh = hourly_kwh.resample('D').sum()
    return ac_total_kw, hourly_kwh, daily_kwh

# --- Streamlit App ---
st.set_page_config(page_title="Next-Day PV Forecast", layout="centered")
st.title("🌞 Next-Day PV Production Forecast")
st.markdown("All times in CET. Enter details and click **Run Forecast**.")

module_keys = list(_modules.keys())
mod_brands = sorted({k.split('_')[0] for k in module_keys})
inv_keys = list(_inverters.keys())
inv_brands = sorted({k.split('_')[0] for k in inv_keys})

tab1, tab2 = st.tabs(["Settings", "Results"])
with tab1:
    st.subheader("Location & Orientation")
    lat = st.number_input("Latitude", -90.0, 90.0, 51.5074, format="%.6f")
    lon = st.number_input("Longitude", -180.0, 180.0, 13.4050, format="%.6f")
    tilt = st.slider("Tilt (°)", 0.0, 90.0, 30.0)
    azimuth = st.slider("Azimuth (°)", 0.0, 360.0, 180.0)

    st.subheader("PV Module Selection")
    m_brand = st.selectbox("Module Brand", mod_brands)
    module_options = [k for k in module_keys if k.startswith(m_brand + '_')]
    module_labels = []
    label_to_module = {}
    for key in module_options:
        base = key.split('___')[0]
        year = key.split('___')[1].strip('_') if '___' in key else 'N/A'
        params = _modules[key]
        p_stc = params.get('STC', params.get('Impo',0)*params.get('Vmpo',0))
        label = f"{base} ({year}, {int(p_stc)} W)"
        module_labels.append(label)
        label_to_module[label] = key
    selected_module = st.selectbox("Module Type", module_labels)
    module_key = label_to_module[selected_module]

    st.subheader("Inverter Selection")
    i_brand = st.selectbox("Inverter Brand", inv_brands)
    inverter_options = [k for k in inv_keys if k.startswith(i_brand + '_')]
    inverter_labels = []
    label_to_inv = {}
    for key in inverter_options:
        base = key.split('_')[0]
        params = _inverters[key]
        paco = params.get('Paco',params.get('Pac0',0))/1000
        vac = params.get('Vac','N/A')
        label = f"{base} ({paco:.2f} kW, {vac} V)"
        inverter_labels.append(label)
        label_to_inv[label] = key
    selected_inverter = st.selectbox("Inverter Type", inverter_labels)
    inverter_key = label_to_inv[selected_inverter]

    st.subheader("Plant Size")
    num_panels = st.number_input("# of Panels", 1, 10000, 1)
    num_inverters = st.number_input("# of Inverters", 1, 1000, 1)

    run = st.button("Run Forecast")

with tab2:
    if not run:
        st.info("Run a forecast in Settings.")
    else:
        with st.spinner("Computing plant forecast..."):
            weather = fetch_forecast(lat, lon, tz)
            ac, hourly_kwh, daily_kwh = compute_pv_output(
                weather, lat, lon, tilt, azimuth,
                module_key, inverter_key,
                num_panels, num_inverters
            )
        st.subheader("Hourly Plant Power (kW)")
        st.line_chart(ac)
        st.subheader("Hourly Energy (kWh)")
        st.line_chart(hourly_kwh)
        st.subheader("Daily Energy (kWh)")
        st.write(daily_kwh)
        total = daily_kwh.sum() if not daily_kwh.empty else 0.0
        st.success(f"Total tomorrow: {total:.2f} kWh")
        st.download_button("Download CSV", data=hourly_kwh.to_frame().to_csv(), file_name="forecast.csv")

st.markdown("---")
st.markdown("Built with PVLib & Streamlit.")









