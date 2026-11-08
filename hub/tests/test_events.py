"""WebSocket 이벤트 모델이 docs/05-ui.md 6장의 봉투·type 목록과 일치하는지."""

import uuid

import pytest
from pydantic import TypeAdapter, ValidationError

from app.events import CLIENT_MESSAGE_TYPES, SERVER_EVENT_TYPES, ClientMessage, HubEvent

hub = TypeAdapter(HubEvent)
client_msg = TypeAdapter(ClientMessage)
RUN = str(uuid.uuid4())
SID = str(uuid.uuid4())
MID = str(uuid.uuid4())

PAYLOADS = {
    "message.ack": {"message_id": MID, "run_id": RUN, "status": "queued"},
    "run.started": {"run_id": RUN, "claude_session_id": "abc"},
    "claude.note": {"run_id": RUN, "text": "Vooy 답을 뮤즈에 넘깁니다"},
    "bot.pending": {"run_id": RUN, "bot": "vooy", "tool_call_id": "toolu_1", "queue_position": 0},
    "bot.reply": {
        "run_id": RUN, "bot": "vooy", "tool_call_id": "toolu_1", "ok": True, "raw": "원문",
        "structured": {"buttons": []}, "needs_input": False, "error": None, "latency_ms": 1200, "message_id": MID,
    },
    "claude.final": {"run_id": RUN, "text": "정리", "message_id": MID, "usage": {"input_tokens": 10}},
    "run.finished": {"run_id": RUN, "status": "done"},
    "bot.status": {
        "bot": "vooy", "available": True, "reason": None, "last_ok_at": None,
        "queue_depth": 0, "avg_latency_ms": None, "enabled": True,
    },
    "session.system": {"text": "세션을 새로 시작했습니다"},
}


DESIGN_SERVER_TYPES = {  # docs/05-ui.md 6장 서버 → 클라이언트 표
    "message.ack", "run.started", "claude.note", "bot.pending", "bot.reply",
    "claude.final", "run.finished", "bot.status", "session.system",
}
DESIGN_CLIENT_TYPES = {"subscribe", "send", "cancel_queued", "stop_run"}


def test_type_lists_match_design():
    assert set(SERVER_EVENT_TYPES) == DESIGN_SERVER_TYPES == set(PAYLOADS)
    assert len(SERVER_EVENT_TYPES) == len(DESIGN_SERVER_TYPES)
    assert set(CLIENT_MESSAGE_TYPES) == DESIGN_CLIENT_TYPES


@pytest.mark.parametrize("etype", SERVER_EVENT_TYPES)
def test_every_server_event_parses(etype):
    session_id = None if etype == "bot.status" else SID
    seq = None if etype == "bot.status" else 7
    ev = hub.validate_python(
        {"type": etype, "session_id": session_id, "seq": seq, "ts": "2026-11-08T00:00:00Z", "payload": PAYLOADS[etype]}
    )
    assert ev.type == etype
    dumped = ev.model_dump(mode="json")
    assert set(dumped) == {"type", "session_id", "seq", "ts", "payload"}


def test_unknown_type_and_extra_field_rejected():
    base = {"session_id": SID, "seq": 1, "ts": "2026-11-08T00:00:00Z"}
    with pytest.raises(ValidationError):
        hub.validate_python({**base, "type": "bot.unknown", "payload": {}})
    with pytest.raises(ValidationError):
        hub.validate_python({**base, "type": "claude.note", "payload": {"run_id": RUN, "text": "x", "extra": 1}})


def test_client_messages():
    m = client_msg.validate_python({"type": "subscribe", "payload": {"session_id": SID, "since_seq": 3}})
    assert m.payload.since_seq == 3
    m = client_msg.validate_python({"type": "send", "payload": {"text": "안녕", "model": "opus"}})
    assert m.payload.model == "opus"
    with pytest.raises(ValidationError):
        client_msg.validate_python({"type": "send", "payload": {"text": "x", "model": "gpt"}})
