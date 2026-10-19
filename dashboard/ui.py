"""Utilidades de presentación compartidas por las páginas: etiquetas en español,
fechas en hora local y gestión de errores de la API."""

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

import api_client

COUNTRY_TZ = {"ES": ZoneInfo("Europe/Madrid"), "MX": ZoneInfo("America/Mexico_City")}
COUNTRY_LABEL = {"ES": "España", "MX": "México"}

STAGE_LABEL = {
    "identity": "Identidad",
    "license": "Carné",
    "zone": "Zona",
    "availability": "Disponibilidad",
    "schedule": "Horario",
    "experience": "Experiencia",
    "start_date": "Fecha de inicio",
    "scheduling": "Agendando entrevista",
    "done": "Terminada",
}
OUTCOME_LABEL = {
    None: "En curso",
    "qualified": "✅ Apto (entrevista)",
    "disqualified": "❌ Descartado",
    "manual_review": "🟡 Revisión manual",
    "abandoned": "💤 Abandonado",
    "wrong_contact": "📵 Contacto erróneo",
}
REASON_LABEL = {"no_license": "Sin carné", "out_of_zone": "Fuera de zona", "rejected_by_recruiter": "Descartado en revisión"}
FLAG_LABEL = {
    "identity_unclear": "Identidad dudosa",
    "license_unclear": "Carné dudoso (caducado, en trámite, extranjero…)",
    "zone_unclear": "Zona dudosa",
    "no_slot_fits": "Ningún hueco de entrevista le encaja",
    "scheduling_unclear": "No eligió hueco de entrevista",
    "no_slots_available": "No había huecos libres",
}
VALUE_LABEL = {
    "yes": "Sí", "no": "No", "unclear": "No está claro",
    "full_time": "Jornada completa", "part_time": "Media jornada", "weekends": "Fines de semana",
    "morning": "Mañana", "afternoon": "Tarde", "evening": "Noche", "flexible": "Flexible",
    "immediately": "Inmediatamente",
}
_DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
DAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def call(fn, *args, **kwargs):
    """Llama a la API; si falla, muestra el error y para la página (sin traceback)."""
    try:
        return fn(*args, **kwargs)
    except api_client.APIError as e:
        st.error(f"Error de la API: {e}")
        st.stop()


def local_time(iso: str | None, country: str, with_day: bool = True) -> str:
    if not iso:
        return ""
    t = datetime.fromisoformat(iso).astimezone(COUNTRY_TZ[country])
    return f"{_DAYS[t.weekday()]} {t:%d/%m %H:%M}" if with_day else f"{t:%H:%M}"


def value(v) -> str:
    """Un campo del screening en lenguaje natural."""
    if v is None:
        return "—"
    if isinstance(v, dict):  # experiencia
        years = v["years"]
        platforms = ", ".join(v["platforms"]) or "sin plataformas"
        return "Sin experiencia" if years == 0 else f"{years:g} años ({platforms})"
    return VALUE_LABEL.get(v, str(v))


def candidate_label(c: dict) -> str:
    return f"{c['full_name']} · {COUNTRY_LABEL[c['country']]} · {OUTCOME_LABEL[c['outcome']]}"


def render_message(m: dict, country: str) -> None:
    agent = m["role"] == "agent"
    with st.chat_message("assistant" if agent else "user", avatar="🤖" if agent else "👤"):
        st.write(m["text"])
        st.caption(local_time(m["created_at"], country))


def show_conversation(messages: list[dict], country: str) -> None:
    if not messages:
        st.info("Todavía no hay mensajes: falta lanzar el primer contacto.")
    for m in messages:
        render_message(m, country)
