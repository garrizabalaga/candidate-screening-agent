# Grupo Sazón — Agente de screening de repartidores

Agente conversacional (Guille) que realiza el screening de candidatos a repartidor por mensajería, o por voz si el candidato lo prefiere, y agenda a los aptos directamente con un recruiter. Incluye un panel para que los recruiters supervisen el proceso.

| Problema del cliente | Solución |
| --- | --- |
| El 60 % de los candidatos no contesta las llamadas | Mensajería asíncrona: el sistema inicia el contacto y el candidato responde cuando puede, o abre una llamada de voz desde un enlace cuando le conviene. Recordatorios ante la inactividad. |
| El 80 % del tiempo de los recruiters se dedica a candidatos no aptos | Las preguntas eliminatorias (carné y zona) se hacen al principio. El recruiter solo recibe entrevistas ya agendadas, con un resumen del candidato, y una cola reducida de casos dudosos. |

## Funcionalidades

**Núcleo**
- Agente conversacional que recoge y valida los 7 campos del screening, con historial completo, repreguntas y salida estructurada (JSON validado con Pydantic).
- Persistencia en SQLite y resumen posterior a la conversación para el recruiter.
- Dashboard de recruiters: revisiones pendientes, próximas entrevistas, métricas, ficha de cada candidato con su conversación y calendario.
- Primer contacto y recordatorios implementados como jobs. En producción se ejecutarían periódicamente mediante un cron (EventBridge Scheduler); en la demo se lanzan manualmente desde el dashboard, con un reloj simulado para los recordatorios. El código es el mismo en ambos casos.

**Bonus**
- **Voz** (ElevenLabs): el mismo proceso, accesible desde un enlace en el mensaje de apertura.
- **Agenda de entrevistas** con 13 recruiters ficticios y sus horarios.
- **Re-engagement**: recordatorios a las 2 h y a las 24 h y cierre a las 72 h, en horario local.
- **RAG**: respuestas a partir de un FAQ de la empresa que se carga desde el dashboard.
- **Multi-idioma** (ES/EN), **guardrails**, **análisis de sentimiento** y **analítica** del proceso.
- **Tests** (174 casos) y **evals** con el LLM real.

## Arquitectura

```
                 ┌──────────────────────────── backend/ (FastAPI) ─────────────────────────────┐
 Candidato ──►   │ POST /chat ──────────────────────┐                                          │
 (chat)          │                                  ▼                                          │
                 │                     ChatService ─► AgentService ─► conexión al LLM (Claude)  │
 Candidato ──►   │ POST /voice/llm/chat/completions ▲      │  tools                            │
 (voz) ─► Eleven │   (Custom LLM, canal voz) ───────┘      ├─ record_answer ─► máquina de       │
          Labs   │ GET /voice/{id} ─► página de voz         │                   estados + agenda │
                 │                                          └─ search_faq ─► FAQ (RAG) + config │
                 │ POST /knowledge ─► embeddings (Titan) ─► knowledge_chunks                   │
                 │ Jobs (cron en prod.): primer contacto, recordatorios · Resumen en 2.º plano │
                 │ SQLite: candidates · messages · interviews · summaries · knowledge_chunks   │
                 └──────────────────────────────────▲──────────────────────────────────────────┘
                                                    │ HTTP
                 dashboard/ (Streamlit): panel de recruiters + vista del candidato (simulador de chat)
```

### El LLM conversa, el código decide

Un único agente LLM conduce la conversación y utiliza dos tools, que ejecutan una **máquina de estados determinista**. El LLM no puede cambiar de etapa, aprobar ni descartar: se limita a interpretar las respuestas del candidato y a redactar los mensajes que el código le solicita.

Cada turno sigue estos pasos:

1. Se guarda el mensaje del candidato.
2. **`record_answer`, de uso obligatorio** (temperatura 0): el LLM extrae los campos que aporta el mensaje, el idioma, si contiene una pregunta, el tono del candidato y si es ofensivo.
3. **El código decide**: Pydantic valida los campos y `advance()` determina la siguiente acción (preguntar, repreguntar, confirmar, descartar, enviar a revisión, ofrecer huecos o reservar).
4. **El agente redacta** el mensaje según ese objetivo y consulta `search_faq` si el candidato ha preguntado algo (temperatura 0,5).
5. **`reply_guard` valida el mensaje**: que incluya una pregunta cuando corresponde, que no contenga cifras inventadas y que respete la longitud. Si no cumple, se reescribe; si sigue sin cumplir, se utiliza un texto fijo.
6. El estado, los mensajes y la reserva se guardan en una única transacción.

Si la conexión al LLM falla **antes** de la decisión, se pide al candidato que repita el mensaje sin consumir una repregunta. Si falla **después**, la decisión se comunica con el texto fijo correspondiente.

### Flujo

`identidad → carné → zona → disponibilidad → horario → experiencia → fecha de inicio → agenda`

- Una respuesta negativa en una eliminatoria **se confirma antes de descartar**, de modo que un error de interpretación no puede descartar a un candidato.
- Una eliminatoria dudosa (carné caducado, ciudad no reconocida) se envía a **revisión manual**, pero se completan las preguntas para que el recruiter disponga del perfil completo.
- Resultados posibles: apto (entrevista agendada), descartado, revisión manual, abandonado y contacto erróneo.

## Decisiones clave

- **Agente híbrido (LLM + máquina de estados):** las reglas eliminatorias no dependen de un prompt y las decisiones son reproducibles y testeables. Un agente que decidiera el flujo por sí mismo podría descartar candidatos que deberían confirmarse e inventarse preguntas.
- **Claude Sonnet 4.6 en Amazon Bedrock:** mejor redacción y mejor adherencia a los objetivos y a la tool obligatoria (~3 s por turno). Se utiliza la región `eu-north-1` con perfiles `eu.*` para que los datos no salgan de la UE (RGPD). Se evaluaron también Nova, Haiku 4.5, DeepSeek, Kimi y Qwen: Nova Lite confundía campos y Nova Pro inventaba información; Haiku 4.5 es más rápido, pero su redacción es de menor calidad.
- **Textos fijos** para los mensajes que inicia la empresa (apertura, recordatorios, respuesta a una revisión), ya que en WhatsApp deben ser plantillas aprobadas.
- **SQLite con repositorios:** suficiente para la demo; en producción se sustituiría por RDS o DynamoDB sin modificar el resto del código.
- **Voz con ElevenLabs como Custom LLM:** ElevenLabs aporta únicamente la voz y el turno es el mismo que en el chat. Utilizar el LLM de ElevenLabs con las tools como webhooks impediría forzar `record_answer` y omitiría `reply_guard`.
- **RAG en una tabla de SQLite:** con un FAQ de decenas de preguntas, comparar la consulta con todos los fragmentos es exacto e inmediato. Un índice vectorial (FAISS, Bedrock Knowledge Bases) se justificaría con miles de documentos.
- **Ciudades y requisito del carné en configuración, no en el FAQ:** el código los utiliza para decidir, por lo que deben tener una única fuente de verdad.

## Estructura

```
backend/
  app/
    core/          configuración (.env, reglas, zonas de servicio)
    models/        modelos Pydantic: candidato, campos, mensajes, entrevistas, resumen, conocimiento
    routes/        API: candidatos, chat, voz, base de conocimiento, jobs, recruiters
    services/      chat, agente, máquina de estados, reply_guard, agenda, recordatorios, revisión, resumen, RAG
    tools/         record_answer y search_faq (definición y ejecución)
    prompts/       prompt del agente, preguntas por etapa, textos fijos, resumen, saludo de voz
    clients/       conexión al LLM: chat y embeddings
    repositories/  acceso a datos con SQL; la transacción la gestiona el servicio
  data/            ciudades con servicio, recruiters, FAQ de ejemplo; demo.db (local)
  scripts/         seed_demo (datos de demostración) y sample_conversations (conversaciones de ejemplo)
  evals/           evals con el LLM real; en results/, el último resultado y las conversaciones de ejemplo
  tests/           tests sin red ni base de datos real
dashboard/         Streamlit: inicio, candidatos, calendario, base de conocimiento y vista del candidato
```

## Puesta en marcha

Requisitos: Python 3.11+ y una cuenta de AWS con acceso a Bedrock en `eu-north-1` (para los modelos de Anthropic es necesario enviar una vez el formulario de caso de uso en la consola de Bedrock).

**Backend**
```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env          # completar AWS_BEARER_TOKEN_BEDROCK (API key de Bedrock)
.venv\Scripts\python -m pytest
.venv\Scripts\python -m scripts.seed_demo          # opcional: datos de demostración (60 días, ~800 candidatos)
.venv\Scripts\python -m uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
```

**Dashboard** (en otra terminal)
```powershell
cd dashboard
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\streamlit run app.py                 # http://localhost:8501
```

En Git Bash, los mismos comandos con `/` (`.venv/Scripts/python …`).

**FAQ**: en el dashboard, *Base de conocimiento* → cargar `backend/data/faq_grupo_sazon.md`.

**Voz** (opcional)
1. Exponer el backend mediante un túnel (ngrok, dev tunnels) e indicar su URL en `PUBLIC_BASE_URL`.
2. En `.env`: `ELEVENLABS_AGENT_ID` y `VOICE_LLM_API_KEY` (un secreto de longitud suficiente).
3. En el agente de ElevenLabs:
   - Custom LLM con URL `{PUBLIC_BASE_URL}/voice/llm` y la misma API key;
   - primer mensaje `{{saludo}}`;
   - en el system prompt, la línea `candidate_id={{candidate_id}}`;
   - tiempo de espera en cascada de 15 s, soft timeout de ~1,5 s y disposición baja.

### Demo

1. **Inicio**: seleccionar un recruiter en "Soy", consultar sus revisiones pendientes y métricas, y pulsar *Revisar* en un caso para aprobarlo o descartarlo.
2. **Probar como candidato**: formular una pregunta sobre el puesto, cargar el FAQ en *Base de conocimiento* y repetirla.
3. **Candidatos** → nueva aplicación → *Lanzar primer contacto* → conversar en la vista del candidato hasta reservar (o abrir el enlace de voz del mensaje); la entrevista aparece en el *Calendario*.
4. *Lanzar recordatorios* con horas simuladas (en producción, mediante el cron).

## Tests y evals

- **Tests** (`python -m pytest`, 174 casos, sin red ni base de datos real): reglas de negocio expresadas como tablas "situación → decisión esperada" (máquina de estados, agenda, recordatorios, `reply_guard`), el agente con un LLM simulado (protocolo del turno, fallos antes y después de decidir, reescritura), persistencia y API, RAG y voz.
- **Evals** (`python -m evals.run`, ~40 llamadas): miden el comportamiento en un entorno real, con el LLM de producción.
  - **Extracción** (18 casos): carné retirado o caducado, campos que no deben confundirse, varios campos en un mismo mensaje, idioma, preguntas, elección de hueco, lenguaje ofensivo y sentimiento.
  - **Escenarios** (8 conversaciones completas): descarte con confirmación, ciudad sin servicio, respuestas ambiguas, pregunta sin información, insulto, intento de manipulación, candidato frustrado y cambio de idioma.
  - Último resultado: **18/18 y 8/8** (`backend/evals/results/latest.json`). Los evals permitieron detectar y corregir varios errores del prompt, como clasificar un carné retirado como dudoso o deducir información no disponible.
- **Conversaciones de ejemplo** (`backend/evals/results/sample-conversations.md`): 7 conversaciones generadas con el agente real mediante `python -m scripts.sample_conversations`.

## Escalado y mejoras

**Escalado a 10.000 candidatos por semana** (diseño, no desplegado):
- La API se desplegaría en contenedores (ECS o App Runner) con HTTPS. Se utilizaría un webhook como el de WhatsApp y se encolarían los mensajes en SQS FIFO agrupados por candidato, para mantener el orden de cada conversación y procesar distintos candidatos en paralelo. Un worker ejecutaría cada turno.
- Los jobs se programarían con EventBridge Scheduler y el resumen se generaría como un mensaje más de la cola.
- La base de datos pasaría a RDS o DynamoDB detrás de los mismos repositorios, y la base de conocimiento a `pgvector` o Bedrock Knowledge Bases.
- Las métricas se calcularían en un endpoint o en una vista agregada en lugar de en el dashboard.
- Se ampliaría la cuota del LLM y se activarían reintentos adaptativos.

**Mejoras con más tiempo**:
- **Voz**: ElevenLabs trata el endpoint como un LLM sin estado y descarta peticiones cuando el candidato hace una pausa. En la versión actual, cada turno espera 1,5 s antes de procesarse por si llega la frase completa; la solución adecuada sería confirmar cada turno cuando aparece en la transcripción. Además, se utilizaría un enlace con token firmado y se registraría el canal de cada mensaje.
- **Latencia**: ~3 s por turno (2–3 llamadas al LLM). Se valoraría un modelo más rápido para la voz o la validación por frases en streaming.
- **Guardrails**: se añadiría Bedrock Guardrails como capa gestionada (filtros de contenido, ataques al prompt y datos personales) y el enmascaramiento de datos sensibles como DNI o IBAN.
- **RAG**: varios documentos, re-ranking y autenticación en la carga.
- **Plataforma**: autenticación en la API y el dashboard, migraciones de base de datos, versiones fijadas y logs sin datos personales.
- **Integración con un ATS**: recepción de candidaturas y envío de resultados mediante API.
