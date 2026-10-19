"""Tests del cliente de Bedrock con un cliente boto3 falso (sin red)."""

import pytest
from botocore.exceptions import ClientError

from app.clients.aws_client import BedrockClient, LLMError, text_of, was_truncated

TOOL = {
    "name": "record_fields",
    "description": "Guarda los campos extraídos",
    "inputSchema": {"json": {"type": "object", "properties": {"city": {"type": "string"}}}},
}
MESSAGES = [{"role": "user", "content": [{"text": "vivo en Madrid"}]}]


class FakeBoto:
    """Guarda la última petición y devuelve una respuesta fija o lanza un error."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.last_kwargs = None

    def converse(self, **kwargs):
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def reply(*content):
    return {"output": {"message": {"role": "assistant", "content": list(content)}}}


def test_converse_sends_the_request_and_returns_the_content():
    fake = FakeBoto(reply({"text": "Hola, "}, {"text": "Ana."}))
    content = BedrockClient(model_id="m", client=fake).converse("sys", MESSAGES, temperature=0.5)
    assert text_of(content) == "Hola, Ana."
    assert fake.last_kwargs["modelId"] == "m" and fake.last_kwargs["system"] == [{"text": "sys"}]
    assert fake.last_kwargs["inferenceConfig"]["temperature"] == 0.5
    assert "toolConfig" not in fake.last_kwargs


def test_generate_structured_forces_the_tool_and_returns_its_input():
    fake = FakeBoto(reply({"toolUse": {"toolUseId": "1", "name": "record_fields", "input": {"city": "Madrid"}}}))
    assert BedrockClient(model_id="m", client=fake).generate_structured("sys", MESSAGES, TOOL) == {"city": "Madrid"}
    assert fake.last_kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": "record_fields"}}


# Cualquier fallo de Bedrock acaba en LLMError: los servicios lo capturan y responden con un texto fijo.
@pytest.mark.parametrize("fake", [
    FakeBoto(reply({"text": "no quiero"})),                       # no llama a la tool obligatoria
    FakeBoto({"weird": True}),                                    # respuesta con forma inesperada
    FakeBoto(error=ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")),
])
def test_bedrock_failures_become_llm_error(fake):
    with pytest.raises(LLMError):
        BedrockClient(model_id="m", client=fake).generate_structured("sys", MESSAGES, TOOL)


def test_max_tokens_stop_is_marked_as_truncated():
    cut = reply({"text": "Genial, Ana. ¿Tienes carné de"}) | {"stopReason": "max_tokens"}
    assert was_truncated(BedrockClient(model_id="m", client=FakeBoto(cut)).converse("sys", MESSAGES))
    assert not was_truncated(BedrockClient(model_id="m", client=FakeBoto(reply({"text": "ok"}))).converse("sys", MESSAGES))
