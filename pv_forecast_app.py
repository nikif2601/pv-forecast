import streamlit as st
import pandas as pd
import pvlib
import requests

# --- Helper Functions ---
@st.cache_data(show_spinner=False)
def fetch_forecast(lat, lon, tz):
    """
    Fetches next-day hourly weather forecast using NASA POWER API as fallback for Open-Meteo.
    Returns DataFrame indexed in CET timezone with columns: ghi, dhi, dni, temperature_2m, wind_speed_10m.
    """
    # Compute tomorrow date for API
    local_tz = tz
    tomorrow_date = (pd.Timestamp.now(local_tz) + pd.Timedelta(days=1)).strftime('%Y%m%d')

    # NASA POWER API parameters
    url = 'https://power.larc.nasa.gov/api/temporal/hourly/point'
    params = {
        'parameters': 'ALLSKY_SFC_SW_DWN,ALLSKY_SFC_DL_SW_DWN,ALLSKY_SFC_DIFF_SW_DWN,T2M,WS10M',
        'community': 'RE',
        'longitude': lon,
        'latitude': lat,
        'start': tomorrow_date,
        'end': tomorrow_date,
        'format': 'JSON'
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        st.error(f"NASA POWER API error: {e}")
        return pd.DataFrame()

    # Extract hourly data
    try:
        params_data = data['properties']['parameter']
        ghi_series = pd.Series(params_data['ALLSKY_SFC_SW_DWN'])
        dni_series = pd.Series(params_data['ALLSKY_SFC_DL_SW_DWN'])
        dhi_series = pd.Series(params_data['ALLSKY_SFC_DIFF_SW_DWN'])
        temp_series = pd.Series(params_data['T2M'])
        wind_series = pd.Series(params_data['WS10M'])
    except KeyError:
        st.error("Unexpected NASA POWER response format.")
        return pd.DataFrame()

    # Build DataFrame
    df = pd.DataFrame({
        'ghi': ghi_series,
        'dni': dni_series,
        'dhi': dhi_series,
        'temperature_2m': temp_series,
        'wind_speed_10m': wind_series
    })
    # Index is hour strings like '2025061100', parse to datetime UTC
    df.index = pd.to_datetime(df.index, format='%Y%m%d%H', utc=True)
    # Convert to local timezone
    df = df.tz_convert(local_tz)

    return df

# --- Load PVLib Tables ---
@st.cache_data(show_spinner=False)
def get_pv_tables():
    modules = pvlib.pvsystem.retrieve_sam('CECmod')
    inverters = pvlib.pvsystem.retrieve_sam('CECinverter')
    return modules, inverters

_modules, _inverters = get_pv_tables()

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
    ac = pvlib.pvsystem.snlinverter(
        v_dc=sd['v_mp'],
        i_dc=sd['i_mp'],
        **system.inverter_parameters
    ).rename('ac_power')

    hourly_kwh = ac / 1000
    daily_kwh = hourly_kwh.resample('D').sum()
    return ac, hourly_kwh, daily_kwh

# --- Streamlit App Config ---
tz = "Europe/Berlin"
modules = list(_modules.keys())
inverters = list(_inverters.keys())

def get_default_index(lst, item):
    try:
        return lst.index(item)
    except ValueError:
        return 0

default_module = get_default_index(modules, 'Canadian_Solar_CS5P_220M___2009_')
default_inverter = get_default_index(inverters, 'ABB__MICRO_0_25_I_OUTD_US_208__208V_')

st.set_page_config(page_title="Next-Day PV Forecast", layout="centered")
st.title("🌞 Next-Day PV Production Forecast")
st.markdown("All times in Central European Time (CET). Enter system details and click **Run Forecast**.")

tab1, tab2 = st.tabs(["Settings", "Results"])

with tab1:
    st.subheader("Location & Orientation")
    lat = st.number_input("Latitude", min_value=-90.0, max_value=90.0, value=51.5074, format="%.6f")
    lon = st.number_input("Longitude", min_value=-180.0, max_value=180.0, value=13.4050, format="%.6f")
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
            ac, hourly_kwh, daily_kwh = compute_pv_output(
                weather, lat, lon, tilt, azimuth, module_name, inverter_name
            )

        st.subheader("Hourly AC Power (W)")
        st.line_chart(ac)

        st.subheader("Hourly Energy (kWh)")
        st.line_chart(hourly_kwh)

        st.subheader("Daily Energy (kWh)")
        st.write(daily_kwh)

        total = daily_kwh.sum() if not daily_kwh.empty else 0.0
        st.success(f"Total tomorrow (CET): {total:.2f} kWh")

        csv = hourly_kwh.to_frame().to_csv()
        st.download_button("Download CSV", data=csv, file_name="hourly_kWh_forecast.csv")

st.markdown("---")
st.markdown("Built with PVLib and Streamlit. Timezone fixed to CET (Europe/Berlin). Selected module and inverter above.")


