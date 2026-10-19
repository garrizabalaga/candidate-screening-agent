"""Clientes de Amazon Bedrock: el modelo de chat (Converse API) y el de embeddings.

Solo saben hablar con Bedrock: no conocen prompts, etapas ni candidatos. Los
servicios los instancian y le pasan el system prompt, los mensajes y, si quieren
salida estructurada, la definición de la herramienta (o el texto a convertir en vector).

Credenciales: boto3 lee AWS_BEARER_TOKEN_BEDROCK del entorno (cargado desde el
.env por core/config.py), así que la API key no pasa por este código.
"""

import json
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import AWS_REGION


class LLMError(Exception):
    """Fallo al llamar a Bedrock (red, timeout, throttling, respuesta inesperada).
    Los servicios la capturan para responder con un mensaje fijo sin romper."""


class BedrockClient:
    def __init__(self, model_id: str, region: str = AWS_REGION, client: Any = None):
        self.model_id = model_id
        # `client` permite inyectar uno falso en los tests.
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=20,  # es un chat: mejor fallar rápido que dejar al candidato esperando
                retries={"max_attempts": 2, "mode": "standard"},  # reintenta throttling y errores 5xx
            ),
        )

    def converse(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        force_tool: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> list[dict]:
        """Llamada a la Converse API. Devuelve el contenido del mensaje del modelo:
        una lista de bloques ({"text": ...} y/o {"toolUse": ...}).

        `messages` va en formato Converse: [{"role": "user", "content": [{"text": "..."}]}].
        `tools` son toolSpecs. Por defecto el modelo decide si llama a alguna (auto);
        con `force_tool` se le obliga a llamar a esa, así la respuesta siempre es un
        JSON con la forma de su inputSchema.
        """
        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system}],
            "messages": messages,
            "inferenceConfig": {"temperature": temperature, "maxTokens": max_tokens},
        }
        if tools:
            choice = {"tool": {"name": force_tool}} if force_tool else {"auto": {}}
            kwargs["toolConfig"] = {"tools": [{"toolSpec": t} for t in tools], "toolChoice": choice}
        try:
            response = self._client.converse(**kwargs)
        except (BotoCoreError, ClientError) as e:
            raise LLMError(f"Bedrock converse failed: {e}") from e
        try:
            content = list(response["output"]["message"]["content"])
            if response.get("stopReason") == "max_tokens":
                # Respuesta cortada por el tope de tokens: se marca para que no se envíe a medias.
                content.append({TRUNCATED: True})
            return content
        except (KeyError, TypeError) as e:
            raise LLMError("Unexpected Bedrock response shape") from e

    def generate_structured(self, system: str, messages: list[dict], tool: dict, **kwargs: Any) -> dict:
        """Respuesta estructurada: el `input` con el que el modelo llamó a `tool`.
        Validarlo contra el modelo Pydantic es cosa del servicio."""
        content = self.converse(system, messages, tools=[tool], force_tool=tool["name"], **kwargs)
        for use in tool_uses(content):
            if use["name"] == tool["name"]:
                return use["input"]
        raise LLMError(f"Bedrock response did not call tool {tool['name']}")


EMBEDDING_DIMENSIONS = 1024


class EmbeddingClient:
    """Amazon Titan Text Embeddings V2, con invoke_model (la Converse API no hace embeddings).
    Pide vectores normalizados: la similitud coseno entre dos de ellos es su producto escalar."""

    def __init__(self, model_id: str, region: str = AWS_REGION, client: Any = None):
        self.model_id = model_id
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"}),
        )

    def embed(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True})
        try:
            response = self._client.invoke_model(
                modelId=self.model_id, body=body, contentType="application/json", accept="application/json"
            )
            return json.loads(response["body"].read())["embedding"]
        except (BotoCoreError, ClientError) as e:
            raise LLMError(f"Bedrock embedding failed: {e}") from e
        except (KeyError, TypeError, ValueError) as e:
            raise LLMError("Unexpected Bedrock embedding response") from e


TRUNCATED = "truncated"


def was_truncated(content: list[dict]) -> bool:
    """¿El modelo se quedó sin tokens a mitad de respuesta?"""
    return any(TRUNCATED in block for block in content)


def text_of(content: list[dict]) -> str:
    """El texto de una respuesta (los bloques de texto unidos)."""
    return "".join(block["text"] for block in content if "text" in block).strip()


def tool_uses(content: list[dict]) -> list[dict]:
    """Las llamadas a tools de una respuesta: [{"toolUseId", "name", "input"}, ...]."""
    return [block["toolUse"] for block in content if "toolUse" in block]
