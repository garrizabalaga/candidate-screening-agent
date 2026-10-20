"""Calendario de un recruiter: sus entrevistas por día, con el perfil del candidato."""

from collections import defaultdict
from datetime import date, datetime, timedelta

import streamlit as st

import api_client
from ui import COUNTRY_LABEL, COUNTRY_TZ, DAY_NAMES, call, local_time, value

st.set_page_config(page_title="Calendario", page_icon="📅", layout="wide")
st.title("📅 Calendario de entrevistas")

recruiters = call(api_client.list_recruiters)
by_id = {r["id"]: r for r in recruiters}

col_r, col_d = st.columns([2, 2])
recruiter_id = col_r.selectbox(
    "Recruiter", list(by_id), format_func=lambda i: f"{by_id[i]['name']} · {COUNTRY_LABEL[by_id[i]['country']]}"
)
today = date.today()
period = col_d.date_input("Periodo", value=(today, today + timedelta(days=14)))
if not isinstance(period, tuple) or len(period) != 2:
    st.stop()  # mientras se elige la segunda fecha
start, end = period

recruiter = by_id[recruiter_id]
entries = call(api_client.recruiter_calendar, recruiter_id, start, end)
st.caption(f"{len(entries)} entrevista(s) por videollamada · hora de {COUNTRY_LABEL[recruiter['country']]}")

if not entries:
    st.info("No tiene entrevistas en este periodo.")
    st.stop()

tz = COUNTRY_TZ[recruiter["country"]]
by_day = defaultdict(list)
for e in entries:
    by_day[datetime.fromisoformat(e["interview"]["starts_at"]).astimezone(tz).date()].append(e)

for day in sorted(by_day):
    st.subheader(f"{DAY_NAMES[day.weekday()]} {day:%d/%m}")
    for e in by_day[day]:
        c = e["candidate"]
        f = c["fields"]
        with st.container(border=True):
            when, who = st.columns([1, 5])
            when.markdown(f"### {local_time(e['interview']['starts_at'], recruiter['country'], with_day=False)}")
            who.markdown(f"**{c['full_name']}** · {c['phone']} · {f['city'] or '—'}")
            who.caption(
                f"Disponibilidad: {value(f['availability'])} · Horario: {value(f['schedule'])} · "
                f"Experiencia: {value(f['experience'])} · Puede empezar: {value(f['start_date'])}"
            )
