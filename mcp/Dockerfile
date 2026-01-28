FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir httpx

COPY mcp /app/mcp
COPY hummingbot-api-mcp /app/hummingbot-api-mcp

RUN chmod +x /app/hummingbot-api-mcp

ENTRYPOINT ["/app/hummingbot-api-mcp"]
