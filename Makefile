.PHONY: setup run deploy stop install uninstall build install-pre-commit mcp mcp-docker mcp-docker-build mcp-docker-stop

SETUP_SENTINEL := .setup-complete

setup: $(SETUP_SENTINEL)

$(SETUP_SENTINEL):
	chmod +x setup.sh
	./setup.sh

# Run locally (dev mode)
run:
	docker compose up emqx postgres -d
	conda run --no-capture-output -n hummingbot-api uvicorn main:app --reload

# Run MCP stdio adapter
mcp:
	conda run --no-capture-output -n hummingbot-api python -m mcp.mcp_server

MCP_DOCKER_IMAGE ?= hummingbot-api-mcp:local
MCP_ENV_FILE ?= .env
MCP_DOCKER_CONTAINER ?= hummingbot-api-mcp

mcp-docker-build:
	docker build -t $(MCP_DOCKER_IMAGE) -f mcp/Dockerfile .

mcp-docker: mcp-docker-build
	@ENV_ARGS=""; \
	ENV_MOUNT=""; \
	if [ -f "$(MCP_ENV_FILE)" ]; then \
		ENV_MOUNT="-v $$(pwd)/$(MCP_ENV_FILE):/app/.env:ro"; \
	fi; \
	if [ -n "$(MCP_HUMMINGBOT_API_URL)" ]; then \
		ENV_ARGS="$$ENV_ARGS -e MCP_HUMMINGBOT_API_URL=$(MCP_HUMMINGBOT_API_URL)"; \
	elif [ ! -f "$(MCP_ENV_FILE)" ]; then \
		ENV_ARGS="$$ENV_ARGS -e MCP_HUMMINGBOT_API_URL=http://host.docker.internal:8000"; \
	fi; \
	if [ -n "$(MCP_HUMMINGBOT_API_USERNAME)" ]; then \
		ENV_ARGS="$$ENV_ARGS -e MCP_HUMMINGBOT_API_USERNAME=$(MCP_HUMMINGBOT_API_USERNAME)"; \
	fi; \
	if [ -n "$(MCP_HUMMINGBOT_API_PASSWORD)" ]; then \
		ENV_ARGS="$$ENV_ARGS -e MCP_HUMMINGBOT_API_PASSWORD=$(MCP_HUMMINGBOT_API_PASSWORD)"; \
	fi; \
	docker run --rm -d -i --name $(MCP_DOCKER_CONTAINER) $$ENV_MOUNT $$ENV_ARGS $(MCP_DOCKER_IMAGE)

mcp-docker-stop:
	-docker rm -f $(MCP_DOCKER_CONTAINER)

# Deploy with Docker
deploy: $(SETUP_SENTINEL)
	docker compose up -d

# Stop all services
stop:
	docker compose down

# Install conda environment
install:
	@if ! command -v conda >/dev/null 2>&1; then \
		echo "Error: Conda is not found in PATH. Please install Conda or add it to your PATH."; \
		exit 1; \
	fi
	@if conda env list | grep -q '^hummingbot-api '; then \
		echo "Environment already exists."; \
	else \
		conda env create -f environment.yml; \
	fi
	$(MAKE) install-pre-commit
	$(MAKE) setup

uninstall:
	conda env remove -n hummingbot-api -y
	rm -f $(SETUP_SENTINEL)

install-pre-commit:
	conda run -n hummingbot-api pip install pre-commit
	conda run -n hummingbot-api pre-commit install

# Build Docker image
build:
	docker build -t hummingbot/hummingbot-api:latest .
