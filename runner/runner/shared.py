"""허브와 공유하는 모델(hub/app/events.py, hub/app/runner_protocol.py)을 hubshared 로 불러온다.

러너 이미지에는 Dockerfile 이 두 파일을 hubshared 패키지로 복사해 둔다.
레포 체크아웃(테스트)에서는 hub/app 디렉터리를 hubshared 패키지 경로로 잡는다.
hub/app/__init__.py 는 실행하지 않고 두 모듈만 읽는다(pydantic 외 의존성 없음).
"""

import importlib.util
import os
import sys
import types
from pathlib import Path


def _ensure_hubshared() -> None:
    if importlib.util.find_spec("hubshared") is not None:
        return
    hub_app = Path(os.environ.get("HUB_APP_DIR") or Path(__file__).resolve().parents[2] / "hub" / "app")
    pkg = types.ModuleType("hubshared")
    pkg.__path__ = [str(hub_app)]
    sys.modules["hubshared"] = pkg


_ensure_hubshared()

from hubshared.events import (  # noqa: E402
    BotPending,
    BotPendingPayload,
    BotReply,
    BotReplyPayload,
    ClaudeFinal,
    ClaudeFinalPayload,
    ClaudeNote,
    ClaudeNotePayload,
    HubEvent,
    RunFinished,
    RunFinishedPayload,
    RunStarted,
    RunStartedPayload,
    SessionSystem,
    SessionSystemPayload,
)
from hubshared.runner_protocol import (  # noqa: E402
    BotEndpoint,
    RunRequest,
    Runner,
    RunnerSession,
    StreamLine,
    ToolCallRecord,
    ToolCallSink,
)

__all__ = [
    "BotEndpoint", "BotPending", "BotPendingPayload", "BotReply", "BotReplyPayload", "ClaudeFinal",
    "ClaudeFinalPayload", "ClaudeNote", "ClaudeNotePayload", "HubEvent", "RunFinished", "RunFinishedPayload",
    "RunRequest", "RunStarted", "RunStartedPayload", "Runner", "RunnerSession", "SessionSystem",
    "SessionSystemPayload", "StreamLine", "ToolCallRecord", "ToolCallSink",
]
