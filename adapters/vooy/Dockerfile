# Vooy 어댑터 (docs/03-adapters.md 7장). 빌드 컨텍스트는 adapters/ (base.py 등 공통 모듈을 함께 넣는다)
# 에뮬레이터는 이 컨테이너 밖에 있다. adb 서버도 밖에 두고 ANDROID_ADB_SERVER_HOST/PORT 로 붙는다
# (로컬: 호스트의 adb 서버, VM 2: 에뮬레이터 호스트). 컨테이너 안에는 adb 바이너리·APK 가 없다.
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
RUN uv sync --frozen --no-dev --group android

COPY __init__.py base.py auth.py mcp_server.py admin_api.py android_base.py phonechat.py learned.py ./
COPY vooy ./vooy
# 휴대폰 워커 경로(PhoneDevice·게이트웨이). 같은 이미지로 phone-gateway 도 띄운다(python -m adapters.phone)
COPY phone ./phone
# 피클·뮤즈(범용 채팅 앱 어댑터 phonechat.py)도 같은 이미지로 띄운다(python -m adapters.pickle·muse)
COPY pickle ./pickle
COPY muse ./muse
COPY cue ./cue
# 계약 테스트용 가짜 기기(adapters.testing.fake_vooy). 운영에서는 ANDROID_DEVICE_FACTORY 를 비워 쓰지 않는다
COPY testing ./testing

WORKDIR /srv
VOLUME /data
EXPOSE 9000 9100
CMD ["python", "-m", "adapters.vooy"]
