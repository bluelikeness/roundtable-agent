# Claude Code 러너. 빌드 컨텍스트는 레포 루트(허브와 공유하는 모델 두 파일을 함께 넣는다).
# Claude Code 버전은 여기서만 고정한다. 올리지 않는다(CLAUDE.md). P0 ④ 검증 버전.
FROM python:3.12-slim

ARG CLAUDE_CODE_VERSION=2.1.310

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 전역 hook·plugin 없는 빈 설정으로 돌린다(P0 ④). 로그인 정보만 이 볼륨에 남는다.
# CLAUDE_CONFIG_DIR 로 ~/.claude.json 까지 볼륨 안에 두어 컨테이너를 다시 만들어도 로그인이 유지된다.
RUN useradd --create-home --uid 1000 runner
ENV HOME=/home/runner \
    CLAUDE_CONFIG_DIR=/home/runner/.claude \
    DISABLE_AUTOUPDATER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:/home/runner/.local/bin:$PATH"

USER runner
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_VERSION} \
 && claude --version | grep -F "${CLAUDE_CODE_VERSION}" \
 && mkdir -p /home/runner/.claude

USER root
WORKDIR /srv/runner
COPY runner/pyproject.toml runner/uv.lock runner/.python-version ./
RUN uv sync --frozen --no-dev

# 허브와 공유하는 모델(hub/app/events.py, runner_protocol.py) → hubshared 패키지
COPY hub/app/events.py hub/app/runner_protocol.py ./hubshared/
RUN touch ./hubshared/__init__.py
COPY runner/runner ./runner
COPY runner/orchestrator.md ./orchestrator.md
# Claude Code 작업 디렉터리: 비어 있고 CLAUDE.md 없음. --resume 이 이 경로 기준이라 고정
RUN mkdir -p /srv/runner/work && chown runner:runner /srv/runner/work

USER runner
VOLUME /home/runner/.claude
EXPOSE 8700
CMD ["python", "-m", "runner"]
