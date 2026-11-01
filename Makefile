# Atalhos de desenvolvimento do agente Empório da Música.
# Uso: make <alvo>

PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest

.DEFAULT_GOAL := help

.PHONY: help install test test-verbose policies run check clean

help: ## Mostra os comandos disponíveis
	@echo "Comandos disponíveis:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-14s %s\n", $$1, $$2}'

install: ## Instala as dependências do projeto
	$(PYTHON) -m pip install -r requirements.txt

test: ## Executa a suíte de testes
	$(PYTEST) -q

test-verbose: ## Executa os testes com detalhes
	$(PYTEST) -v

policies: ## Regenera o cache data/politicas.md a partir do PDF
	$(PYTHON) -m src.policies > /dev/null
	@echo "Manual atualizado em data/politicas.md"

run: ## Inicia o agente no terminal
	$(PYTHON) -m src.cli

check: ## Valida sintaxe e executa os testes
	$(PYTHON) -m compileall -q src tests
	$(PYTEST) -q

clean: ## Remove caches locais
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	rm -rf .pytest_cache
	@echo "Caches removidos."
