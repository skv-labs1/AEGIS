# Aegis — developer entry points.
#
# Two deployables: the demo enterprise systems (mcp-servers/) and the Aegis
# gateway (backend/). They have separate virtualenvs because they are separate
# systems; in production the enterprise side would be a vendor's software.

.DEFAULT_GOAL := help
.PHONY: help install seed demo-systems gateway stack stop test test-demo test-gateway \
        lint fmt clean pending audit investigations inspector investigate check-providers \
        approve reject demo console console-dev build-console

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install everything: both Python venvs and the console
	cd mcp-servers && uv venv --python 3.11 .venv && uv pip install -e ".[dev]"
	cd backend && uv venv --python 3.11 .venv && uv pip install -e ".[dev]" \
		&& uv pip install -e ../mcp-servers
	cd frontend && npm install && npm run build

build-console:  ## Rebuild the console after a frontend change
	cd frontend && npm run build

console:  ## Run the console and gateway in one process (port 8000)
	cd backend && .venv/bin/python -m uvicorn aegis.api.app:app --port 8000

console-dev:  ## Frontend dev server with hot reload (needs `make demo` running)
	cd frontend && npm run dev

seed:  ## Rebuild enterprise_demo.db from the JSON seed files
	cd mcp-servers && .venv/bin/python -m aegis_demo.common.seed

demo-systems:  ## Run the ITSM, ITAM and endpoint MCP servers (ports 8801-8803)
	cd mcp-servers && .venv/bin/python -m aegis_demo.run_all --reseed

gateway:  ## Run the Aegis gateway (port 8800). Needs demo-systems running.
	cd backend && .venv/bin/python -m aegis.gateway.server --port 8800

demo:  ## Start everything in the background: demo systems + console + gateway
	@cd mcp-servers && (.venv/bin/python -m aegis_demo.run_all --reseed \
		> /tmp/aegis-demo.log 2>&1 &)
	@sleep 6
	@cd backend && (.venv/bin/python -m uvicorn aegis.api.app:app --port 8000 \
		> /tmp/aegis-console.log 2>&1 &)
	@sleep 7
	@echo ""
	@echo "  Console         http://127.0.0.1:8000"
	@echo "  MCP gateway     http://127.0.0.1:8000/mcp   (.mcp.json points here)"
	@echo "  Approvals       in the console, or: make pending && make approve ID=1 WHO='You'"
	@echo "  Logs            /tmp/aegis-console.log  /tmp/aegis-demo.log"
	@echo "  Stop            make stop"
	@echo ""

stack:  ## Demo systems + a standalone gateway on 8800, without the console
	@cd mcp-servers && (.venv/bin/python -m aegis_demo.run_all --reseed \
		> /tmp/aegis-demo.log 2>&1 &)
	@sleep 6
	@cd backend && (.venv/bin/python -m aegis.gateway.server --port 8800 \
		> /tmp/aegis-gateway.log 2>&1 &)
	@sleep 5
	@echo "  Aegis gateway   http://127.0.0.1:8800/mcp"

stop:  ## Stop everything started by `make demo` or `make stack`
	@pkill -f "uvicorn aegis" 2>/dev/null || true
	@pkill -f "aegis.gateway.server" 2>/dev/null || true
	@pkill -f "aegis_demo" 2>/dev/null || true
	@echo "stopped"

pending:  ## List proposals waiting for a human decision
	@cd backend && .venv/bin/python -m aegis.cli pending

approve:  ## Approve a proposal: make approve ID=1 WHO='Your Name' [ROLE=it_admin]
	@cd backend && .venv/bin/python -m aegis.cli approve $(ID) \
		--approver "$(WHO)" --role "$(or $(ROLE),it_admin)" --note "$(NOTE)"

reject:  ## Reject a proposal: make reject ID=1 WHO='Your Name' NOTE='why'
	@cd backend && .venv/bin/python -m aegis.cli reject $(ID) \
		--approver "$(WHO)" --role "$(or $(ROLE),it_admin)" --note "$(NOTE)"

investigations:  ## List recent investigations
	@cd backend && .venv/bin/python -m aegis.cli investigations

audit:  ## Print the audit trail
	@cd backend && .venv/bin/python -m aegis.cli audit

investigate:  ## Run an investigation with the built-in engine: make investigate INC=INC-1042
	@cd backend && .venv/bin/python -m aegis.engine.run $(or $(INC),INC-1042) $(if $(PROVIDER),--provider $(PROVIDER),)

check-providers:  ## Verify the LLM providers against their live APIs (needs keys)
	@cd backend && .venv/bin/python -m aegis.engine.check $(if $(PROVIDER),--provider $(PROVIDER),)

test: test-demo test-gateway  ## Run every test

test-demo:  ## Test the demo enterprise systems
	cd mcp-servers && .venv/bin/python -m pytest -q

test-gateway:  ## Test the gateway (starts the demo systems automatically)
	cd backend && .venv/bin/python -m pytest -q

lint:  ## Check lint and formatting
	cd mcp-servers && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt:  ## Apply formatting and safe fixes
	cd mcp-servers && .venv/bin/ruff format . && .venv/bin/ruff check --fix .
	cd backend && .venv/bin/ruff format . && .venv/bin/ruff check --fix .

inspector:  ## Open MCP Inspector (requires Node). Start `make stack` first.
	npx @modelcontextprotocol/inspector

clean:  ## Remove generated databases and caches
	rm -f mcp-servers/aegis_demo/enterprise_demo.db* backend/aegis.db*
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
