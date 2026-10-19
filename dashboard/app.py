"""Dashboard de recruiters de Grupo Sazón: inicio con las métricas del proceso.

Arrancar (con la API levantada): streamlit run app.py
Las páginas están en pages/ (Streamlit las añade al menú lateral).

Las métricas se calculan aquí, a partir de GET /candidates. Con el volumen de la
demo es suficiente; con miles de candidatos irían a un endpoint de la API (o a
una vista agregada en la base de datos) para no descargar todos los registros.
"""

from collections import Counter
from datetime import datetime, timezone
from statistics import median

import altair as alt
import pandas as pd
import streamlit as st

import api_client
from ui import OUTCOME_LABEL, REASON_LABEL, STAGE_LABEL, call

st.set_page_config(page_title="Grupo Sazón · Selección", page_icon="🛵", layout="wide")
st.title("🛵 Grupo Sazón · Selección de repartidores")
st.caption(f"API: {api_client.API_URL}")

candidates = call(api_client.list_candidates)
if not candidates:
    st.info("Todavía no hay candidatos. Empieza en la página **Candidatos**.")
    st.stop()


def pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


now = datetime.now(timezone.utc)
contacted = [c for c in candidates if c["contacted_at"]]
responded = [c for c in contacted if c["last_candidate_message_at"]]
finished = [c for c in contacted if c["outcome"]]
qualified = [c for c in candidates if c["outcome"] == "qualified" or c["stage"] == "scheduling"]
booked = [c for c in candidates if c["interview"]]
upcoming = [c for c in booked if ts(c["interview"]["starts_at"]) > now]
in_review = [c for c in candidates if c["outcome"] == "manual_review"]

# --- Cifras clave: responden al problema del cliente -----------------------------------
# 60% no contestaba al teléfono -> tasa de respuesta. 80% del tiempo en no aptos -> lo
# que le llega al recruiter es solo la entrevista agendada o la revisión manual.

st.subheader("Cifras clave")
k = st.columns(5)
k[0].metric("Candidatos", len(candidates), help="Aplicaciones registradas")
k[1].metric("Tasa de respuesta", pct(len(responded), len(contacted)), help="Contactados que han contestado al menos una vez")
k[2].metric("Conversaciones terminadas", pct(len(finished), len(contacted)), help="Contactados con un resultado final")
k[3].metric("Entrevistas próximas", len(upcoming), help="Entrevistas agendadas a partir de ahora")
k[4].metric("Revisión manual", len(in_review), help="Casos que esperan a un recruiter")

durations = [
    (ts(c["last_candidate_message_at"]) - ts(c["contacted_at"])).total_seconds() / 60
    for c in finished
    if c["outcome"] != "abandoned" and c["last_candidate_message_at"]
]
if durations:
    minutes = median(durations)
    duration = f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h"
    st.caption(
        "Duración mediana de una conversación terminada (del primer contacto al último mensaje "
        f"del candidato): {duration}"
    )


def bars(data: dict[str, int], title: str) -> None:
    """Barras horizontales de una sola serie, en el orden dado (no alfabético)."""
    df = pd.DataFrame({"Etiqueta": list(data), "Candidatos": list(data.values())})
    chart = (
        alt.Chart(df, title=title)
        .mark_bar(cornerRadiusEnd=4, height={"band": 0.6})
        .encode(
            y=alt.Y("Etiqueta:N", sort=None, title=None),
            x=alt.X("Candidatos:Q", title=None, axis=alt.Axis(tickMinStep=1, grid=True)),
            tooltip=["Etiqueta", "Candidatos"],
        )
    )
    labels = chart.mark_text(align="left", dx=4).encode(text="Candidatos:Q")
    st.altair_chart((chart + labels).properties(height=max(120, 36 * len(df))), width="stretch")


left, right = st.columns(2)

with left:
    bars(
        {
            "Aplicaron": len(candidates),
            "Contactados": len(contacted),
            "Respondieron": len(responded),
            "Aptos": len(qualified),
            "Entrevista agendada": len(booked),
        },
        "Embudo",
    )
    outcomes = Counter(c["outcome"] for c in candidates)
    bars({OUTCOME_LABEL[o]: outcomes[o] for o in OUTCOME_LABEL if outcomes[o]}, "Resultados")

with right:
    abandoned = Counter(c["stage"] for c in candidates if c["outcome"] == "abandoned")
    if abandoned:
        bars({STAGE_LABEL[s]: abandoned[s] for s in STAGE_LABEL if abandoned[s]}, "Abandono: última etapa alcanzada")
    else:
        st.markdown("**Abandono: última etapa alcanzada**")
        st.caption("Nadie ha abandonado todavía.")

    reasons = Counter(c["disqualification_reason"] for c in candidates if c["outcome"] == "disqualified")
    if reasons:
        bars({REASON_LABEL.get(r, r): n for r, n in reasons.most_common()}, "Motivos de descarte")
    else:
        st.markdown("**Motivos de descarte**")
        st.caption("Ningún candidato descartado todavía.")

with st.expander("Ver los datos en tabla"):
    st.dataframe(
        pd.DataFrame(
            [
                {"Métrica": "Contactados", "Valor": len(contacted)},
                {"Métrica": "Respondieron", "Valor": len(responded)},
                {"Métrica": "Terminadas", "Valor": len(finished)},
                {"Métrica": "Aptos", "Valor": len(qualified)},
                {"Métrica": "Entrevistas agendadas", "Valor": len(booked)},
                {"Métrica": "Revisión manual", "Valor": len(in_review)},
            ]
        ),
        hide_index=True,
    )
