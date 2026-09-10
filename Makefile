# Aegis — developer entry points.
# Phase 1 covers the demo enterprise systems. Later phases add the gateway,
# engine and console targets.

PY := mcp-servers/.venv/bin/python
PIP := uv pip

.DEFAULT_GOAL := help
.PHONY: help install seed demo-systems test lint fmt clean inspector

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtualenv and install the demo systems
	cd mcp-servers && uv venv --python 3.11 .venv && $(PIP) install -e ".[dev]"

seed:  ## Rebuild enterprise_demo.db from the JSON seed files
	cd mcp-servers && .venv/bin/python -m aegis_demo.common.seed

demo-systems:  ## Run the ITSM, ITAM and endpoint MCP servers (ports 8801-8803)
	cd mcp-servers && .venv/bin/python -m aegis_demo.run_all --reseed

test:  ## Run the test suite
	cd mcp-servers && .venv/bin/python -m pytest -q

lint:  ## Check formatting and lint rules
	cd mcp-servers && .venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt:  ## Apply formatting
	cd mcp-servers && .venv/bin/ruff format . && .venv/bin/ruff check --fix .

inspector:  ## Open MCP Inspector against the endpoint server (requires Node)
	@echo "Start the servers first: make demo-systems"
	npx @modelcontextprotocol/inspector

clean:  ## Remove the generated database and caches
	rm -f mcp-servers/aegis_demo/enterprise_demo.db*
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
