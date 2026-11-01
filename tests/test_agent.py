from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.agent import TOOL_DEFINITIONS, Agent
from src.data_loader import load_data
from src.tools import StoreTools
from src.cli import ASCII_LOGO

ROOT = Path(__file__).parents[1]
DATA_DIR = ROOT / "data"


class FakeToolCall:
    def __init__(self, name: str, arguments: dict | str, call_id: str = "call_1") -> None:
        self.id = call_id
        self.function = SimpleNamespace(
            name=name,
            arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
        )


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = True) -> dict:
        data = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in self.tool_calls
            ]
        if exclude_none:
            data = {key: value for key, value in data.items() if value is not None}
        return data


class FakeCompletions:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(choices=[SimpleNamespace(message=response)])


class FakeClient:
    def __init__(self, responses: list) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def make_agent(responses: list, max_iterations: int = 3) -> Agent:
    agent = Agent.__new__(Agent)
    agent.tools = StoreTools(load_data(DATA_DIR))
    agent.client = FakeClient(responses)
    agent.model = "fake-model"
    agent.provider = "fake-provider"
    agent.max_iterations = max_iterations
    agent.system_prompt = "prompt de teste"
    return agent


def tool_messages(kwargs: dict) -> list[dict]:
    return [message for message in kwargs["messages"] if message["role"] == "tool"]


def test_agent_executes_product_tool_and_returns_final_answer() -> None:
    agent = make_agent(
        [
            FakeMessage(tool_calls=[FakeToolCall("buscar_produtos", {"termo": "Yamaha C40"})]),
            FakeMessage(content="O Yamaha C40 custa R$ 599,90."),
        ]
    )

    answer = agent.respond([], "Quanto custa o Yamaha C40?")

    assert answer == "O Yamaha C40 custa R$ 599,90."
    calls = agent.client.chat.completions.calls
    assert len(calls) == 2
    assert calls[0]["tools"] == TOOL_DEFINITIONS
    assert calls[0]["extra_body"]["provider"]["only"] == ["fake-provider"]

    result = json.loads(tool_messages(calls[1])[0]["content"])
    assert result["produtos"][0]["product_id"] == 81
    assert result["produtos"][0]["disponibilidade"] == "disponivel"


def test_agent_queries_policies_for_store_information() -> None:
    agent = make_agent(
        [
            FakeMessage(
                tool_calls=[FakeToolCall("consultar_politicas", {"topico": "horario"})]
            ),
            FakeMessage(content="Funcionamos de segunda a sexta, das 09:00 às 18:00."),
        ]
    )

    answer = agent.respond([], "Qual o horário de funcionamento?")

    assert "segunda a sexta" in answer.lower()
    result = json.loads(tool_messages(agent.client.chat.completions.calls[1])[0]["content"])
    assert "09:00 às 18:00" in result["politica"]


def test_unknown_tool_is_returned_to_model_without_crashing() -> None:
    agent = make_agent(
        [
            FakeMessage(tool_calls=[FakeToolCall("ferramenta_inexistente", {})]),
            FakeMessage(content="Não consegui consultar."),
        ]
    )

    answer = agent.respond([], "Consulta qualquer")

    assert answer == "Não consegui consultar."
    result = json.loads(tool_messages(agent.client.chat.completions.calls[1])[0]["content"])
    assert "desconhecida" in result["erro"]


def test_invalid_tool_arguments_are_returned_to_model_without_crashing() -> None:
    agent = make_agent(
        [
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        "consultar_pedido",
                        {"id_pedido": 0, "identificador_cliente": ""},
                    )
                ]
            ),
            FakeMessage(content="Preciso do número do pedido e do e-mail ou telefone."),
        ]
    )

    answer = agent.respond([], "Meu pedido")

    assert "Preciso do número" in answer
    result = json.loads(tool_messages(agent.client.chat.completions.calls[1])[0]["content"])
    assert "Parâmetros inválidos" in result["erro"]


def test_provider_failure_returns_friendly_message() -> None:
    agent = make_agent([RuntimeError("provider offline")])

    answer = agent.respond([], "Olá")

    assert "indisponível agora" in answer


def test_agent_stops_after_max_iterations() -> None:
    tool_call = FakeToolCall("buscar_produtos", {"termo": "violao"})
    agent = make_agent(
        [FakeMessage(tool_calls=[tool_call]), FakeMessage(tool_calls=[tool_call])],
        max_iterations=2,
    )

    answer = agent.respond([], "Busque violões")

    assert "não consegui concluir" in answer
    assert len(agent.client.chat.completions.calls) == 2


def test_system_prompt_contains_persona_scope_and_policy_safety_rules() -> None:
    agent = make_agent([])

    prompt = agent._build_system_prompt()

    assert "Empório da Música" in prompt
    assert "MANUAL DE POLÍTICAS" in prompt
    assert "e-mail ou telefone do titular" in prompt
    assert "não comercializamos acessórios" in prompt
    assert "Recuse educadamente assuntos fora" in prompt


def test_tool_definitions_cover_required_capabilities() -> None:
    names = {definition["function"]["name"] for definition in TOOL_DEFINITIONS}
    assert names == {
        "buscar_produtos",
        "consultar_pedido",
        "consultar_promocoes",
        "consultar_condicoes",
        "consultar_politicas",
    }

    order_definition = next(
        definition
        for definition in TOOL_DEFINITIONS
        if definition["function"]["name"] == "consultar_pedido"
    )
    assert set(order_definition["function"]["parameters"]["required"]) == {
        "id_pedido",
        "identificador_cliente",
    }


def test_examples_deliverable_has_three_to_five_conversations() -> None:
    examples = sorted((ROOT / "examples").glob("*.md"))
    assert 3 <= len(examples) <= 5
    contents = [path.read_text(encoding="utf-8").lower() for path in examples]
    assert any("devolução" in content and "pedido" in content for content in contents)


def test_cli_has_ascii_brand_banner() -> None:
    assert "EMPORIO DA MUSICA" in ASCII_LOGO
    assert "atendimento inteligente" in ASCII_LOGO
