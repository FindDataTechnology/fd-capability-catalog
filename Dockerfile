# fd-capability-catalog image — the catalog served as an MCP server on /mcp.
#
# WHY the kernel wheel is copied instead of pip-resolved: `fd-ontology-kernel`
# is not on PyPI (measured 2026-11-23: 404). `vendor/` holds the wheel built
# from that repo's committed HEAD (law-bench's precedent). Installing it first
# and the catalog `--no-deps` is the only sequence that works from an index
# that has neither package.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY vendor ./vendor
COPY pyproject.toml README.md ./
COPY fd_capability_catalog ./fd_capability_catalog

# Kernel first (no-deps: it declares zero deps by design), then the catalog
# with its MCP extra. Installing the catalog by path avoids needing it on PyPI.
RUN pip install --no-deps ./vendor/fd_ontology_kernel-0.1.0-py3-none-any.whl \
 && pip install ".[mcp]"

RUN useradd --create-home --uid 1000 appuser
USER appuser

# The catalog owns its db; mount it here (CAPABILITY_CATALOG_DB).
VOLUME ["/data"]
ENV CAPABILITY_CATALOG_DB=/data/capability.db \
    CAPABILITY_CATALOG_SCOPE=org:finddata

EXPOSE 8320
CMD ["capability-catalog-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8320"]
