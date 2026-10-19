"""Punto de entrada de la API. Crea la app, prepara la base de datos y registra
los routers.

Arrancar: uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.repositories.database import get_connection, init_db
from app.routes import candidates, chat, jobs, recruiters


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
