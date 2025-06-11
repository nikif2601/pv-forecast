import streamlit as st
import pandas as pd
import pvlib
import requests

# --- Helper Functions ---
@st.cache_data(show_spinner=False)
def fetch_forecast(lat, lon, tz):
    """
    Fetches next-day hourly weather forecast from Open-Meteo.
    Uses URL parameters via requests to ensure proper encoding (e.g., timezone with slash).
    Expects an IANA timezone string (e.g., Europe/Berlin).
    Returns a pandas DataFrame indexed by datetime with columns: ghi, dhi, dni, temperature_2m, wind_speed_10m.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        'latitude': lat,
        'longitude': lon,
        'hourly': 'ghi,dhi,dni,temperature_2m,wind_speed_10m',
        'timezone': tz,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        st.error(f"Failed to fetch weather data: {e}")
        return pd.DataFrame()

    payload = r.json()
    if 'hourly' not in payload or not payload['hourly']:
        st.error("No hourly data returned by weather API.")
        return pd.DataFrame()

    data = payload['hourly']
    df = pd.DataFrame(data)
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')

    # Filter to tomorrow's data
    now = pd.Timestamp.now(tz)
    tomorrow = (now + pd.Timedelta(days=1)).date().isoformat()
    try:
        df = df.loc[tomorrow]
    except KeyError:
        st.error(f"No data available for {tomorrow} in timezone {tz}.")
        return pd.DataFrame()
    return df

# Cache module/inverter tables
def get_modules_and_inverters():
    """Load and cache module/inverter tables once."""
    modules = pvlib.pvsystem.retrieve_sam('CECmod')
    inverters = pvlib.pvsystem.retrieve_sam('CECinverter')
    return modules, inverters

_modules, _inverters = get_modules_and_inverters()

@st.cache_data(show_spinner=False)
def compute_pv_output(weather, lat, lon, tilt, azimuth, module_name, inverter_name):
    """
    Computes POA irradiance, AC power, hourly kWh, and daily kWh.
    """
    if weather.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)

    solpos = pvlib.solarposition.get_solarposition(weather.index, lat, lon)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        dni=weather['dni'],
        ghi=weather['ghi'],
        dhi=weather['dhi'],
        solar_zenith=solpos['zenith'],
        solar_azimuth=solpos['azimuth'],
        model='perez'
    )

    system = pvlib.pvsystem.PVSystem(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        module_parameters=_modules[module_name],
        inverter_parameters=_inverters[inverter_name],
        temperature_model_parameters=pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    )

    sd = pvlib.pvsystem.singlediode(
        photocurrent=poa['poa_global'] * system.module_parameters['Impo'] / system.module_parameters['Isco'],
        saturation_current=system.module_parameters['I02'],
        resistance_series=system.module_parameters['R_s'],
        resistance_shunt=system.module_parameters['R_sh'],
        nNsVth=system.module_parameters['nNsVth']
    )
    ac = pvlib.pvsystem.snlinverter(v_dc=sd['v_mp'], i_dc=sd['i_mp'], **system.inverter_parameters).rename('ac_power')

    hourly_kwh = ac / 1000
    daily_kwh = hourly_kwh.resample('D').sum()
    return ac, hourly_kwh, daily_kwh

# --- Streamlit App ---
tz = "Europe/Berlin"  # Fixed to CET
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
st.markdown("All times in Central European Time (CET, Europe/Berlin). Enter system details and click **Run Forecast**.")

tab1, tab2 = st.tabs(["Settings", "Results"])

with tab1:
    st.subheader("Location & Orientation")
    lat = st.number_input("Latitude", 51.5074, format="%.6f")
    lon = st.number_input("Longitude", 13.4050, format="%.6f")
    tilt = st.slider("Tilt (°)", 0.0, 90.0, 30.0)
    azimuth = st.slider("Azimuth (°)", 0.0, 360.0, 180.0)

    st.subheader("PV Components")
    module_name = st.selectbox("Module", modules, index=default_module)
    inverter_name = st.selectbox("Inverter", inverters, index=default_inverter)

    run = st.button("Run Forecast")

with tab2:
    if not run:
        st.info("Run a forecast in the Settings tab first.")
    else:
        with st.spinner("Fetching weather and computing forecast..."):
            weather = fetch_forecast(lat, lon, tz)
            ac, hourly_kwh, daily_kwh = compute_pv_output(weather, lat, lon, tilt, azimuth, module_name, inverter_name)

        st.subheader("Hourly AC Power (W)")
        st.line_chart(ac)

        st.subheader("Hourly Energy (kWh)")
        st.line_chart(hourly_kwh)

        st.subheader("Daily Energy (kWh)")
        st.write(daily_kwh)

        total = daily_kwh.sum() if not daily_kwh.empty else 0.0
        st.success(f"Total tomorrow (CET): {total:.2f} kWh")

        csv = hourly_kwh.to_frame().to_csv()
        st.download_button("Download CSV", csv, file_name="hourly_kWh_forecast.csv")

st.markdown("---")
st.markdown("Built with PVLib and Streamlit. Timezone fixed to CET.")
