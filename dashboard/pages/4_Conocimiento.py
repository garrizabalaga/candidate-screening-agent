"""Base de conocimiento: subir el FAQ (Markdown) y ver las preguntas cargadas."""

from datetime import datetime

import streamlit as st

import api_client
from ui import call

st.set_page_config(page_title="Base de conocimiento", page_icon="📚", layout="wide")
st.title("📚 Base de conocimiento")
st.caption("FAQ en Markdown: cada `## Pregunta` es una entrada.")

with st.container(border=True):
    file = st.file_uploader("FAQ en Markdown", type=["md"])
    # Con botón y no al soltar el archivo: el uploader lo conserva entre recargas y se
    # regenerarían los embeddings en cada interacción.
    if st.button("Subir y regenerar la base", type="primary", disabled=file is None):
        with st.spinner("Calculando los embeddings de cada pregunta…"):
            try:
                result = api_client.upload_faq(file.name, file.getvalue())
            except api_client.APIError as e:
                st.error(f"No se ha podido cargar el FAQ (el anterior se mantiene). {e}")
            else:
                st.success(f"FAQ cargado: {result['questions']} preguntas de {result['source']}.")

entries = call(api_client.list_faq)
if not entries:
    st.info("Todavía no hay FAQ cargado.")
    st.stop()

loaded = datetime.fromisoformat(entries[0]["created_at"]).astimezone()
st.markdown(f"**{len(entries)} preguntas** · `{entries[0]['source']}` · cargado el {loaded:%d/%m/%Y %H:%M}")
for e in entries:
    with st.expander(e["question"]):
        st.markdown(e["answer"])
