"""Inicio del panel de recruiters: pendientes de revisión y próximas entrevistas,
cifras del periodo frente al anterior y gráficas del proceso.

Las métricas se calculan aquí, a partir de GET /candidates: suficiente para la demo;
con miles de candidatos irían a un endpoint o a una vista agregada.
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median

import altair as alt
import pandas as pd
import streamlit as st

import api_client
from ui import COUNTRY_LABEL, COUNTRY_TZ, FLAG_LABEL, STAGE_LABEL, call, local_time

st.set_page_config(page_title="Inicio · Grupo Sazón", page_icon="🛵", layout="wide")

SERIES = {"Candidaturas": "#2a78d6", "Respondieron": "#eb6834", "Aptos": "#1baf7a"}
FUNNEL_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#184f95"]
ACCENT, MUTED = "#2a78d6", "#c3c2b7"
RESULTS = {
    "qualified": ("Apto", "#0ca30c"),
    "manual_review": ("Revisión", "#fab219"),
    "disqualified": ("Descartado", "#d03b3b"),
    "abandoned": ("Abandono", "#898781"),
    "wrong_contact": ("Contacto erróneo", "#c3c2b7"),
    None: ("En curso", "#2a78d6"),
}
REVIEW_PROMISE_HOURS = 48  # al candidato se le dice "te responderemos en 24-48 h"
LIST_HEIGHT = 220

now = datetime.now(timezone.utc)
candidates = call(api_client.list_candidates)
recruiters = {r["id"]: r for r in call(api_client.list_recruiters)}
me = recruiters.get(st.session_state.get("me")) or next(iter(recruiters.values()))

# --- Cabecera y filtros ---------------------------------------------------------------

head, period_col, country_col = st.columns([3, 2, 2], vertical_alignment="bottom")
head.markdown(f"#### Hola, {me['name'].split()[0]}")
head.caption(local_time(now.isoformat(), me["country"]))
days = period_col.segmented_control("Periodo", [7, 14, 30], default=14, format_func=lambda d: f"{d} días") or 14
country = country_col.segmented_control(
    "País", ["Todos", "ES", "MX"], default=me["country"], format_func=lambda c: COUNTRY_LABEL.get(c, c)
) or "Todos"

if not candidates:
    st.info("Todavía no hay candidatos. Empieza en la página **Candidatos**.")
    st.stop()

pool = [c for c in candidates if country == "Todos" or c["country"] == country]


def ts(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


def local_day(c: dict) -> date:
    return ts(c["applied_at"]).astimezone(COUNTRY_TZ[c["country"]]).date()


def open_candidate(candidate_id: str) -> None:
    st.session_state["open_candidate"] = candidate_id
    st.switch_page("pages/1_Candidatos.py")


# --- 1. Lo pendiente: revisiones y próximas entrevistas --------------------------------------

review, agenda = st.columns([3, 2])

with review.container(border=True):
    pending = sorted(
        (c for c in pool if c["outcome"] == "manual_review"),
        key=lambda c: ts(c["last_candidate_message_at"] or c["applied_at"]),
    )
    late = [c for c in pending if now - ts(c["last_candidate_message_at"]) > timedelta(hours=REVIEW_PROMISE_HOURS)]
    st.markdown(f"**🟡 Pendientes de revisión · {len(pending)}**")
    if not pending:
        st.caption("Nada pendiente. 🎉")
    else:
        st.caption(
            f"{len(late)} fuera de plazo (más de {REVIEW_PROMISE_HOURS} h)" if late
            else f"Todos dentro del plazo de {REVIEW_PROMISE_HOURS} h prometido al candidato"
        )
        with st.container(height=LIST_HEIGHT, border=False):
            for c in pending:
                hours = (now - ts(c["last_candidate_message_at"])).total_seconds() / 3600
                mark = "🔴" if hours > REVIEW_PROMISE_HOURS else "🟡" if hours > 24 else "⚪"
                reason = FLAG_LABEL.get(c["review_flags"][-1]["reason"], "") if c["review_flags"] else ""
                name, why, wait, action = st.columns([3, 4, 2, 2], vertical_alignment="center")
                name.markdown(f"**{c['full_name']}**  \n{COUNTRY_LABEL[c['country']]}")
                why.caption(reason)
                wait.markdown(f"{mark} {hours:.0f} h")
                # No con on_click: los callbacks corren antes de que app.py registre las páginas.
                if action.button("Revisar", key=f"review_{c['id']}"):
                    open_candidate(c["id"])

with agenda.container(border=True):
    today = now.astimezone(COUNTRY_TZ[me["country"]]).date()
    upcoming = [
        e for e in call(api_client.recruiter_calendar, me["id"], today, today + timedelta(days=7))
        if ts(e["interview"]["starts_at"]) > now
    ]
    st.markdown(f"**📅 Tus próximas entrevistas · {len(upcoming)}**")
    st.caption("Próximos 7 días" if upcoming else "Ninguna en los próximos 7 días.")
    if upcoming:
        with st.container(height=LIST_HEIGHT, border=False):
            for e in upcoming:
                st.markdown(f"{local_time(e['interview']['starts_at'], me['country'])} · {e['candidate']['full_name']}")

# --- 2. Cifras del periodo frente al anterior -----------------------------------------------

end = now.astimezone(COUNTRY_TZ["ES"]).date()
window = [end - timedelta(days=i) for i in reversed(range(days))]
previous_start = window[0] - timedelta(days=days)


def in_range(c: dict, start: date, stop: date) -> bool:
    return start <= local_day(c) <= stop


cohort = [c for c in pool if in_range(c, window[0], end)]
before = [c for c in pool if in_range(c, previous_start, window[0] - timedelta(days=1))]


def responded(c: dict) -> bool:
    return c["last_candidate_message_at"] is not None


def qualified(c: dict) -> bool:
    return c["outcome"] == "qualified" or c["stage"] == "scheduling"


def rate(group: list[dict], test) -> float | None:
    contacted = [c for c in group if c["contacted_at"]]
    return sum(map(test, contacted)) / len(contacted) if contacted else None


def finish_minutes(group: list[dict]) -> float | None:
    times = [
        (ts(c["last_candidate_message_at"]) - ts(c["contacted_at"])).total_seconds() / 60
        for c in group
        if c["outcome"] in ("qualified", "disqualified", "manual_review") and c["last_candidate_message_at"]
    ]
    return median(times) if times else None


def daily(test) -> list[int]:
    return [sum(1 for c in cohort if local_day(c) == d and test(c)) for d in window]


def daily_rate(test) -> list[float]:
    out = []
    for d in window:
        r = rate([c for c in cohort if local_day(c) == d], test)
        out.append(round(r * 100) if r is not None else 0)
    return out


def delta_pts(a: float | None, b: float | None) -> str | None:
    return f"{round((a - b) * 100) + 0:+d} pts" if a is not None and b is not None else None  # + 0: sin "-0"


vs = f"vs. {days} días anteriores"
k = st.columns(5)
k[0].metric("Candidaturas", len(cohort), len(cohort) - len(before), chart_data=daily(lambda c: True),
            border=True, delta_description=vs)
r_now, r_before = rate(cohort, responded), rate(before, responded)
k[1].metric("Tasa de respuesta", f"{r_now:.0%}" if r_now is not None else "—", delta_pts(r_now, r_before),
            chart_data=daily_rate(responded), border=True, delta_description=vs,
            help="Contactados que han contestado al menos una vez")
k[2].metric("Aptos", sum(map(qualified, cohort)), sum(map(qualified, cohort)) - sum(map(qualified, before)),
            chart_data=daily(qualified), border=True, delta_description=vs, help="Superaron el screening")
a_now, a_before = rate(cohort, lambda c: c["outcome"] == "abandoned"), rate(before, lambda c: c["outcome"] == "abandoned")
k[3].metric("Abandono", f"{a_now:.0%}" if a_now is not None else "—", delta_pts(a_now, a_before),
            delta_color="inverse", chart_data=daily_rate(lambda c: c["outcome"] == "abandoned"), chart_type="bar",
            border=True, delta_description=vs,
            help="Contactados que dejaron de contestar. Una conversación se da por abandonada a las 72 h sin "
                 "respuesta: en los últimos días la cifra todavía puede subir.")
m_now, m_before = finish_minutes(cohort), finish_minutes(before)
k[4].metric("Tiempo hasta terminar", f"{m_now:.0f} min" if m_now is not None else "—",
            f"{m_now - m_before:+.0f} min" if m_now is not None and m_before is not None else None,
            delta_color="inverse", border=True, delta_description=vs,
            help="Mediana, del primer contacto al último mensaje, en conversaciones terminadas")

# --- 3. Gráficas ---------------------------------------------------------------------------

AXIS = dict(labelColor="#898781", titleColor="#898781", domainColor=MUTED, tickColor=MUTED, gridColor="#e1e0d9")


def activity_chart() -> alt.Chart:
    """Por día de candidatura: candidaturas, cuántas respondieron y cuántas fueron aptas."""
    wide = pd.DataFrame({"Día": pd.to_datetime(window), **{s: daily(t) for s, t in zip(
        SERIES, (lambda c: True, responded, qualified))}})
    base = alt.Chart(wide).encode(x=alt.X("Día:T", title=None, axis=alt.Axis(format="%d %b", grid=False, **AXIS)))
    lines = base.transform_fold(list(SERIES), as_=["Serie", "Candidatos"]).mark_line(
        strokeWidth=2, interpolate="monotone").encode(
        y=alt.Y("Candidatos:Q", title=None, axis=alt.Axis(tickMinStep=1, **AXIS)),
        color=alt.Color("Serie:N", scale=alt.Scale(domain=list(SERIES), range=list(SERIES.values())),
                        legend=alt.Legend(orient="top", title=None, labelColor="#898781")),
    )
    hover = alt.selection_point(fields=["Día"], nearest=True, on="pointerover", empty=False)
    rule = base.mark_rule(color=MUTED).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("Día:T", format="%A %d %b"), *list(SERIES)],
    ).add_params(hover)
    return (lines + rule).properties(height=260, title="Actividad diaria (por día de candidatura)")


def funnel_chart() -> alt.Chart:
    steps = [
        ("Aplicaron", cohort),
        ("Contactados", [c for c in cohort if c["contacted_at"]]),
        ("Respondieron", [c for c in cohort if responded(c)]),
        ("Aptos", [c for c in cohort if qualified(c)]),
        ("Entrevista agendada", [c for c in cohort if c["interview"]]),
    ]
    rows = []
    for i, (name, group) in enumerate(steps):
        prev = len(steps[i - 1][1]) if i else None
        label = f"{len(group)}" + (f"  ·  {len(group) / prev:.0%}" if prev else "")
        rows.append({"Paso": name, "Candidatos": len(group), "Etiqueta": label})
    df = pd.DataFrame(rows)
    names = [s for s, _ in steps]
    bars = alt.Chart(df).mark_bar(height=22, cornerRadiusEnd=4).encode(
        y=alt.Y("Paso:N", sort=names, title=None, axis=alt.Axis(ticks=False, domain=False, labelColor="#52514e")),
        x=alt.X("Candidatos:Q", title=None, axis=None),
        color=alt.Color("Paso:N", sort=names, scale=alt.Scale(domain=names, range=FUNNEL_RAMP), legend=None),
        tooltip=["Paso", "Candidatos"],
    )
    text = bars.mark_text(align="left", dx=6, color="#52514e").encode(text="Etiqueta:N", color=alt.value("#52514e"))
    return (bars + text).properties(height=230, title="Embudo · conversión entre pasos")


def dropoff_chart() -> alt.Chart | None:
    lost = Counter(c["stage"] for c in cohort if c["outcome"] == "abandoned")
    if not lost:
        return None
    order = [s for s in STAGE_LABEL if lost[s]]
    worst = max(order, key=lambda s: lost[s])
    df = pd.DataFrame([{"Etapa": STAGE_LABEL[s], "Abandonos": lost[s], "Peor": s == worst} for s in order])
    bars = alt.Chart(df).mark_bar(height=22, cornerRadiusEnd=4).encode(
        y=alt.Y("Etapa:N", sort=[STAGE_LABEL[s] for s in order], title=None,
                axis=alt.Axis(ticks=False, domain=False, labelColor="#52514e")),
        x=alt.X("Abandonos:Q", title=None, axis=None),
        color=alt.condition("datum.Peor", alt.value(ACCENT), alt.value(MUTED)),
        tooltip=["Etapa", "Abandonos"],
    )
    text = bars.mark_text(align="left", dx=6).encode(text="Abandonos:Q", color=alt.value("#52514e"))
    return (bars + text).properties(height=230, title="Abandono · última etapa a la que llegaron")


def results_chart() -> alt.Chart:
    counts = Counter(c["outcome"] for c in cohort)
    df = pd.DataFrame([
        {"Resultado": label, "Candidatos": counts[o], "Parte": counts[o] / len(cohort), "Orden": i}
        for i, (o, (label, _)) in enumerate(RESULTS.items()) if counts[o]
    ])
    labels = [label for label, _ in RESULTS.values()]
    colors = [color for _, color in RESULTS.values()]
    return alt.Chart(df).mark_bar(height=24, stroke="white", strokeWidth=2).encode(
        x=alt.X("Candidatos:Q", stack="normalize", title=None, axis=alt.Axis(format="%", **AXIS)),
        color=alt.Color("Resultado:N", sort=labels, scale=alt.Scale(domain=labels, range=colors),
                        legend=alt.Legend(orient="top", title=None, labelColor="#898781")),
        order=alt.Order("Orden:Q"),
        tooltip=["Resultado", "Candidatos", alt.Tooltip("Parte:Q", format=".0%")],
    ).properties(height=70, title="¿Cómo terminan? · reparto de resultados")


if not cohort:
    st.info("No hay candidaturas en este periodo.")
    st.stop()

st.altair_chart(activity_chart(), width="stretch")
left, right = st.columns(2)
left.altair_chart(funnel_chart(), width="stretch")
dropoff = dropoff_chart()
if dropoff is not None:
    right.altair_chart(dropoff, width="stretch")
else:
    right.caption("Nadie ha abandonado en este periodo.")
st.altair_chart(results_chart(), width="stretch")

with st.expander("Ver los datos en tabla"):
    counts = Counter(c["outcome"] for c in cohort)
    st.dataframe(
        pd.DataFrame([{"Resultado": label, "Candidatos": counts[o]} for o, (label, _) in RESULTS.items()]),
        hide_index=True,
    )
