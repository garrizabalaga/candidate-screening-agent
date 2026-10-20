"""Vista del candidato: hablar con Guille como si fueras el candidato (sustituye a WhatsApp).

El chat es un @st.fragment y la conversación vive en la sesión: al enviar un mensaje
solo se vuelve a ejecutar el chat, tu mensaje aparece al instante y la respuesta se
añade cuando llega. "Actualizar" relee de la API (apertura, recordatorios).
"""

from datetime import datetime, timezone

import streamlit as st

import api_client
from ui import OUTCOME_LABEL, STAGE_LABEL, call, candidate_label, render_message

st.set_page_config(page_title="Vista del candidato", page_icon="🧪", layout="centered")
st.page_link("pages/0_Inicio.py", label="Volver al panel de recruiters", icon="⬅️")
st.title("🧪 Vista del candidato")
st.caption("Lo que vive el candidato. En producción escribe por WhatsApp o habla por voz; aquí lo simulas.")

candidates = call(api_client.list_candidates)
if not candidates:
    st.info("No hay candidatos. Registra uno en la página de Candidatos.")
    st.stop()

ids = [c["id"] for c in candidates]
by_id = {c["id"]: c for c in candidates}
default = ids.index(st.session_state["sim_candidate"]) if st.session_state.get("sim_candidate") in ids else 0
candidate_id = st.selectbox("Candidato", ids, index=default, format_func=lambda i: candidate_label(by_id[i]))
st.session_state["sim_candidate"] = candidate_id
country = by_id[candidate_id]["country"]

if not by_id[candidate_id]["contacted_at"]:
    st.warning("A este candidato todavía no le hemos escrito.")
    if st.button("📨 Lanzar primer contacto"):
        call(api_client.run_first_contact)
        st.rerun()
    st.stop()


def load(candidate_id: str) -> None:
    """Conversación y estado del candidato desde la API, guardados en la sesión."""
    st.session_state[f"messages_{candidate_id}"] = call(api_client.get_messages, candidate_id)
    st.session_state[f"status_{candidate_id}"] = next(
        c for c in call(api_client.list_candidates) if c["id"] == candidate_id
    )


@st.fragment
def chat(candidate_id: str) -> None:
    if f"messages_{candidate_id}" not in st.session_state:
        load(candidate_id)
    messages = st.session_state[f"messages_{candidate_id}"]

    status, refresh = st.columns([4, 1])
    if refresh.button("🔄 Actualizar", help="Vuelve a leer la conversación (p. ej. tras lanzar recordatorios)"):
        load(candidate_id)
        messages = st.session_state[f"messages_{candidate_id}"]
    s = st.session_state[f"status_{candidate_id}"]
    status.caption(f"Etapa: {STAGE_LABEL[s['stage']]} · {OUTCOME_LABEL[s['outcome']]}")

    box = st.container(height=520)
    with box:
        for m in messages:
            render_message(m, country)

    if text := st.chat_input("Escribe como si fueras el candidato…"):
        mine = {"role": "candidate", "text": text, "created_at": datetime.now(timezone.utc).isoformat()}
        messages.append(mine)
        with box:
            render_message(mine, country)
            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("Guille está escribiendo…"):
                    reply = call(api_client.send_message, candidate_id, text)
                st.write(reply)
        messages.append({"role": "agent", "text": reply, "created_at": datetime.now(timezone.utc).isoformat()})
        # El estado (etapa, resultado) puede haber cambiado: se refresca para la siguiente vez.
        st.session_state[f"status_{candidate_id}"] = next(
            c for c in call(api_client.list_candidates) if c["id"] == candidate_id
        )


chat(candidate_id)
