"""계약 테스트 대상과 드라이버 (docs/03-adapters.md 11장).

대상은 compose 로 띄운 어댑터다(make test-adapters). 어댑터마다 "느린 응답 유도", "세션 제거",
"재시작" 같은 시나리오를 만드는 방법이 다르므로 드라이버가 그 차이를 감춘다.
실봇 어댑터는 계약 테스트 대상에 넣을 때 드라이버를 하나 추가한다.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.override.yml",
    "-f",
    "adapters/tests/contract/compose.yml",
]
SINK_URL = os.environ.get("CONTRACT_WEBHOOK_SINK", "http://127.0.0.1:9199")


def _load_token() -> str:
    """레포 루트 .env 의 HUB_ADAPTER_TOKEN. 해석 규칙은 허브 테스트의 envfile 과 같다."""
    if os.environ.get("HUB_ADAPTER_TOKEN"):
        return os.environ["HUB_ADAPTER_TOKEN"]
    spec = importlib.util.spec_from_file_location("hub_envfile", ROOT / "hub" / "tests" / "envfile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env_path = ROOT / ".env"
    env = mod.parse_env(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    token = env.get("HUB_ADAPTER_TOKEN", "")
    if not token:
        raise RuntimeError(f"{env_path} 에 HUB_ADAPTER_TOKEN 이 없습니다 (.env.example 참고)")
    return token


TOKEN = _load_token()


def compose(*args: str, timeout: float = 120) -> None:
    subprocess.run([*COMPOSE, *args], cwd=ROOT, check=True, timeout=timeout, capture_output=True)


@dataclass
class MockDriver:
    """Mock 어댑터용 시나리오 드라이버. text 지시어와 compose 명령으로 상황을 만든다."""

    service: str
    shared_thread: bool = False  # Vooy 처럼 봇 쪽 대화방이 하나뿐인 어댑터면 True

    def normal_text(self, s: str) -> str:
        return f"[mock:ok] {s}"

    def slow_text(self, s: str) -> str:
        return f"[mock:slow] {s}"

    def buttons_text(self) -> str:
        return "[mock:ok] 강남 식당 추천해줘"

    def confirm_text(self) -> str:
        return "[mock:confirm] 기차표 결제해줘"

    def image_text(self) -> str | None:
        return "[mock:image] 사진 보여줘"

    def expire_login(self) -> None:
        compose("exec", "-T", self.service, "rm", "-f", "/data/session.json")

    def restore_login(self) -> None:
        """사람이 봇에 다시 로그인하는 단계. 관리 HTTP /login/complete 가 상태를 저장하는 어댑터는 할 일이 없다."""

    def restart(self) -> None:
        compose("restart", self.service)


@dataclass
class FakeVooyDriver(MockDriver):
    """Vooy 어댑터 + 가짜 기기(adapters/testing/fake_vooy.py). 지시어 이름만 다르다."""

    shared_thread: bool = True  # 앱의 에이전트 대화방은 하나(03 7장)

    def normal_text(self, s: str) -> str:
        return s

    def slow_text(self, s: str) -> str:
        return f"[vooy:slow] {s}"

    def buttons_text(self) -> str:
        return "[vooy:buttons] 강남 식당 추천해줘"

    def confirm_text(self) -> str:
        return "[vooy:confirm] 기차표 결제해줘"

    def image_text(self) -> str | None:
        return "[vooy:image] 사진 보여줘"

    def expire_login(self) -> None:
        compose("exec", "-T", self.service, "rm", "-f", "/data/fake-vooy/session")


@dataclass
class PhoneVooyDriver(FakeVooyDriver):
    """휴대폰 워커 경로의 Vooy(게이트웨이 + 가짜 휴대폰, adapters/testing/fake_phone.py).

    로그인 상태는 가짜 휴대폰 쪽에 있다. 실제로도 사람이 휴대폰에서 다시 로그인한다.
    """

    phone_service: str = "fake-phone"

    def expire_login(self) -> None:
        compose("exec", "-T", self.phone_service, "rm", "-f", "/data/fake-vooy/session")

    def restore_login(self) -> None:
        compose("exec", "-T", self.phone_service, "sh", "-c", "echo logged-in > /data/fake-vooy/session")


@dataclass
class PhoneChatDriver(MockDriver):
    """피클·뮤즈 어댑터(범용 채팅 앱) + 가짜 휴대폰의 가짜 채팅 앱(adapters/testing/fake_chat.py)."""

    shared_thread: bool = True  # 앱의 대화방 하나를 쓴다
    phone_service: str = "fake-phone"
    app_dir: str = ""  # 가짜 휴대폰 안 앱 상태 폴더

    def normal_text(self, s: str) -> str:
        return s

    def slow_text(self, s: str) -> str:
        return f"[chat:slow] {s}"

    def buttons_text(self) -> str:
        return "[chat:buttons] 강남 식당 추천해줘"

    def confirm_text(self) -> str:
        return "[chat:confirm] 기차표 결제해줘"

    def image_text(self) -> str | None:
        return None  # 이미지는 범용 채팅 어댑터가 아직 뽑지 않는다

    def expire_login(self) -> None:
        compose("exec", "-T", self.phone_service, "rm", "-f", f"{self.app_dir}/session")

    def restore_login(self) -> None:
        compose("exec", "-T", self.phone_service, "sh", "-c", f"echo logged-in > {self.app_dir}/session")


@dataclass
class LiveVooyDriver(MockDriver):
    """실제 Vooy(에뮬레이터 + 로그인된 앱). 사람이 CONTRACT_TARGETS=vooy 로 돌린다.

    실봇에 메시지가 실제로 간다(한 번 돌리면 20건 안팎). 실봇에서 만들 수 없는 상황은 건너뛴다:
    로그인 만료(로그아웃하면 사람이 다시 로그인해야 한다), 확정 단계(실제 결제·예약 위험), 이미지.
    느린 응답은 timeout_s=10 으로 만든다(Vooy 응답이 12~15초라 10초 안에 오지 않는다).
    """

    shared_thread: bool = True

    def normal_text(self, s: str) -> str:
        return f"(자동 시험) '{s}' 라고만 짧게 답해줘"

    def slow_text(self, s: str) -> str:
        return f"(자동 시험) {s}"

    def buttons_text(self) -> str:
        pytest.skip("실봇에서 버튼이 달린 응답을 확정적으로 만들 수 없음(스모크 체크리스트에서 사람이 확인)")

    def confirm_text(self) -> str:
        pytest.skip("실봇 확정 단계는 실제 결제·예약 위험이 있어 계약 테스트에서 만들지 않음")

    def image_text(self) -> str | None:
        return None

    def expire_login(self) -> None:
        pytest.skip("실봇 로그인 만료는 사람이 로그아웃·재로그인해야 해서 자동으로 만들지 않음")


@dataclass
class Target:
    name: str
    mcp_url: str
    admin_url: str
    driver: MockDriver

    def admin(self, token: str | None = TOKEN) -> httpx.AsyncClient:
        headers = {"X-Hub-Token": token} if token is not None else {}
        return httpx.AsyncClient(base_url=self.admin_url, headers=headers, timeout=60)

    def wait_healthy(self, timeout_s: float = 90) -> None:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            try:
                r = httpx.get(f"{self.admin_url}/health", headers={"X-Hub-Token": TOKEN}, timeout=3)
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"{self.name} 가 {timeout_s}초 안에 뜨지 않았습니다")


# 이름 → (MCP 포트, 관리 포트). docker-compose.override.yml 과 맞춘다
KNOWN = {
    "mock-a": (9001, 9101),
    "mock-b": (9002, 9102),
    "mock-c": (9003, 9103),
    "vooy-fake": (9004, 9104),
    "vooy": (9005, 9105),
    "vooy-phone": (9006, 9106),
    "pickle-phone": (9009, 9109),
    "muse-phone": (9010, 9110),
}
DRIVERS = {
    "vooy-fake": FakeVooyDriver,
    "vooy": LiveVooyDriver,
    "vooy-phone": PhoneVooyDriver,
    "pickle-phone": lambda service: PhoneChatDriver(service=service, phone_service="fake-phone-pickle", app_dir="/data/fake-client"),
    "muse-phone": lambda service: PhoneChatDriver(service=service, phone_service="fake-phone-muse", app_dir="/data/fake-aura"),
}


def _targets() -> list[Target]:
    names = [n.strip() for n in os.environ.get("CONTRACT_TARGETS", "mock-a,mock-b,mock-c,vooy-fake,vooy-phone,pickle-phone,muse-phone").split(",") if n.strip()]
    out = []
    for n in names:
        if n not in KNOWN:
            raise ValueError(f"CONTRACT_TARGETS 에 모르는 대상 {n!r}. 가능한 값: {', '.join(KNOWN)}")
        mcp_port, admin_port = KNOWN[n]
        out.append(
            Target(
                name=n,
                mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
                admin_url=f"http://127.0.0.1:{admin_port}",
                driver=DRIVERS.get(n, MockDriver)(service=n),
            )
        )
    return out


TARGETS = _targets()


def pytest_generate_tests(metafunc):
    # 같은 어댑터를 겨누는 테스트는 한 xdist 워커에서 순서대로 돈다(--dist loadgroup).
    # 로그인 만료·큐 깊이·재시작 테스트가 다른 테스트와 겹치면 서로를 깨뜨린다.
    if "target" in metafunc.fixturenames:
        metafunc.parametrize(
            "target",
            [pytest.param(t, id=t.name, marks=pytest.mark.xdist_group(t.name)) for t in TARGETS],
            scope="module",
        )


@pytest.fixture(scope="session", autouse=True)
def _adapters_up():
    for t in TARGETS:
        t.wait_healthy()

