"""Tools del agente. Cada módulo contiene la definición de una tool (nombre,
descripción y esquema de argumentos) y lo que ejecuta."""

from app.tools import record_answer, search_faq

SPECS = [record_answer.SPEC, search_faq.SPEC]
