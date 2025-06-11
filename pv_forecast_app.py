import streamlit as st
import pandas as pd
import pvlib
import requests

# --- Helper Functions ---
@st.cache_data(show_spinner=False)
def fetch_forecast(lat, lon, tz):
    """
    Fetches next-day hourly weather forecast (GHI, DHI, DNI, temperature, wind) from Open-Meteo.
    Expects an IANA timezone string (e.g., Europe/Berlin).
    Returns a pandas DataFrame indexed by datetime.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=ghi,dhi,dni,temperature_2m,wind_speed_10m"
        f"&timezone={tz}"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        st.error(f"Failed to fetch weather data: {e}")
        return pd.DataFrame()

    data = r.json().get('hourly', {})
    df = pd.DataFrame(data)
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')

    # Only keep tomorrow's data in specified timezone
    tomorrow = pd.Timestamp.now(tz=tz) + pd.Timedelta(days=1)
    date_str = tomorrow.strftime('%Y-%m-%d')
    df = df.loc[date_str]
    return df

# Cache module/inverter tables to avoid reloading on each call
_modules = pvlib.pvsystem.retrieve_sam('CECmod')
_inverters = pvlib.pvsystem.retrieve_sam('CECinverter')

@st.cache_data(show_spinner=False)
def compute_pv_output(weather, lat, lon, tz, tilt, azimuth, module_name, inverter_name):
    """
    Given weather DataFrame and system parameters, computes POA irradiance, AC power, and energy.
    Returns:
      - ac (hourly AC power in W),
      - hourly_kwh (hourly energy in kWh),
      - daily_kwh (daily energy in kWh).
    """
    if weather.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)

    # Solar position and POA irradiance
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

    # Build PV system
    system = pvlib.pvsystem.PVSystem(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        module_parameters=_modules[module_name],
        inverter_parameters=_inverters[inverter_name],
        temperature_model_parameters=pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    )

    # Effective irradiance
    effective_irradiance = poa['poa_global']

    # Single diode model
    sd = pvlib.pvsystem.singlediode(
        photocurrent=effective_irradiance * system.module_parameters['Impo'] / system.module_parameters['Isco'],
        saturation_current=system.module_parameters['I02'],
        resistance_series=system.module_parameters['R_s'],
        resistance_shunt=system.module_parameters['R_sh'],
        nNsVth=system.module_parameters['nNsVth']
    )

    # Inverter AC output
    ac = pvlib.pvsystem.snlinverter(
        v_dc=sd['v_mp'], i_dc=sd['i_mp'], **system.inverter_parameters
    ).rename('ac_power')

    # Hourly energy in kWh
    hourly_kwh = ac / 1000  # W to kW * 1h = kWh

    # Daily energy in kWh
    daily_kwh = hourly_kwh.resample('D').sum()

    return ac, hourly_kwh, daily_kwh

# --- Streamlit App ---
# Fixed IANA timezone for CET region
tz_fixed = "Europe/Berlin"

st.set_page_config(page_title="Next-Day PV Forecast", layout="centered")
st.title("🌞 Next-Day PV Production Forecast")
st.markdown(
    "Enter your PV system parameters below, then click **Run Forecast**. "
    "All times are in Central European Time (CET, Europe/Berlin)."
)

# Precompute module/inverter lists and defaults
tz = tz_fixed
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

# Input panel
tab1, tab2 = st.tabs(["Settings", "Results"])
with tab1:
    st.subheader("System Location & Orientation")
    col1, col2 = st.columns(2)
    with col1:
        lat = st.number_input("Latitude", value=51.5074, format="%.6f")
        lon = st.number_input("Longitude", value=13.4050, format="%.6f")
    with col2:
        tilt = st.slider("Tilt (°)", 0.0, 90.0, 30.0)
        azimuth = st.slider("Azimuth (°)", 0.0, 360.0, 180.0)

    st.subheader("PV Components")
    col3, col4 = st.columns(2)
    with col3:
        module_name = st.selectbox("PV Module", modules, index=default_module)
    with col4:
        inverter_name = st.selectbox("Inverter", inverters, index=default_inverter)

    run = st.button("Run Forecast")

with tab2:
    if not run:
        st.info("Run a forecast in the Settings tab first.")
    else:
        with st.spinner("Computing..."):
            weather = fetch_forecast(lat, lon, tz)
            ac, hourly_kwh, daily_kwh = compute_pv_output(
                weather, lat, lon, tz, tilt, azimuth, module_name, inverter_name
            )
        st.subheader("Hourly AC Power (W)")
        st.line_chart(ac)

        st.subheader("Hourly Energy (kWh)")
        st.line_chart(hourly_kwh)

        st.subheader("Daily Energy (kWh)")
        st.write(daily_kwh)

        total = daily_kwh.sum() if not daily_kwh.empty else 0.0
        st.success(f"Estimated total production for tomorrow (CET): {total:.2f} kWh")

        csv = hourly_kwh.to_frame().to_csv()
        st.download_button("Download Hourly kWh CSV", data=csv, file_name="hourly_kWh_forecast.csv")

# Footer
st.markdown("---")
st.markdown("Built with PVLib and Streamlit. Timezone fixed to Central European Time (CET, IANA: Europe/Berlin). Selected module and inverter above.")

