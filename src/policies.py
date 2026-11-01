"""Extração, limpeza e consulta do manual de políticas da loja.

O PDF é lido uma única vez e o resultado limpo fica versionado em
``data/politicas.md``. Em runtime o agente usa esse cache, sem exigir Poppler.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PDF_PATH = DATA_DIR / "politicas_da_loja.pdf"
LEGACY_PDF_PATH = DATA_DIR / "políticas_da_loja.pdf"
TEXT_PATH = DATA_DIR / "politicas.md"

# Termos que o cliente (ou o modelo) pode usar, mapeados para a seção que
# responde à dúvida. O manual continua sendo a fonte da verdade; os aliases
# servem apenas para a recuperação do trecho correto.
TOPIC_ALIASES: dict[str, tuple[int, ...]] = {
    "sobre": (1,),
    "empresa": (1,),
    "missao": (1,),
    "dados da empresa": (1,),
    "cnpj": (1,),
    "endereco": (1,),
    "telefone": (1,),
    "contato": (1,),
    "email": (1,),
    "horario": (2,),
    "funcionamento": (2,),
    "aberto": (2,),
    "funciona": (2,),
    "pagamento": (3,),
    "formas de pagamento": (3,),
    "parcelamento": (3,),
    "parcelas": (3,),
    "pix": (3,),
    "cartao": (3,),
    "credito": (3,),
    "debito": (3,),
    "boleto": (3,),
    "desconto pix": (3,),
    "troca": (4,),
    "trocas": (4,),
    "devolucao": (4,),
    "devolucoes": (4,),
    "devolver": (4,),
    "arrependimento": (4,),
    "arrependi": (4,),
    "reembolso": (4,),
    "frete": (5,),
    "entrega": (5,),
    "entregas": (5,),
    "envio": (5,),
    "rastreamento": (5,),
    "rastreio": (5,),
    "correios": (5,),
    "jadlog": (5,),
    "promocao": (6,),
    "promocoes": (6,),
    "desconto": (6,),
    "descontos": (6,),
    "atendimento": (7,),
    "whatsapp": (7,),
    "fluxo": (7,),
    "situacoes especiais": (7,),
    "garantia": (8,),
    "defeito": (8,),
    "privacidade": (9,),
    "lgpd": (9,),
    "dados pessoais": (9,),
    "disposicoes finais": (10,),
}

_HEADING_RE = re.compile(r"(?m)^[ \t]*(\d+)(?:\.(\d+))?\.?[ \t]+(.+?)[ \t]*$")
_PAGE_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(?:Emp[óo]rio da M[úu]sica Manual de Pol[íi]ticas e Procedimentos|"
    r"P[áa]gina \d+)[ \t]*$"
)


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    body: str


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().lower()


def clean_policy_text(text: str) -> str:
    """Remove ruído da extração sem perder a estrutura do documento."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u200b", "").replace("\x0c", "\n")

    # O PDF traz duas grafias do e-mail; mantemos a válida, sem acento.
    text = text.replace("empóriodamusica", "emporiodamusica")

    # O manual repete um segundo WhatsApp. A capa e a seção 1.2 concordam em
    # (67) 3341-4444, adotado como fonte única.
    text = text.replace("(67) 3321-4500", "(67) 3341-4444")

    # O catálogo tem 65 produtos, então o número de marketing "300" não pode
    # ser verificado pelo agente. Evitamos repetir essa informação.
    text = re.sub(
        r"mais de 300 instrumentos",
        "instrumentos",
        text,
        flags=re.IGNORECASE,
    )

    text = _PAGE_HEADER_RE.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Preserva a indentação das tabelas; remove apenas excesso de linhas vazias.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _extract_with_pdftotext(pdf_path: Path) -> str | None:
    executable = shutil.which("pdftotext")
    if executable is None:
        return None
    result = subprocess.run(
        [executable, "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _extract_with_pypdf(pdf_path: Path) -> str | None:
    """Fallback em Python puro para ambientes sem Poppler instalado."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text(extraction_mode="layout") or "")
        except TypeError:  # versões antigas do pypdf não aceitam "layout"
            pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def _find_pdf(pdf_path: Path = PDF_PATH) -> Path:
    if pdf_path.exists():
        return pdf_path
    if pdf_path == PDF_PATH and LEGACY_PDF_PATH.exists():
        return LEGACY_PDF_PATH
    raise FileNotFoundError(f"PDF de políticas não encontrado: {pdf_path}")


def rebuild_policy_text(
    pdf_path: Path = PDF_PATH, text_path: Path = TEXT_PATH
) -> str:
    """Extrai o PDF e grava o Markdown limpo usado em runtime."""
    source_pdf = _find_pdf(pdf_path)
    raw = _extract_with_pdftotext(source_pdf)
    if not raw:
        raw = _extract_with_pypdf(source_pdf)
    if not raw:
        raise RuntimeError(
            "Não foi possível extrair o manual. Instale 'pdftotext' (Poppler) "
            "ou a dependência Python 'pypdf'."
        )

    text = clean_policy_text(raw)
    text_path.write_text(text, encoding="utf-8")
    return text


def extract_policy_text(
    pdf_path: Path = PDF_PATH, text_path: Path = TEXT_PATH
) -> str:
    """Retorna o cache do manual, extraindo apenas se ele não existir.

    O cache versionado evita reextrair o PDF a cada clone (onde as datas dos
    arquivos são arbitrárias) e permite rodar o agente sem Poppler.
    """
    if text_path.exists():
        return text_path.read_text(encoding="utf-8")
    return rebuild_policy_text(pdf_path, text_path)


def _top_level_sections(source: str) -> list[Section]:
    """Divide o manual em seções de nível 1, mantendo todas as subseções.

    É preciso ignorar a lista numerada dentro da seção 7.2 ("1. Saudação: ..."),
    por isso a checagem de sequência e a rejeição de títulos com dois-pontos.
    """
    candidates: list[tuple[int, re.Match[str]]] = []
    expected = 1
    for match in _HEADING_RE.finditer(source):
        number = int(match.group(1))
        sub = match.group(2)
        title = match.group(3).strip()
        if sub is not None:
            continue
        if number != expected:
            continue
        if ":" in title or len(title) > 100:
            continue
        candidates.append((number, match))
        expected += 1

    sections: list[Section] = []
    for index, (number, match) in enumerate(candidates):
        end = (
            candidates[index + 1][1].start()
            if index + 1 < len(candidates)
            else len(source)
        )
        sections.append(
            Section(
                number=number,
                title=match.group(3).strip(),
                body=source[match.start() : end].strip(),
            )
        )
    return sections


def list_sections(text: str | None = None) -> list[Section]:
    source = text if text is not None else extract_policy_text()
    return _top_level_sections(source)


def query_policies(topic: str, text: str | None = None) -> str:
    """Retorna as seções do manual mais relevantes para um tópico.

    Tópicos desconhecidos retornam o manual completo em vez de "nada
    encontrado", o que antes escondia informações válidas como "pix",
    "devolver" e "horário".
    """
    source = text if text is not None else extract_policy_text()
    normalized_topic = _normalize(topic)
    if not normalized_topic:
        return source

    sections = _top_level_sections(source)
    if not sections:
        return source

    wanted_numbers: set[int] = set(TOPIC_ALIASES.get(normalized_topic, ()))

    # Aceita tópicos no formato "3.1" ou "seção 3".
    number_match = re.search(r"\d+", normalized_topic)
    if number_match:
        wanted_numbers.add(int(number_match.group()))

    # Sem alias nem número, procura o termo no corpo das seções.
    if not wanted_numbers:
        for section in sections:
            if normalized_topic in _normalize(section.body):
                wanted_numbers.add(section.number)

    selected = [section for section in sections if section.number in wanted_numbers]
    if not selected:
        return source
    return "\n\n".join(section.body for section in selected)


def policy_prompt(text: str | None = None) -> str:
    """Manual completo para o system prompt (cerca de 3 mil tokens)."""
    return text if text is not None else extract_policy_text()


if __name__ == "__main__":
    print(rebuild_policy_text())
