# Agentic Resell Broker — dev shortcuts
# Usage: `make <target>`.  Run `make help` to list.

.DEFAULT_GOAL := help
SHELL := /bin/bash
PYTHON ?= python3

.PHONY: help setup setup-node setup-py env wallets \
        dev-payments dev-commerce dev-shopping dev-buy \
        compose-up compose-down typecheck check-wallets demo clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: setup-node setup-py ## Install all deps (Node + Python)

setup-node: ## Install TS workspace deps (pnpm)
	pnpm install

setup-py: ## Install Python agent deps into a venv
	cd agents && $(PYTHON) -m venv .venv && ./.venv/bin/pip install -U pip && ./.venv/bin/pip install -e .

env: ## Create .env from template if missing
	@test -f .env || (cp .env.example .env && echo "Created .env — fill it in.")

wallets: ## Copy your existing solana keypairs into ./wallets (edit paths first)
	@mkdir -p wallets && touch wallets/.gitkeep
	@echo "Place merchant.json and buyer.json under ./wallets/ (solana-keygen id.json format)."

check-wallets: ## Print SOL + USDC balances for both wallets
	pnpm --filter @arb/payments check-wallets

dev-payments: ## Run the payments service (TS)
	pnpm --filter @arb/payments dev

dev-commerce: ## Run the commerce service (TS)
	pnpm --filter @arb/commerce dev

dev-shopping: ## Run the shopping (broker) agent (Python)
	cd agents && ./.venv/bin/python -m agentic_broker.shopping.server

dev-buy: ## Run a one-shot buyer purchase from the CLI (QUERY="wireless earbuds" BUDGET=30)
	cd agents && ./.venv/bin/python -m agentic_broker.buyer.cli --query "$(QUERY)" --budget "$(BUDGET)"

compose-up: ## Bring the whole stack up in Docker
	docker compose -f infra/docker-compose.yml up --build

compose-down: ## Tear the stack down
	docker compose -f infra/docker-compose.yml down

typecheck: ## Typecheck all TS packages
	pnpm -r typecheck

demo: ## Run the scripted end-to-end demo (assumes stack is up)
	./scripts/demo.sh

clean: ## Remove build artefacts and venvs
	rm -rf node_modules services/*/node_modules packages/*/node_modules agents/.venv
