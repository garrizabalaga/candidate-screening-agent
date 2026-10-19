"""Punto de entrada de la API. Crea la app, prepara la base de datos y registra
los routers.

Arrancar: uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.repositories.database import get_connection, init_db
from app.routes import candidates, chat, jobs, recruiters, voice

# Logs de la app (loggers "app.*") en la consola, desde INFO. Uvicorn solo configura
# los suyos: sin esto, Python únicamente muestra WARNING o más. Solo "app", para que
# las librerías (boto3...) no llenen la consola.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
logging.getLogger("app").addHandler(_handler)
logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Al arrancar: crear las tablas si no existen.
    conn = get_connection()
    init_db(conn)
    conn.close()
    yield


app = FastAPI(title="Grupo Sazón — screening de repartidores", lifespan=lifespan)

app.include_router(candidates.router)
app.include_router(chat.router)
app.include_router(jobs.router)
app.include_router(recruiters.router)
app.include_router(voice.router)
