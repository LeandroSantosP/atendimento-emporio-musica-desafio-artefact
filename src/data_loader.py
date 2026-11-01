"""Carga e normalização dos CSVs da loja.

Nenhum arquivo em ``data/`` é alterado em tempo de execução. As colunas
derivadas (promoção, preço efetivo, disponibilidade e rótulos) são calculadas
aqui e reaproveitadas pelas ferramentas do agente.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Os nomes atuais são ASCII e sem espaços para funcionar bem em shell, Windows,
# ZIP e diferentes checkouts. Os nomes originais continuam aceitos como fallback.
CSV_FILES = {
    "products": "products.csv",
    "categories": "categories.csv",
    "customers": "customers.csv",
    "orders": "orders.csv",
    "order_items": "order_items.csv",
    "promotions": "promotions.csv",
}

LEGACY_CSV_FILES = {
    name: f"desafio_tecnico_ai_eng - {name}.csv" for name in CSV_FILES
}

STATUS_LABELS = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

PAYMENT_LABELS = {
    "pix": "PIX",
    "debit": "Cartão de débito",
    "boleto": "Boleto bancário",
    "credit_3x": "Cartão de crédito em 3x",
    "credit_6x": "Cartão de crédito em 6x",
    "credit_12x": "Cartão de crédito em 12x",
}

NUMERIC_COLUMNS = {
    "products": ["product_id", "price_brl", "category_id", "stock_quantity"],
    "categories": ["category_id"],
    "customers": ["customer_id"],
    "orders": ["order_id", "customer_id", "total_brl"],
    "order_items": ["order_id", "quantity", "product_id"],
    "promotions": ["promotion_id", "product_id", "discount_percent", "is_active"],
}


def _read_csv(data_dir: Path, name: str) -> pd.DataFrame:
    path = data_dir / CSV_FILES[name]
    if not path.exists():
        path = data_dir / LEGACY_CSV_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de dados não encontrado: {path}")
    return pd.read_csv(path, encoding="utf-8-sig")


def available_products(products: pd.DataFrame) -> pd.DataFrame:
    """Retorna apenas produtos que podem ser vendidos agora."""
    return products[
        (products["status"] == "active") & (products["stock_quantity"] > 0)
    ]


def get_reference_date() -> pd.Timestamp:
    """Data usada para calcular devolução e garantia.

    O dataset não traz a data real de recebimento. Usamos a data estimada de
    entrega como referência e permitimos fixá-la via ``REFERENCE_DATE`` para
    que a demonstração seja reproduzível.
    """
    raw = os.getenv("REFERENCE_DATE")
    if raw:
        try:
            return pd.Timestamp(raw).normalize()
        except (TypeError, ValueError):
            pass
    return pd.Timestamp(date.today())


def _attach_active_promotions(
    products: pd.DataFrame, promotions: pd.DataFrame
) -> pd.DataFrame:
    """Anexa a melhor promoção ativa de cada produto, se existir."""
    products = products.copy()
    active = promotions[promotions["is_active"] == 1].copy()
    if not active.empty:
        # Defensivo: se um produto tiver duas promoções ativas, mantém o maior
        # desconto em vez de duplicar a linha do produto.
        active = active.sort_values("discount_percent", ascending=False)
        active = active.drop_duplicates("product_id", keep="first")

    merged = products.merge(
        active[["product_id", "discount_percent", "description"]].rename(
            columns={
                "discount_percent": "desconto_percent",
                "description": "promocao_descricao",
            }
        ),
        on="product_id",
        how="left",
    )
    merged["desconto_percent"] = merged["desconto_percent"].fillna(0).astype(int)
    merged["preco_original"] = merged["price_brl"]
    merged["preco_promocional"] = (
        merged["price_brl"] * (1 - merged["desconto_percent"] / 100)
    ).round(2)
    merged["preco_promocional"] = merged["preco_promocional"].where(
        merged["desconto_percent"] > 0, other=None
    )
    merged["preco_efetivo"] = merged["preco_promocional"].fillna(
        merged["preco_original"]
    )
    merged["tem_promocao"] = merged["desconto_percent"] > 0
    return merged


def _availability(row: pd.Series) -> str:
    if row["status"] == "discontinued":
        return "descontinuado"
    if row["status"] == "coming_soon":
        return "em_breve"
    if row["stock_quantity"] > 0:
        return "disponivel"
    return "sem_estoque"


def _parse_specs(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_numeric(data: dict[str, pd.DataFrame]) -> None:
    for name, columns in NUMERIC_COLUMNS.items():
        for column in columns:
            data[name][column] = pd.to_numeric(data[name][column], errors="raise")


def _normalize_orders(orders: pd.DataFrame) -> None:
    """Converte datas e adiciona rótulos legíveis para status e pagamento."""
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    orders["estimated_delivery"] = pd.to_datetime(
        orders["estimated_delivery"], errors="coerce"
    )
    orders["status_label"] = orders["status"].map(STATUS_LABELS).fillna(orders["status"])
    orders["payment_label"] = (
        orders["payment_method"].map(PAYMENT_LABELS).fillna(orders["payment_method"])
    )


def load_data(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    """Carrega e normaliza todos os dados estruturados da loja."""
    data: dict[str, pd.DataFrame] = {
        name: _read_csv(data_dir, name) for name in CSV_FILES
    }

    # ``specs`` vem como JSON em texto; a busca precisa do dicionário.
    data["products"]["specs"] = data["products"]["specs"].map(_parse_specs)

    _coerce_numeric(data)
    _normalize_orders(data["orders"])

    # O total oficial é o registrado em orders.total_brl. A soma dos itens usa
    # o preço atual e serve apenas como referência, não para recalcular o pedido.
    products = _attach_active_promotions(data["products"], data["promotions"])
    products["disponibilidade"] = products.apply(_availability, axis=1)
    data["products"] = products
    return data


def _json_safe(value: Any) -> Any:
    """Converte valores de pandas/numpy em valores serializáveis em JSON.

    ``NaT`` é um objeto de data e, sem este tratamento, viraria a string
    literal ``"NaT"`` em ``to_dict()``.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        if pd.isna(value):
            return None
        if (
            isinstance(value, pd.Timestamp)
            and value.hour == 0
            and value.minute == 0
            and value.second == 0
            and value.microsecond == 0
        ):
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def json_records(
    frame: pd.DataFrame, columns: list[str] | None = None
) -> list[dict[str, Any]]:
    """Converte um DataFrame em registros seguros para JSON (sem NaT/nan)."""
    selected = frame[columns] if columns else frame
    records = selected.to_dict(orient="records")
    return [
        {key: _json_safe(value) for key, value in record.items()} for record in records
    ]
