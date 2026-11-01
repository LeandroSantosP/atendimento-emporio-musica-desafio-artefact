"""Ferramentas determinísticas usadas pelo agente.

O modelo de linguagem decide *quando* consultar cada ferramenta, mas nunca
calcula preço, prazo ou frete sozinho. Toda regra de negócio fica neste módulo.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from .data_loader import (
    available_products,
    get_reference_date,
    json_records,
    load_data,
)
from .policies import query_policies


# --------------------------------------------------------------------------- #
# Validação dos argumentos recebidos do modelo
# --------------------------------------------------------------------------- #
class ProductSearch(BaseModel):
    termo: str | None = None
    categoria: str | None = None
    preco_max: float | None = Field(default=None, ge=0)
    incluir_indisponiveis: bool = False
    limite: int = Field(default=10, ge=1, le=50)


class OrderQuery(BaseModel):
    id_pedido: int = Field(gt=0)
    identificador_cliente: str = Field(min_length=1)


class PromotionQuery(BaseModel):
    produto: str | None = None
    limite: int = Field(default=10, ge=1, le=50)


class PaymentQuery(BaseModel):
    valor: float = Field(gt=0)
    tem_promocao: bool = False
    cidade: str | None = None


class PolicyQuery(BaseModel):
    topico: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Normalização e busca textual
# --------------------------------------------------------------------------- #
def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().lower()


def _compact(value: Any) -> str:
    return _normalize(value).replace(" ", "")


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value))


def _phone_digits(value: Any) -> str:
    digits = _digits(value)
    if len(digits) in {12, 13} and digits.startswith("55"):
        digits = digits[2:]
    return digits


def _singular(word: str) -> str:
    """Reduz plurais comuns do português para melhorar a busca."""
    if word.endswith("oes"):
        return word[:-3] + "ao"
    if word.endswith("aes"):
        return word[:-3] + "ao"
    if word.endswith("ais"):
        return word[:-3] + "al"
    if word.endswith("eis"):
        return word[:-3] + "el"
    if word.endswith("ois"):
        return word[:-3] + "ol"
    if word.endswith("ns"):
        return word[:-2] + "m"
    if word.endswith(("res", "zes", "ses")):
        return word[:-2]
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _plural(word: str) -> str:
    if word.endswith("ao"):
        return word[:-2] + "oes"
    if word.endswith("m"):
        return word[:-1] + "ns"
    if word.endswith("l"):
        return word[:-1] + "is"
    if word.endswith(("r", "z", "s")):
        return word + "es"
    return word + "s"


_STOPWORDS = {
    "a",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
}


def _token_variants(word: str) -> set[str]:
    singular = _singular(word)
    return {_compact(word), _compact(singular), _compact(_plural(singular))}


def _matches(haystack: str, term: str) -> bool:
    """Compara termos por palavras, ignorando plural e acentos.

    Um termo com várias palavras só casa quando todas as palavras relevantes
    aparecem no texto. Códigos de modelo (C40, GD20, GF-3D) são comparados como
    substrings compactas, pois a pontuação atrapalharia a busca.
    """
    words = [
        word
        for word in _normalize(term).split()
        if len(word) >= 2 and word not in _STOPWORDS
    ]
    if not words:
        return True

    compact_haystack = _compact(haystack)
    for word in words:
        variants = _token_variants(word)
        if any(char.isdigit() for char in word):
            found = any(variant in compact_haystack for variant in variants)
        else:
            found = any(
                re.search(
                    rf"(?<![a-z0-9]){re.escape(variant)}s?(?![a-z0-9])",
                    haystack,
                )
                for variant in variants
            )
        if not found:
            return False
    return True


def _fmt_brl(value: float | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------- #
# Fachada usada pelo dispatcher do agente
# --------------------------------------------------------------------------- #
class StoreTools:
    def __init__(self, data: dict[str, pd.DataFrame] | None = None) -> None:
        self.data = data or load_data()
        self.products = self._build_product_index(self.data)

    @staticmethod
    def _build_product_index(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Junta categoria e cria o texto único usado pela busca."""
        products = data["products"].copy()
        categories = data["categories"][["category_id", "name"]].rename(
            columns={"name": "categoria"}
        )
        products = products.merge(categories, on="category_id", how="left")
        specs_text = products["specs"].map(
            lambda specs: " ".join(map(str, specs.values()))
            if isinstance(specs, dict)
            else ""
        )
        products["_search"] = (
            products["name"].map(_normalize)
            + " "
            + products["description"].map(_normalize)
            + " "
            + products["categoria"].map(_normalize)
            + " "
            + specs_text.map(_normalize)
        )
        return products

    # ------------------------------------------------------------------ busca
    def buscar_produtos(
        self,
        termo: str | None = None,
        categoria: str | None = None,
        preco_max: float | None = None,
        incluir_indisponiveis: bool = False,
        limite: int = 10,
    ) -> dict[str, Any]:
        params = ProductSearch(
            termo=termo,
            categoria=categoria,
            preco_max=preco_max,
            incluir_indisponiveis=incluir_indisponiveis,
            limite=limite,
        )

        mask = pd.Series(True, index=self.products.index)
        if params.termo:
            mask &= self.products["_search"].map(
                lambda text: _matches(text, params.termo)
            )
        if params.categoria:
            mask &= self.products["categoria"].map(
                lambda value: _matches(_normalize(value), params.categoria)
            )
        if params.preco_max is not None:
            mask &= self.products["preco_efetivo"] <= params.preco_max

        matched = self.products[mask]
        sellable = available_products(matched)
        aviso = None

        if params.incluir_indisponiveis:
            result = matched
        elif not params.termo or not sellable.empty or matched.empty:
            result = sellable
        else:
            # Um modelo específico foi pedido, mas não há unidade vendável.
            # Mostramos o produto com o motivo explícito em vez de escondê-lo.
            result = matched
            aviso = (
                "Nenhuma unidade disponível para venda. Os produtos encontrados "
                "estão sem estoque, descontinuados ou em breve; confira o campo "
                "disponibilidade antes de responder."
            )

        total = len(result)
        result = result.sort_values("preco_efetivo").head(params.limite)

        columns = [
            "product_id",
            "name",
            "categoria",
            "price_brl",
            "preco_original",
            "desconto_percent",
            "preco_promocional",
            "preco_efetivo",
            "preco_efetivo_formatado",
            "stock_quantity",
            "disponibilidade",
            "description",
            "specs",
        ]
        result = result.copy()
        result["preco_efetivo_formatado"] = result["preco_efetivo"].map(_fmt_brl)

        payload: dict[str, Any] = {
            "produtos": json_records(result, columns),
            "total": total,
            "exibindo": len(result),
            "limite": params.limite,
        }
        if total > len(result):
            payload["aviso"] = (
                f"Há {total} resultados; exibindo os {len(result)} mais baratos. "
                "Refine a busca se necessário."
            )
        elif aviso:
            payload["aviso"] = aviso
        return payload

    # ----------------------------------------------------------------- pedidos
    def _order_items(self, order_id: int) -> list[dict[str, Any]]:
        items = self.data["order_items"]
        items = items[items["order_id"] == order_id].merge(
            self.products[
                ["product_id", "name", "preco_efetivo", "disponibilidade"]
            ],
            on="product_id",
            how="left",
        )
        return [
            {
                "produto": item["name"],
                "quantidade": int(item["quantity"]),
                "preco_atual_unitario": _fmt_brl(item["preco_efetivo"]),
            }
            for _, item in items.iterrows()
        ]

    @staticmethod
    def _order_deadlines(
        order: pd.Series, reference: pd.Timestamp
    ) -> tuple[int | None, bool, bool, bool]:
        """Calcula os prazos a partir da entrega estimada, não da data real."""
        delivery = order["estimated_delivery"]
        days = (reference - delivery).days if pd.notna(delivery) else None
        delivered = order["status"] == "delivered"

        def within(limit: int) -> bool:
            return delivered and days is not None and 0 <= days <= limit

        return days, within(7), within(30), within(90)

    def consultar_pedido(
        self, id_pedido: int, identificador_cliente: str
    ) -> dict[str, Any]:
        params = OrderQuery(
            id_pedido=id_pedido, identificador_cliente=identificador_cliente
        )
        # A mesma mensagem é usada para pedido inexistente e dados incorretos,
        # evitando que alguém descubra quais números de pedido existem.
        generic_error = {
            "erro": (
                "Não foi possível localizar um pedido com esses dados. "
                "Confira o número do pedido e use o e-mail ou telefone do titular."
            )
        }

        orders = self.data["orders"]
        order = orders[orders["order_id"] == params.id_pedido]
        if order.empty:
            return generic_error

        row = order.iloc[0]
        customers = self.data["customers"]
        customer = customers[customers["customer_id"] == row["customer_id"]]
        if customer.empty:
            return generic_error
        customer = customer.iloc[0]

        identifier = _normalize(params.identificador_cliente)
        valid_email = identifier == _normalize(customer["email"])
        if valid_email:
            # Há dois clientes com o mesmo e-mail na base. Nesse caso o e-mail
            # não prova titularidade; o telefone é obrigatório.
            same_email = customers[customers["email"].map(_normalize) == identifier]
            if len(same_email) > 1:
                valid_email = False

        phone = _phone_digits(identifier)
        valid_phone = len(phone) >= 10 and phone == _phone_digits(customer["phone"])

        # Nome é público e fácil de adivinhar, então nunca é aceito sozinho.
        if not (valid_email or valid_phone):
            return generic_error

        reference = get_reference_date()
        delivery = row["estimated_delivery"]
        days, within_7, within_30, within_90 = self._order_deadlines(row, reference)

        pedido = {
            "order_id": int(row["order_id"]),
            "status": row["status"],
            "status_label": row["status_label"],
            "order_date": row["order_date"],
            "total_brl": float(row["total_brl"]),
            "total_formatado": _fmt_brl(row["total_brl"]),
            "payment_method": row["payment_method"],
            "payment_label": row["payment_label"],
            "tracking_code": row["tracking_code"],
            "estimated_delivery": delivery,
            "notes": row["notes"],
            "itens": self._order_items(params.id_pedido),
            "total_oficial": True,
            "observacao_total": (
                "O total do pedido é o valor efetivamente registrado em orders.total_brl. "
                "Os itens mostram o preço atual de referência e não devem ser somados "
                "para recalcular o valor pago."
            ),
        }
        # json_records converte Timestamp/NaT para valores seguros em JSON.
        safe = json_records(pd.DataFrame([pedido]))[0]
        safe["prazos"] = {
            "referencia": reference.date().isoformat(),
            "dias_desde_entrega_estimada": days,
            "arrependimento_7_dias": within_7,
            "troca_por_defeito_30_dias": within_30,
            "garantia_legal_90_dias": within_90,
            "observacao": (
                "Os prazos usam a data estimada de entrega como referência, "
                "pois o conjunto de dados não traz a data real de recebimento. "
                "A garantia legal de 90 dias é independente da troca em loja de 30 dias."
            ),
        }
        if row["status"] in {"pending", "confirmed"} and pd.isna(row["tracking_code"]):
            safe["mensagem_entrega"] = "Pedido ainda não despachado; não há rastreio."
        return {"pedido": safe}

    # -------------------------------------------------------------- promoções
    def consultar_promocoes(
        self, produto: str | None = None, limite: int = 10
    ) -> dict[str, Any]:
        params = PromotionQuery(produto=produto, limite=limite)
        promotions = self.data["promotions"]
        active = promotions[promotions["is_active"] == 1]
        merged = active.merge(
            self.products[
                [
                    "product_id",
                    "name",
                    "categoria",
                    "price_brl",
                    "preco_promocional",
                    "preco_efetivo",
                    "stock_quantity",
                    "disponibilidade",
                ]
            ],
            on="product_id",
            how="left",
        )
        if params.produto:
            merged = merged[
                merged["name"].map(
                    lambda name: _matches(_normalize(name), params.produto)
                )
            ]

        merged = merged.sort_values("preco_efetivo")
        total = len(merged)
        merged = merged.head(params.limite)
        merged = merged.assign(
            preco_original=merged["price_brl"],
            preco_promocional_formatado=merged["preco_promocional"].map(_fmt_brl),
        )
        payload: dict[str, Any] = {
            "promocoes": json_records(
                merged,
                [
                    "name",
                    "categoria",
                    "preco_original",
                    "discount_percent",
                    "preco_promocional",
                    "preco_promocional_formatado",
                    "disponibilidade",
                    "description",
                ],
            ),
            "total": total,
        }
        if not payload["promocoes"]:
            payload["mensagem"] = (
                "Não há promoção ativa para esse produto. "
                "Promoções vencidas não podem ser oferecidas."
            )
        return payload

    # ------------------------------------------------------ pagamento e frete
    @staticmethod
    def _installments(value: float) -> list[dict[str, Any]]:
        """Aplica a tabela de parcelamento da seção 3.1 do manual."""
        options = []
        for number in range(1, 13):
            if number <= 3:
                allowed = value >= 50
                minimum = 0.0
            elif number <= 6:
                allowed = value / number >= 80
                minimum = 80.0
            else:
                allowed = value / number >= 100
                minimum = 100.0
            if allowed:
                installment = round(value / number, 2)
                options.append(
                    {
                        "parcelas": number,
                        "valor_parcela": installment,
                        "valor_parcela_formatado": _fmt_brl(installment),
                        "minimo_por_parcela": minimum,
                    }
                )
        return options

    @staticmethod
    def _freight(value: float, city: str | None) -> dict[str, Any]:
        """Só Campo Grande tem frete fixo; as demais cidades exigem CEP."""
        normalized_city = _normalize(city or "")
        if "campo grande" in normalized_city:
            free = value > 500
            return {
                "regiao": "Campo Grande (região metropolitana)",
                "frete_gratis": free,
                "valor_frete": None if free else 35.0,
                "valor_frete_formatado": None if free else _fmt_brl(35.0),
                "prazo": "1 a 3 dias úteis",
            }
        return {
            "regiao": city,
            "frete_gratis": None,
            "valor_frete": None,
            "valor_frete_formatado": None,
            "prazo": "PAC: 5 a 12 dias úteis; SEDEX: 2 a 5 dias úteis; Jadlog: 3 a 8 dias úteis.",
            "mensagem": (
                "Para cidades fora da região metropolitana de Campo Grande, "
                "o frete é calculado pelo CEP de destino, peso e dimensões. "
                "Não invente um valor: peça o CEP ou consulte a loja."
            ),
        }

    def consultar_condicoes(
        self,
        valor: float,
        tem_promocao: bool = False,
        cidade: str | None = None,
    ) -> dict[str, Any]:
        params = PaymentQuery(
            valor=valor, tem_promocao=tem_promocao, cidade=cidade
        )
        pix_percent = 0.0 if params.tem_promocao else 5.0
        pix_value = round(params.valor * (1 - pix_percent / 100), 2)

        payment = {
            "valor_informado": params.valor,
            "valor_formatado": _fmt_brl(params.valor),
            "tem_promocao": params.tem_promocao,
            "pix_desconto_percent": pix_percent,
            "pix_valor": pix_value,
            "pix_valor_formatado": _fmt_brl(pix_value),
            "observacao_pix": (
                "O desconto de PIX não se aplica sobre preço já promocional."
                if params.tem_promocao
                else "Desconto de 5% sobre o preço de tabela."
            ),
            "parcelas": self._installments(params.valor),
            "formas": [
                "PIX",
                "Cartão de débito",
                "Cartão de crédito em até 12x sem juros",
                "Boleto bancário (compensação em até 3 dias úteis)",
            ],
        }
        return {
            "pagamento": payment,
            "frete": self._freight(params.valor, params.cidade),
        }

    # --------------------------------------------------------------- políticas
    def consultar_politicas(self, topico: str) -> dict[str, str]:
        params = PolicyQuery(topico=topico)
        return {"politica": query_policies(params.topico)}
