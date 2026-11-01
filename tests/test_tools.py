from __future__ import annotations

from pathlib import Path

from src.data_loader import json_records, load_data
from src.policies import list_sections, query_policies
from src.tools import StoreTools

DATA_DIR = Path(__file__).parents[1] / "data"


def make_tools() -> StoreTools:
    return StoreTools(load_data(DATA_DIR))


# --------------------------------------------------------------------- data


def test_data_has_no_duplicate_rows_or_orphan_keys() -> None:
    data = load_data(DATA_DIR)
    for name, frame in data.items():
        comparable = frame.drop(columns=["specs"]) if name == "products" else frame
        assert comparable.duplicated().sum() == 0
    products = data["products"]
    assert set(data["products"]["category_id"]) <= set(data["categories"]["category_id"])
    assert set(data["orders"]["customer_id"]) <= set(data["customers"]["customer_id"])
    assert set(data["order_items"]["order_id"]) <= set(data["orders"]["order_id"])
    assert set(data["order_items"]["product_id"]) <= set(products["product_id"])
    assert set(data["promotions"]["product_id"]) <= set(products["product_id"])


def test_availability_is_derived_from_status_and_stock() -> None:
    products = load_data(DATA_DIR)["products"].set_index("product_id")
    assert products.loc[96, "disponibilidade"] == "sem_estoque"
    assert products.loc[113, "disponibilidade"] == "descontinuado"
    assert products.loc[136, "disponibilidade"] == "descontinuado"
    assert products.loc[130, "disponibilidade"] == "em_breve"
    assert products.loc[83, "disponibilidade"] == "disponivel"


def test_json_records_does_not_leak_nat_or_nan() -> None:
    orders = load_data(DATA_DIR)["orders"]
    record = json_records(
        orders[orders["order_id"] == 13],
        ["order_id", "estimated_delivery", "tracking_code", "notes"],
    )[0]
    assert record["estimated_delivery"] is None
    assert record["tracking_code"] is None
    assert record["notes"] is None

    delivered = json_records(
        orders[orders["order_id"] == 1], ["order_date", "estimated_delivery"]
    )[0]
    assert delivered["order_date"] == "2025-10-15"
    assert delivered["estimated_delivery"] == "2025-10-25"


# ------------------------------------------------------------------- policies


def test_policy_sections_include_subsections() -> None:
    text = (DATA_DIR / "politicas.md").read_text(encoding="utf-8")
    payment = query_policies("pagamento", text)
    assert "3.1 Regras de Parcelamento" in payment
    assert "parcela mínima de R$ 80,00" in payment

    frete = query_policies("frete", text)
    assert "5.1 Entregas na Região Metropolitana" in frete
    assert "5.2 Entregas para Outras Cidades" in frete

    troca = query_policies("troca", text)
    assert "4.1 Direito de Arrependimento" in troca
    assert "4.2 Trocas por Defeito" in troca


def test_policy_aliases_and_unknown_fallback() -> None:
    text = (DATA_DIR / "politicas.md").read_text(encoding="utf-8")
    assert "Formas de Pagamento" in query_policies("pix", text)
    assert "Política de Trocas" in query_policies("devolver", text)
    assert "Horário de Funcionamento" in query_policies("horario", text)
    assert "Dados da Empresa" in query_policies("endereço", text)
    assert "Disposições Finais" in query_policies("assunto inexistente", text)


def test_policy_text_is_clean_and_has_all_top_level_sections() -> None:
    text = (DATA_DIR / "politicas.md").read_text(encoding="utf-8")
    assert "\u200b" not in text
    assert "\x0c" not in text
    assert "empóriodamusica" not in text
    sections = list_sections(text)
    assert [section.number for section in sections] == list(range(1, 11))


# ---------------------------------------------------------------------- tools


def test_search_handles_plural_and_accents() -> None:
    tools = make_tools()
    plural = tools.buscar_produtos("violões", limite=50)
    singular = tools.buscar_produtos("violao", limite=50)
    assert plural["total"] > 0
    assert plural["total"] == singular["total"]
    assert any(product["product_id"] == 83 for product in plural["produtos"])


def test_search_hides_unavailable_products_from_broad_listing() -> None:
    tools = make_tools()
    result = tools.buscar_produtos("violao", preco_max=1000, limite=50)
    ids = {product["product_id"] for product in result["produtos"]}
    assert 96 not in ids  # active, zero stock
    assert 113 not in ids  # discontinued
    assert all(product["disponibilidade"] == "disponivel" for product in result["produtos"])


def test_specific_out_of_stock_product_is_explained() -> None:
    result = make_tools().buscar_produtos("Giannini GF-3D")
    assert result["total"] == 1
    product = result["produtos"][0]
    assert product["product_id"] == 96
    assert product["disponibilidade"] == "sem_estoque"
    assert "aviso" in result


def test_product_search_includes_active_promotion_price() -> None:
    tools = make_tools()
    product = tools.buscar_produtos("Taylor 110e")["produtos"][0]
    assert product["desconto_percent"] == 8
    assert product["preco_original"] == 5999.0
    assert product["preco_promocional"] == 5519.08
    assert product["preco_efetivo"] == 5519.08


def test_promotions_only_active_and_no_expired_offer() -> None:
    tools = make_tools()
    promotions = tools.consultar_promocoes(limite=50)
    assert promotions["total"] == 4
    assert all(promotion["discount_percent"] > 0 for promotion in promotions["promocoes"])

    takamine = tools.consultar_promocoes("Takamine GD20")
    assert takamine["promocoes"] == []
    assert "promoção ativa" in takamine["mensagem"]


def test_order_requires_email_or_phone_not_name() -> None:
    tools = make_tools()
    assert "erro" in tools.consultar_pedido(3, "Isabela Moreira Cunha")
    assert tools.consultar_pedido(3, "isabela.moreira@jmail.com")["pedido"]["order_id"] == 3
    assert tools.consultar_pedido(3, "67996789012")["pedido"]["order_id"] == 3
    assert tools.consultar_pedido(3, "(67) 99678-9012")["pedido"]["order_id"] == 3
    assert tools.consultar_pedido(3, "+55 67 99678-9012")["pedido"]["order_id"] == 3


def test_order_errors_do_not_allow_enumeration() -> None:
    tools = make_tools()
    missing = tools.consultar_pedido(999, "alguem@example.com")
    wrong = tools.consultar_pedido(3, "alguem@example.com")
    wrong_name = tools.consultar_pedido(3, "Isabela Moreira Cunha")
    assert missing == wrong == wrong_name


def test_shared_email_requires_phone_for_order_access() -> None:
    tools = make_tools()
    # Customers 11 and 43 share bruno.martins@jmail.com; order 10 belongs to 11.
    assert "erro" in tools.consultar_pedido(10, "bruno.martins@jmail.com")
    assert tools.consultar_pedido(10, "67985432109")["pedido"]["order_id"] == 10


def test_order_returns_items_and_does_not_expose_contact_data() -> None:
    tools = make_tools()
    order = tools.consultar_pedido(3, "isabela.moreira@jmail.com")["pedido"]
    assert order["itens"]
    assert order["itens"][0]["quantidade"] == 2
    assert order["total_oficial"] is True
    assert order["total_brl"] == 3450.0
    serialized = str(order)
    assert "isabela.moreira@jmail.com" not in serialized
    assert "99678-9012" not in serialized


def test_order_eligibility_uses_reference_date(monkeypatch) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "2026-02-18")
    tools = make_tools()
    recent = tools.consultar_pedido(7, "leticia.rocha@jmail.com")["pedido"]["prazos"]
    old = tools.consultar_pedido(6, "gabriel.araujo@jmail.com")["pedido"]["prazos"]
    assert recent["arrependimento_7_dias"] is True
    assert recent["troca_por_defeito_30_dias"] is True
    assert old["arrependimento_7_dias"] is False
    assert old["troca_por_defeito_30_dias"] is True


def test_pix_discount_does_not_stack_with_promotion() -> None:
    tools = make_tools()
    normal = tools.consultar_condicoes(1000)["pagamento"]
    promoted = tools.consultar_condicoes(1000, tem_promocao=True)["pagamento"]
    assert normal["pix_desconto_percent"] == 5
    assert normal["pix_valor"] == 950.0
    assert promoted["pix_desconto_percent"] == 0
    assert promoted["pix_valor"] == 1000.0


def test_installments_respect_section_3_1() -> None:
    tools = make_tools()
    options = tools.consultar_condicoes(600)["pagamento"]["parcelas"]
    numbers = {option["parcelas"] for option in options}
    assert 3 in numbers
    # 600 / 6 = 100 >= 80, so 6x is allowed.
    assert 6 in numbers
    # 600 / 7 = 85.71 < 100, so 7x is not allowed.
    assert 7 not in numbers


def test_freight_rules() -> None:
    tools = make_tools()
    free = tools.consultar_condicoes(600, cidade="Campo Grande")["frete"]
    paid = tools.consultar_condicoes(400, cidade="Campo Grande")["frete"]
    other = tools.consultar_condicoes(600, cidade="Dourados")["frete"]
    assert free["frete_gratis"] is True and free["valor_frete"] is None
    assert paid["frete_gratis"] is False and paid["valor_frete"] == 35.0
    assert other["frete_gratis"] is None and other["valor_frete"] is None
    assert "CEP" in other["mensagem"]
