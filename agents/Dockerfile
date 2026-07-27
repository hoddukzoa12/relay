# Python agents (buyer + shopping + MCP). One image; select the module at deploy.
# Build context: ./agents
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
RUN pip install -e .

EXPOSE 8090 8091 8092

# Default = shopping agent. docker-compose overrides `command:` for the buyer.
CMD ["python", "-m", "agentic_broker.shopping.server"]
