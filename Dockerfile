# Glama and registry introspection image. Real Multisim automation remains
# Windows-only and requires a locally licensed Multisim installation.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY mcp_server/pyproject.toml mcp_server/LICENSE mcp_server/README.md mcp_server/MANIFEST.in ./mcp_server/
COPY mcp_server/multisim_mcp ./mcp_server/multisim_mcp

RUN python -m pip install --no-cache-dir ./mcp_server \
    && useradd --create-home --uid 10001 mcp

USER mcp

CMD ["multisim-mcp"]
