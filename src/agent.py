"""Orquestração do agente: prompt, function calling e dispatcher.

O fluxo é intencionalmente simples: o LLM recebe a conversa e os schemas das
ferramentas; o código valida e executa a ferramenta escolhida; o resultado volta
para o modelo, que formula a resposta final.
"""

from __future__ import annotations

import json
import os
import unicodedata
from typing import Any

from openai import OpenAI, RateLimitError
from pydantic import ValidationError

from .policies import policy_prompt
from .prompts import POLICY_OVERRIDES, SYSTEM_PROMPT
from .tools import StoreTools


# Schemas enviados ao modelo. Eles descrevem quando usar cada ferramenta e
# quais argumentos são aceitos; a validação real continua sendo feita no código.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_produtos",
            "description": (
                "Busca produtos no catálogo por termo (nome, descrição, categoria "
                "ou especificação), categoria e preço máximo. Retorna disponibilidade, "
                "estoque e promoção ativa. Use antes de informar preço ou estoque."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "termo": {
                        "type": "string",
                        "description": "Termo livre, ex.: 'violões', 'Yamaha C40', 'aço'.",
                    },
                    "categoria": {
                        "type": "string",
                        "description": "Categoria, ex.: 'Violões', 'Ukuleles'.",
                    },
                    "preco_max": {
                        "type": "number",
                        "description": "Preço máximo em reais.",
                    },
                    "incluir_indisponiveis": {
                        "type": "boolean",
                        "description": (
                            "Inclui produtos sem estoque, descontinuados ou em breve. "
                            "Use quando o cliente perguntar por um modelo específico."
                        ),
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Máximo de resultados (1 a 50). Padrão 10.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_pedido",
            "description": (
                "Consulta um pedido após validar a titularidade por e-mail ou "
                "telefone. Retorna status, itens, rastreio e prazos de devolução."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id_pedido": {"type": "integer"},
                    "identificador_cliente": {
                        "type": "string",
                        "description": "E-mail ou telefone do titular. Nome não é aceito.",
                    },
                },
                "required": ["id_pedido", "identificador_cliente"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_promocoes",
            "description": (
                "Lista somente promoções ativas, com preço original, percentual e "
                "preço promocional. Nunca retorna promoções vencidas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "produto": {"type": "string"},
                    "limite": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_condicoes",
            "description": (
                "Calcula PIX, parcelamento e frete. Use para qualquer pergunta de "
                "pagamento ou entrega; não faça essas contas de cabeça."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "valor": {"type": "number", "description": "Valor da compra em reais."},
                    "tem_promocao": {
                        "type": "boolean",
                        "description": "True se o preço já é promocional (PIX não acumula).",
                    },
                    "cidade": {
                        "type": "string",
                        "description": "Cidade de entrega, ex.: 'Campo Grande'.",
                    },
                },
                "required": ["valor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_politicas",
            "description": (
                "Consulta o manual de políticas. Tópicos aceitos incluem horário, "
                "pagamento, parcelamento, troca, devolução, arrependimento, frete, "
                "promoções, atendimento, garantia, privacidade e endereço."
            ),
            "parameters": {
                "type": "object",
                "properties": {"topico": {"type": "string"}},
                "required": ["topico"],
            },
        },
    },
]

TOOL_NAMES = {definition["function"]["name"] for definition in TOOL_DEFINITIONS}


def _ascii_header(value: str) -> str:
    """Converte valores opcionais do OpenRouter para ASCII válido."""
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


class Agent:
    def __init__(self, tools: StoreTools | None = None) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY não configurada. "
                "Copie .env.example para .env e informe a chave."
            )

        headers = {}
        site_url = os.getenv("OPENROUTER_SITE_URL")
        site_name = os.getenv("OPENROUTER_SITE_NAME")
        if site_url:
            headers["HTTP-Referer"] = _ascii_header(site_url)
        if site_name:
            headers["X-Title"] = _ascii_header(site_name)

        self.client = OpenAI(
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=api_key,
            default_headers=headers or None,
        )

        self.tools = tools or StoreTools()
        self.model = (
            os.getenv("LLM_MODEL") or "nvidia/nemotron-3.5-super-vl:free"
        ).strip('"')
        self.provider = (os.getenv("OPENROUTER_PROVIDER") or "nvidia").strip('"')
        self.max_iterations = int(os.getenv("MAX_TOOL_ITERATIONS", "5"))
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Combina persona, regras de negócio e manual completo."""
        try:
            manual = policy_prompt()
        except (FileNotFoundError, RuntimeError):
            manual = ""
        if not manual:
            return SYSTEM_PROMPT
        return f"{SYSTEM_PROMPT}\n\n{POLICY_OVERRIDES}\n\n## MANUAL DE POLÍTICAS\n{manual}"

    def _run_tool(self, name: str, arguments: str) -> dict[str, Any]:
        """Valida e executa uma tool; erros voltam ao modelo, não derrubam o loop."""
        if name not in TOOL_NAMES:
            return {"erro": f"Ferramenta desconhecida: {name}."}

        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"erro": "Os argumentos da ferramenta não são um JSON válido."}
        if not isinstance(parsed, dict):
            return {"erro": "Os argumentos da ferramenta devem ser um objeto JSON."}

        try:
            return getattr(self.tools, name)(**parsed)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors()
            )
            return {"erro": f"Parâmetros inválidos para {name}.", "detalhe": errors}
        except (TypeError, ValueError) as exc:
            return {"erro": f"Não foi possível executar {name}.", "detalhe": str(exc)}
        except Exception:
            return {
                "erro": f"Falha interna ao executar {name}. Tente novamente ou "
                "informe ao cliente que a consulta está indisponível."
            }

    def respond(self, messages: list[dict[str, Any]], user_message: str) -> str:
        """Executa o loop de tool calling até obter a resposta final."""
        conversation = [
            {"role": "system", "content": self.system_prompt},
            *messages,
            {"role": "user", "content": user_message},
        ]

        for _ in range(self.max_iterations):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=conversation,
                    tools=TOOL_DEFINITIONS,
                    extra_body={
                        "provider": {
                            "only": [self.provider],
                            "allow_fallbacks": False,
                        },
                        "reasoning": {"enabled": True},
                    },
                )
            except RateLimitError:
                return (
                    "O limite diário do atendimento foi atingido. "
                    "Aguarde a renovação do limite ou configure outro modelo "
                    "no arquivo .env para continuar."
                )
            except Exception:
                return (
                    "Desculpe, o serviço de atendimento está indisponível agora. "
                    "Tente novamente em alguns instantes."
                )

            message = response.choices[0].message
            conversation.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                return message.content or "Não consegui formular uma resposta."

            # Cada tool call vira uma mensagem "tool" com o resultado em JSON.
            for tool_call in message.tool_calls:
                result = self._run_tool(
                    tool_call.function.name, tool_call.function.arguments
                )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        return (
            "Desculpe, não consegui concluir essa consulta. "
            "Pode reformular o pedido ou tentar novamente?"
        )
