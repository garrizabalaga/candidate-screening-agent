"""Dashboard de Grupo Sazón: punto de entrada y menú lateral.

Arrancar (con la API levantada): streamlit run app.py

Tiene dos partes, para dos públicos distintos:
- Panel de recruiters (Inicio, Candidatos, Calendario, Base de conocimiento): el menú lateral.
- Vista del candidato (simulador): fuera del menú, se abre con el botón de abajo.
  En producción el candidato no usa este front (escribe por WhatsApp y habla en
  la página de voz de ElevenLabs); aquí sirve para probar la conversación.

El menú automático de Streamlit (todo lo que hay en pages/) se oculta y se dibuja
a mano, para que el simulador no parezca una página más del panel.
"""

from pathlib import Path

import streamlit as st

import api_client
from ui import COUNTRY_LABEL, call

ASSETS = Path(__file__).parent / "assets"

inicio =st.Page("pages/0_Inicio.py", title="Inicio", icon="🏠", default=True)
candidatos = st.Page("pages/1_Candidatos.py", title="Candidatos", icon="📋")
calendario = st.Page("pages/3_Calendario.py", title="Calendario", icon="📅")
conocimiento = st.Page("pages/4_Conocimiento.py", title="Base de conocimiento", icon="📚")
simulador = st.Page("pages/2_Simulador.py", title="Vista del candidato", icon="🧪")

page = st.navigation([inicio, candidatos, calendario, conocimiento, simulador], position="hidden")

# Streamlit no permite anclar algo al fondo del lateral. Con CSS: el lateral pasa a
# ser una columna flexible a toda altura, cada capa que contiene el botón se estira
# (flex: 1) y el bloque del botón se empuja abajo (margin-top: auto); si Streamlit lo
# estira para llenar el hueco, coloca su contenido al final (justify-content). No depende de cuántas
# capas intermedias ponga Streamlit; si cambia su estructura, el botón solo vuelve a
# quedar justo debajo del menú.
SIDEBAR_CSS = """
<style>
[data-testid="stSidebarContent"] { display: flex; flex-direction: column; }
[data-testid="stSidebarUserContent"],
[data-testid="stSidebarUserContent"] div:has(.st-key-candidate_view) {
    flex: 1; display: flex; flex-direction: column;
}
.st-key-candidate_view { margin-top: auto; justify-content: flex-end; }
</style>
"""

if page.url_path != simulador.url_path:  # en la vista del candidato no hay menú de recruiter
    st.logo(ASSETS / "logo.svg", size="large", icon_image=ASSETS / "icon.svg")
    with st.sidebar:
        st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
        # Quién usa el panel (uno de los recruiters): personaliza el inicio. Sin login en la demo.
        recruiters = call(api_client.list_recruiters)
        by_id = {r["id"]: r for r in recruiters}
        # "me" vive aparte del widget: Streamlit borra el estado de un widget en las páginas
        # que no lo dibujan (la vista del candidato) y se perdería al volver.
        st.session_state.setdefault("me", recruiters[0]["id"])
        st.session_state["me_select"] = st.session_state["me"]
        st.selectbox(
            "Soy", list(by_id), key="me_select",
            format_func=lambda i: f"{by_id[i]['name']} · {COUNTRY_LABEL[by_id[i]['country']]}",
            on_change=lambda: st.session_state.update(me=st.session_state["me_select"]),
        )
        st.page_link(inicio)
        st.page_link(candidatos)
        st.page_link(calendario)
        st.page_link(conocimiento)
        with st.container(key="candidate_view"):
            st.divider()
            if st.button("🧪 Probar como candidato", type="primary", width="stretch"):
                st.switch_page(simulador)

page.run()
