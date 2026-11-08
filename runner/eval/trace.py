"""러너 NDJSON 한 실행분을 판정하기 좋은 모양으로 정리한다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Call:
    id: str
    bot: str
    tool: str
    input: dict[str, Any] | None
    start: int  # 스트림 안 순번(시작 기록)
    end: int | None = None  # 결과 기록 순번
    output: dict[str, Any] | None = None
    error: str | None = None

    @property
    def text(self) -> str:
        return str((self.input or {}).get("text", ""))


@dataclass
class Trace:
    lines: list[dict[str, Any]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    final: str = ""
    status: str | None = None  # run.finished status
    error: str | None = None
    claude_session_id: str | None = None
    usage: dict[str, Any] | None = None
    transport_error: str | None = None

    @classmethod
    def parse(cls, raw_lines: list[str]) -> Trace:
        t = cls()
        by_id: dict[str, Call] = {}
        for i, raw in enumerate(raw_lines):
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            t.lines.append(obj)
            tc = obj.get("tool_call")
            if tc:
                c = by_id.get(tc["id"])
                if c is None:
                    c = Call(id=tc["id"], bot=tc["bot"], tool=tc["tool"], input=tc.get("input"), start=i)
                    by_id[tc["id"]] = c
                    t.calls.append(c)
                if tc.get("done"):
                    c.end = i
                    c.output = tc.get("output")
                    c.error = tc.get("error")
                continue
            ev = obj.get("event") or {}
            p = ev.get("payload") or {}
            typ = ev.get("type")
            if typ == "run.started":
                t.claude_session_id = p.get("claude_session_id")
            elif typ == "claude.note":
                t.notes.append(p.get("text", ""))
            elif typ == "claude.final":
                t.final = p.get("text", "")
                t.usage = p.get("usage")
            elif typ == "run.finished":
                t.status = p.get("status")
                t.error = p.get("error")
        return t

    # --- 조회 도우미 ---

    def asks(self, bot: str | None = None) -> list[Call]:
        return [c for c in self.calls if c.tool == "ask" and (bot is None or c.bot == bot)]

    def statuses(self, bot: str | None = None) -> list[Call]:
        return [c for c in self.calls if c.tool == "status" and (bot is None or c.bot == bot)]

    def ask_bots(self) -> set[str]:
        return {c.bot for c in self.asks()}

    def parallel(self, calls: list[Call]) -> bool:
        """calls 가 전부 첫 결과가 오기 전에 시작됐으면(한 턴에 함께 나갔으면) True."""
        if len(calls) < 2:
            return False
        first_end = min((c.end for c in calls if c.end is not None), default=None)
        return first_end is None or all(c.start < first_end for c in calls)

    def sequential(self, a: Call, b: Call) -> bool:
        return a.end is not None and a.end < b.start

    def final_lines(self) -> list[str]:
        return [ln for ln in self.final.splitlines() if ln.strip()]

    def has_table(self) -> bool:
        rows = [ln for ln in self.final_lines() if ln.strip().startswith("|")]
        return len(rows) >= 3  # 머리 + 구분 + 한 줄 이상

    def num_turns(self) -> int | None:
        return (self.usage or {}).get("num_turns")


def mentions(text: str, *patterns: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)
