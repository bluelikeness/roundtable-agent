# Mock 어댑터. 빌드 컨텍스트는 adapters/ (base.py 등 공통 모듈을 함께 넣는다)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv/adapters
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY __init__.py base.py auth.py mcp_server.py admin_api.py ./
COPY mock ./mock
COPY testing ./testing

WORKDIR /srv
VOLUME /data
EXPOSE 9000 9100
CMD ["python", "-m", "adapters.mock"]
