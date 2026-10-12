"""Citi Bike system-health dashboard.   Run:  streamlit run app/dashboard.py"""
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

M = Path(__file__).resolve().parents[1] / "metrics"
st.set_page_config(page_title="Citi Bike System Health", layout="wide")


@st.cache_data(ttl=3600)
def load():
    b = pd.read_parquet(M / "daily_borough_kpis.parquet")
    s = pd.read_parquet(M / "daily_station_kpis.parquet")
    info = pd.read_parquet(M / "stations.parquet")
    dq = pd.read_parquet(M / "data_quality.parquet")
    for df in (b, s):
        df["day"] = pd.to_datetime(df["day"])
    return b, s, info, dq


b, s, info, dq = load()
KPIS = {  # column: (label, format, higher_is_better)
    "pct_time_empty":      ("Time stations are empty", "{:.1%}", False),
    "pct_time_full":       ("Time stations are full", "{:.1%}", False),
    "peak_availability":   ("Peak availability (bike AND dock)", "{:.1%}", True),
    "median_recovery_min": ("Median minutes to recover from empty", "{:.0f}", False),
    "ebike_share":         ("E-bike share of docked bikes", "{:.1%}", None),
}

st.title("Citi Bike System Health")
st.caption("Built from 5-minute snapshots of Citi Bike's public GBFS feed. "
           "Definitions are in the README.")

with st.sidebar:
    boroughs = ["All"] + sorted(x for x in b.borough.unique() if x != "All")
    borough = st.selectbox("Area", boroughs)
    lo, hi = b.day.min().date(), b.day.max().date()
    start, end = st.date_input("Dates", (lo, hi), min_value=lo, max_value=hi)

bb = b[(b.borough == borough) & b.day.between(pd.Timestamp(start), pd.Timestamp(end))].sort_values("day")

# --- KPI tiles: latest day vs. trailing 7-day average -------------------------
st.subheader(f"{borough}: {bb.day.max():%a %b %d} vs. prior 7-day average")
cols = st.columns(len(KPIS))
latest, prior = bb.iloc[-1], bb.iloc[-8:-1]
for col, (k, (label, fmt, good_up)) in zip(cols, KPIS.items()):
    val, base = latest[k], prior[k].mean() if len(prior) else None
    delta = None if base is None or pd.isna(base) or pd.isna(val) else val - base
    col.metric(label, "–" if pd.isna(val) else fmt.format(val),
               None if delta is None else ("+" if delta >= 0 else "") + fmt.format(delta),
               delta_color="off" if good_up is None else ("normal" if good_up else "inverse"))

# --- Trends -------------------------------------------------------------------
st.subheader("Trends")
kpi = st.radio("Metric", list(KPIS), format_func=lambda k: KPIS[k][0], horizontal=True)
trend = b[b.day.between(pd.Timestamp(start), pd.Timestamp(end))]
if borough != "All":
    trend = trend[trend.borough.isin([borough, "All"])]
st.line_chart(trend.pivot(index="day", columns="borough", values=kpi))

# --- Diagnose: which stations drive it? ---------------------------------------
st.subheader("Where is it happening?")
ss = s[s.day.between(pd.Timestamp(start), pd.Timestamp(end))]
if borough != "All":
    ss = ss[ss.borough == borough]
per = (ss.groupby("station_id")[["observed_min", "empty_min", "full_min"]].sum()
         .assign(pct_empty=lambda d: d.empty_min / d.observed_min,
                 pct_full=lambda d: d.full_min / d.observed_min)
         .join(info.set_index("station_id")[["name", "borough", "lat", "lon", "capacity"]])
         .reset_index())

left, right = st.columns([3, 2])
with left:
    scale = per.pct_empty.fillna(0).clip(0, 0.25) / 0.25          # 25%+ empty = fully red
    per["color"] = scale.map(lambda x: [int(255 * x), 90, int(220 * (1 - x)), 180])
    st.pydeck_chart(pdk.Deck(
        initial_view_state=pdk.ViewState(latitude=per.lat.mean(), longitude=per.lon.mean(), zoom=11),
        layers=[pdk.Layer("ScatterplotLayer", per, get_position="[lon, lat]",
                          get_fill_color="color", get_radius=60, pickable=True)],
        tooltip={"text": "{name}\n% time empty: {pct_empty}"}))
    st.caption("Redder = more time empty in the selected period.")
with right:
    share = per.empty_min.nlargest(20).sum() / max(per.empty_min.sum(), 1)
    st.markdown(f"**The 20 worst stations account for {share:.0%} of all empty time.**")
    st.dataframe(per.nlargest(20, "empty_min")[["name", "borough", "capacity", "pct_empty", "pct_full"]],
                 hide_index=True, column_config={
                     "pct_empty": st.column_config.ProgressColumn("% empty", format="percent", min_value=0, max_value=1),
                     "pct_full": st.column_config.ProgressColumn("% full", format="percent", min_value=0, max_value=1)})

with st.expander("Data quality (latest pipeline run)"):
    st.dataframe(dq[["check_name", "value", "threshold", "status"]], hide_index=True)
