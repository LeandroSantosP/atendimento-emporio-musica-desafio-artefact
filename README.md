# Empório da Música — Agente de Atendimento

Agente de atendimento via terminal para a **Empório da Música**, loja fictícia de instrumentos musicais em Campo Grande/MS. Responde sobre catálogo, preços, estoque, pedidos, promoções, pagamento, frete, trocas e políticas da loja.

O agente usa **function calling**: o modelo escolhe qual ferramenta consultar, mas preços, prazos, frete e regras de negócio são calculados no código. O modelo não inventa dados.

---

## O que ele faz

- Consulta produtos por nome, categoria, especificação ou faixa de preço.
- Informa disponibilidade, estoque, preço promocional e promoções ativas.
- Consulta pedidos somente após validar e-mail ou telefone do titular.
- Calcula PIX, parcelamento e frete.
- Consulta o manual de políticas da loja.
- Recusa educadamente assuntos fora do escopo.
- Não expõe dados pessoais nem confirma se um pedido existe para quem não é o titular.

---

## Arquitetura

```text
CLI (src/cli.py)
  │
  └── Agent (src/agent.py)
        ├── system prompt + manual de políticas
        ├── histórico da conversa
        ├── loop de function calling
        └── tools (src/tools.py)
              ├── data_loader.py → CSVs
              └── policies.py    → manual em Markdown
```

### Fluxo de uma mensagem

1. O cliente digita a mensagem na CLI.
2. O agente monta o contexto: persona + manual + histórico + nova mensagem.
3. O LLM responde diretamente ou solicita uma ou mais ferramentas.
4. O dispatcher valida a ferramenta e os argumentos, executa e devolve o resultado ao modelo.
5. O ciclo se repete até o modelo produzir a resposta final, respeitando `MAX_TOOL_ITERATIONS`.

Não há framework de orquestração nem banco vetorial: o dataset é pequeno, cabe em memória e o manual completo é injetado no prompt.

---

## Estrutura do projeto

```text
.
├── data/
│   ├── products.csv
│   ├── categories.csv
│   ├── customers.csv
│   ├── orders.csv
│   ├── order_items.csv
│   ├── promotions.csv
│   ├── politicas_da_loja.pdf
│   └── politicas.md            # manual limpo e versionado
├── examples/                   # 5 conversas de exemplo
├── src/
│   ├── agent.py                # loop, dispatcher e schemas das tools
│   ├── tools.py                # consultas e cálculos determinísticos
│   ├── data_loader.py          # carga e normalização dos CSVs
│   ├── policies.py             # extração e consulta do manual
│   ├── prompts.py              # persona e regras do agente
│   ├── history.py              # histórico local em JSON
│   └── cli.py                  # interface de terminal
├── tests/                      # dados, políticas, tools e orquestração
├── assets/
│   ├── desafio - solução.md    # principais problemas e soluções
│   └── desafio_tecnico_ai_eng_artefact.pdf  # enunciado (referência)
├── Makefile
├── requirements.txt
└── README.md
```

Os CSVs foram renomeados para nomes ASCII sem espaços. O loader ainda aceita os nomes originais com o prefixo `desafio_tecnico_ai_eng - ` como fallback.

---

## Como rodar

### Pré-requisitos

- Python 3.11+
- Chave de API do [OpenRouter](https://openrouter.ai/)
- Opcional: `pdftotext` (Poppler) para regenerar o manual; sem ele o projeto usa `pypdf`

### Passo a passo

**1. Clone o projeto e entre na pasta**

```bash
git clone <URL_DO_REPOSITORIO>
cd ai-engineer-challange
```

**2. Crie e ative o ambiente virtual**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

**3. Instale as dependências**

```bash
make install
```

Se preferir sem Make:

```bash
pip install -r requirements.txt
```

**4. Configure as variáveis de ambiente**

```bash
cp .env.example .env
```

Edite o `.env` e informe sua chave:

```env
LLM_MODEL=nvidia/nemotron-3.5-super-vl:free
OPENROUTER_API_KEY=sua-chave-aqui
OPENROUTER_PROVIDER=nvidia
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MAX_TOOL_ITERATIONS=5
REFERENCE_DATE=2026-02-18
```

| Variável | Obrigatória | Uso |
|---|---|---|
| `OPENROUTER_API_KEY` | Sim | Autenticação no OpenRouter |
| `LLM_MODEL` | Não | Modelo usado; precisa suportar tool calling |
| `OPENROUTER_PROVIDER` | Não | Provedor fixado, sem fallback |
| `MAX_TOOL_ITERATIONS` | Não | Limite do loop de ferramentas |
| `REFERENCE_DATE` | Não | Data usada nos prazos de devolução/troca |

`REFERENCE_DATE=2026-02-18` é uma data de demonstração. Em produção, use a data atual ou remova a variável.

**5. Rode os testes**

```bash
make test
```

Ou:

```bash
pytest
```

**6. Inicie o agente**

```bash
make run
```

Ou:

```bash
python -m src.cli
```

Ao iniciar, a CLI exibe um banner ASCII fictício do Empório da Música antes de
abrir o atendimento.

Dentro da CLI:

| Comando | Ação |
|---|---|
| `/novo` | Limpa a conversa atual |
| `/sair` | Encerra o atendimento |

### Outros comandos

| Comando | Ação |
|---|---|
| `make check` | Compila `src/` e `tests/` e roda a suíte |
| `make test-verbose` | Executa os testes com detalhes |
| `make policies` | Regenera `data/politicas.md` a partir do PDF |
| `make clean` | Remove caches locais |

---

## Ferramentas

| Ferramenta | Parâmetros | Uso |
|---|---|---|
| `buscar_produtos` | `termo?`, `categoria?`, `preco_max?`, `incluir_indisponiveis?`, `limite?` | Catálogo, preço efetivo, estoque, promoção e disponibilidade |
| `consultar_pedido` | `id_pedido`, `identificador_cliente` | Status, itens, rastreio e prazos, após validar e-mail ou telefone |
| `consultar_promocoes` | `produto?`, `limite?` | Somente promoções ativas |
| `consultar_condicoes` | `valor`, `tem_promocao?`, `cidade?` | PIX, parcelamento e frete |
| `consultar_politicas` | `topico` | Horário, pagamento, troca, devolução, garantia etc. |

### Comportamento importante

- **Busca:** ignora acentos e maiúsculas, trata plural/singular (`violões` → `violao`) e pesquisa em nome, descrição, categoria e especificações.
- **Disponibilidade:** `disponivel`, `sem_estoque`, `descontinuado` ou `em_breve`. Listagens amplas escondem indisponíveis; busca por modelo específico mostra o produto com um aviso.
- **Promoções:** somente `is_active=1`. Promoção vencida nunca é oferecida.
- **Pedidos:** aceita apenas e-mail ou telefone, comparando os dígitos. Nome não é aceito. Pedido inexistente e dados incorretos retornam a mesma mensagem genérica.
- **Prazos:** calculados a partir da data estimada de entrega e da `REFERENCE_DATE`.
- **Pagamento:** PIX de 5% não incide sobre preço já promocional. Parcelamento segue a seção 3.1 do manual.
- **Frete:** só Campo Grande tem valor fixo. Para outras cidades, o agente pede o CEP e não inventa o valor.

---

## Regras de negócio e dados

| Tema | Tratamento |
|---|---|
| Produto ativo com estoque zero | `disponibilidade=sem_estoque`; não aparece em listagens amplas |
| Produto descontinuado ou em breve | Nunca é tratado como vendável |
| Promoções | Apenas ativas; preço original, percentual e preço promocional são explícitos |
| Total do pedido | `orders.total_brl` é o valor oficial; não é recalculado pela soma dos itens |
| Datas ausentes | `NaT`/`NaN` são convertidos para `null` no JSON |
| E-mail duplicado | Dois clientes compartilham o mesmo e-mail; nesse caso o telefone é exigido |
| Manual em PDF | Extraído uma vez, limpo e versionado em `data/politicas.md` |
| Contato da loja | `(67) 3341-4444` e `contato@emporiodamusica.com.br` |
| Nome dos arquivos | CSVs com nomes ASCII; fallback para os nomes originais |

### Suposições documentadas

- O prazo de devolução/troca usa `estimated_delivery`, pois o dataset não traz a data real de recebimento.
- Pedidos são tratados como compras online para o direito de arrependimento.
- Só Campo Grande é considerada região metropolitana para a regra de frete.
- A seção 3.1 prevalece sobre a tabela resumida da seção 3 no parcelamento.
- Frete grátis em Campo Grande vale para compras **acima** de R$ 500.
- O produto 130 (`em_breve`) aparece em pedidos: inconsistência da base, tratada como não vendável no catálogo.

---

## Decisões técnicas

- **Python + módulos da biblioteca padrão:** o projeto é pequeno e orientado a dados. A separação em módulos (`agent`, `tools`, `data_loader`, `policies` e `history`) mantém cada responsabilidade testável sem introduzir um framework de orquestração.
- **Function calling em vez de RAG:** os dados são tabulares e as respostas exigem precisão. Consultas determinísticas evitam erros de similaridade e permitem filtros numéricos.
- **Manual completo no system prompt:** o documento tem cerca de 3 mil tokens e cabe no contexto. A tool `consultar_politicas` serve para consultas direcionadas e para manter o modelo ancorado.
- **Retrieval determinístico:** CSVs são carregados em memória com `pandas`, e o manual é normalizado em Markdown. A busca textual trata acentos, plural/singular, campos relevantes e filtros numéricos; não há banco vetorial porque o volume e o domínio não justificam sua complexidade.
- **Prompt em camadas:** o system prompt define persona e escopo, `POLICY_OVERRIDES` reforça regras críticas e o manual fornece o conteúdo completo das políticas. A resposta final só é produzida depois das consultas necessárias às tools.
- **Cálculos no código:** PIX, parcelas, frete e prazos são aritmética e regra de negócio; modelos pequenos erram contas longas.
- **LLM e provedor configuráveis:** a integração usa a API compatível da OpenRouter, com modelo, provedor e limite de iterações definidos no `.env`. O provedor fica fixado sem fallback para tornar o comportamento reproduzível; o reasoning é habilitado quando suportado pelo modelo.
- **Validação com Pydantic + whitelist:** ferramentas desconhecidas e argumentos inválidos voltam como erro ao modelo, sem derrubar a conversa.
- **CLI com `rich`:** as respostas em Markdown são renderizadas no terminal, incluindo tabelas e listas.

---

## Testes

A suíte usa os dados locais e um cliente fake; nenhum teste chama o provedor real.

```bash
make check
```

Cobertura principal:

- integridade dos CSVs, sem duplicatas ou chaves órfãs;
- disponibilidade derivada de status e estoque;
- JSON sem `NaT`/`nan`;
- seções, subseções, aliases e fallback do manual;
- busca com acento, plural, categoria, especificações e código de modelo;
- promoções ativas e preço efetivo;
- titularidade por e-mail/telefone, e-mail duplicado e mensagem genérica;
- itens do pedido e prazos de 7/30/90 dias;
- PIX, parcelamento e frete;
- orquestração do agente: execução de tool, argumentos inválidos, falha do provedor e limite de iterações.

---

## Exemplos de interação

Conversas completas em [`examples/`](./examples):

| Arquivo | Cenário |
|---|---|
| `01_catalogo.md` | Violões disponíveis até R$ 1.000 |
| `02_info_loja.md` | Endereço e horário da loja |
| `03_preco_promocao.md` | Preço, promoção e cálculo de PIX/parcelas |
| `04_devolucao.md` | Pedido + política + prazo de arrependimento |
| `05_fora_escopo.md` | Recusa de acessórios e redirecionamento |

---

## Limitações conhecidas

- A busca é textual, não semântica; perguntas vagas dependem da descrição e das especificações dos produtos.
- Não há autenticação real; a consulta de pedido valida um identificador informado na conversa. Em produção seria necessário OTP, login ou WhatsApp verificado.
- O histórico é um JSON local, sem suporte a concorrência.
- O modelo gratuito pode variar em qualidade e disponibilidade.
- O uso de modelos gratuitos pode atingir limites diários do provedor; a CLI informa esse caso e orienta aguardar a renovação ou configurar outro modelo.
- O prazo legal usa a data estimada de entrega, não a data real de recebimento.
- Para cidades fora de Campo Grande, o frete depende do CEP e não tem valor calculável com os dados disponíveis.

### O que eu faria com mais tempo

- Adicionaria observabilidade com logs estruturados, métricas de latência, tokens e erros do provedor, sem registrar dados pessoais.
- Criaria testes de contrato com pelo menos um modelo real e avaliações para medir precisão, recusa fora de escopo e uso correto das ferramentas.
- Substituiria o histórico JSON por armazenamento concorrente com controle de sessão e adicionaria autenticação real para consultas de pedidos.
- Evoluiria o retrieval do manual para busca híbrida somente quando o volume de documentos justificasse essa complexidade.

---

## Documentação adicional

- [`desafio - solução.md`](./assets/desafio%20-%20solução.md) — principais problemas e soluções do projeto.
- [`desafio_tecnico_ai_eng_artefact.pdf`](./assets/desafio_tecnico_ai_eng_artefact.pdf) — enunciado original, mantido apenas como referência.

---

## Uso de assistentes de código

Usei duas ferramentas, com papéis complementares:

- **OpenCode com modelos gratuitos:** utilizado para explorar a proposta, discutir alternativas de arquitetura, revisar a implementação e iterar sobre prompts, tools e testes. Os modelos gratuitos foram usados como apoio de raciocínio e geração, não como fonte de verdade dos dados.
- **GitHub Copilot no VS Code:** utilizado para implementação incremental, navegação pelo código, ajustes de testes, tratamento de erros e documentação.

Meu workflow foi: primeiro entender o dataset e as políticas; depois pedir sugestões e implementar mudanças pequenas; em seguida conferir cada comportamento contra os CSVs e o manual; por fim executar testes, revisar o diff e organizar os commits por funcionalidade. As decisões finais, a validação dos resultados e a revisão do código foram feitas por mim. Nenhuma credencial foi compartilhada com essas ferramentas, e a chave da OpenRouter permanece apenas no `.env` local.

---

Projeto desenvolvido exclusivamente para fins de avaliação técnica.
