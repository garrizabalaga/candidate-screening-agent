"""Candidatos: tabla con filtros, alta, jobs de la demo y detalle con la conversación."""

from datetime import datetime, timedelta, timezone

import streamlit as st

import api_client
from ui import (
    COUNTRY_LABEL,
    FLAG_LABEL,
    OUTCOME_LABEL,
    REASON_LABEL,
    STAGE_LABEL,
    call,
    local_time,
    show_conversation,
    value,
)

st.set_page_config(page_title="Candidatos", page_icon="📋", layout="wide")
st.title("📋 Candidatos")

if done := st.session_state.pop("review_done", None):
    st.success(f"Revisión resuelta. Mensaje enviado a {done['name']}: «{done['message']}»")

# --- Acciones de la demo ------------------------------------------------------------
# En producción las aplicaciones llegan del formulario/ATS y los jobs los lanza un scheduler.

col_new, col_jobs = st.columns(2)

with col_new.expander("➕ Nueva aplicación"):
    with st.form("new_candidate", clear_on_submit=True):
        name = st.text_input("Nombre completo")
        phone = st.text_input("Teléfono")
        country = st.selectbox("País de la oferta", ["ES", "MX"], format_func=COUNTRY_LABEL.get)
        if st.form_submit_button("Registrar") and name and phone:
            call(api_client.create_candidate, name, phone, country)
            st.success(f"{name} registrado. Queda pendiente de primer contacto.")

with col_jobs.expander("⏱️ Jobs"):
    if st.button("📨 Lanzar primer contacto", help="Escribe la apertura a los candidatos que aún no han recibido nada"):
        contacted = call(api_client.run_first_contact)
        st.success(f"Primer contacto enviado a {len(contacted)} candidato(s).")
    hours = st.number_input("Simular que han pasado (horas)", min_value=0, max_value=200, value=25)
    if st.button("🔔 Lanzar recordatorios"):
        now = datetime.now(timezone.utc) + timedelta(hours=hours)
        sent = call(api_client.run_reminders, now)
        st.success(f"Recordatorios enviados: {len(sent)}" + (f" ({', '.join(s['action'] for s in sent)})" if sent else ""))

# --- Tabla con filtros ----------------------------------------------------------------

candidates = call(api_client.list_candidates)
if not candidates:
    st.info("No hay candidatos todavía. Registra una aplicación arriba.")
    st.stop()

f1, f2, f3 = st.columns(3)
outcomes = f1.multiselect("Resultado", list(OUTCOME_LABEL), format_func=OUTCOME_LABEL.get)
countries = f2.multiselect("País", list(COUNTRY_LABEL), format_func=COUNTRY_LABEL.get)
stages = f3.multiselect("Etapa", list(STAGE_LABEL), format_func=STAGE_LABEL.get)

rows = [
    c for c in candidates
    if (not outcomes or c["outcome"] in outcomes)
    and (not countries or c["country"] in countries)
    and (not stages or c["stage"] in stages)
]


def table_row(c: dict) -> dict:
    interview = c["interview"]
    return {
        "Nombre": c["full_name"],
        "País": COUNTRY_LABEL[c["country"]],
        "Ciudad": c["fields"]["city"] or "",
        "Etapa": STAGE_LABEL[c["stage"]],
        "Resultado": OUTCOME_LABEL[c["outcome"]],
        "Motivo": REASON_LABEL.get(c["disqualification_reason"], ""),
        "Entrevista": local_time(interview["starts_at"], c["country"]) if interview else "",
        "Recruiter": interview["recruiter_name"] if interview else "",
        "Aplicó": local_time(c["applied_at"], c["country"]),
        "Contactado": "Sí" if c["contacted_at"] else "Pendiente",
    }


st.caption(f"{len(rows)} de {len(candidates)} candidatos. Selecciona una fila para ver el detalle.")
selection = st.dataframe(
    [table_row(c) for c in rows],
    width="stretch",
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

# --- Detalle del candidato seleccionado ------------------------------------------------

if not selection.selection.rows:
    st.stop()
c = rows[selection.selection.rows[0]]

st.divider()
head, action = st.columns([4, 1])
head.subheader(c["full_name"])
head.caption(f"{c['phone']} · {COUNTRY_LABEL[c['country']]} · {OUTCOME_LABEL[c['outcome']]}")
if action.button("💬 Abrir en el simulador"):
    st.session_state["sim_candidate"] = c["id"]
    st.switch_page("pages/2_Simulador.py")

# --- Revisión manual: el recruiter decide y la respuesta le llega al candidato por el chat ---

if c["outcome"] == "manual_review":
    with st.container(border=True):
        st.markdown("**🟡 Pendiente de revisión**: el candidato espera respuesta por este chat (24-48 h).")
        for flag in c["review_flags"]:
            st.markdown(f"- {FLAG_LABEL.get(flag['reason'], flag['reason'])}" + (f": «{flag['raw_answer']}»" if flag["raw_answer"] else ""))
        approve, reject, _ = st.columns([1, 1, 3])
        decision = None
        if approve.button("✅ Aprobar y ofrecer entrevista", type="primary"):
            decision = "approve"
        if reject.button("❌ Descartar"):
            decision = "reject"
        if decision:
            try:
                message = api_client.review(c["id"], decision)
            except api_client.APIError as e:
                st.error("No hay huecos de entrevista libres." if "no interview slots" in str(e) else f"Error de la API: {e}")
            else:
                st.session_state["review_done"] = {"name": c["full_name"], "message": message}
                st.rerun()  # la tabla y el detalle muestran el nuevo estado

data, chat = st.columns([1, 2])
with data:
    st.markdown("**Datos del screening**")
    f = c["fields"]
    st.table({
        "Campo": ["Identidad", "Carné", "Ciudad", "Disponibilidad", "Horario", "Experiencia", "Fecha de inicio"],
        "Valor": [value(f["identity_confirmed"]), value(f["license"]), value(f["city"]), value(f["availability"]),
                  value(f["schedule"]), value(f["experience"]), value(f["start_date"])],
    })
    if c["interview"]:
        st.success(
            f"Entrevista: {local_time(c['interview']['starts_at'], c['country'])} con {c['interview']['recruiter_name']}"
        )
    if c["review_flags"] and c["outcome"] != "manual_review":
        st.caption("Pasó por revisión manual: " + ", ".join(FLAG_LABEL.get(f["reason"], f["reason"]) for f in c["review_flags"]))

    # Resumen post-conversación: los datos clave ya están arriba; aquí, las notas del LLM.
    st.markdown("**📝 Resumen para el recruiter**")
    summary = call(api_client.get_summary, c["id"])
    if summary is None:
        st.caption("Se genera automáticamente al cerrarse la conversación.")
    elif summary["notes"] is None:
        st.caption("No se pudieron generar las notas (fallo del LLM). Los datos clave de arriba son correctos.")
    else:
        notes = summary["notes"]
        sections = [("Contexto", notes["context"]), ("Preguntas sin responder", notes["open_questions"]),
                    ("A confirmar en la entrevista", notes["alerts"])]
        if not any(items for _, items in sections):
            st.caption("Nada que destacar fuera de los datos del screening.")
        for title, items in sections:
            if items:
                st.markdown(f"*{title}*\n" + "\n".join(f"- {item}" for item in items))
        minutes = summary["duration_minutes"]
        duration = f" · conversación de {minutes:.0f} min" if minutes is not None else ""
        st.caption(f"{summary['message_count']} mensajes{duration} · generado {local_time(summary['generated_at'], c['country'])}")
    if st.button("🔄 Regenerar resumen" if summary else "📝 Generar resumen"):
        with st.spinner("Generando resumen…"):
            call(api_client.generate_summary, c["id"])
        st.rerun()
with chat:
    st.markdown("**Conversación**")
    show_conversation(call(api_client.get_messages, c["id"]), c["country"])
