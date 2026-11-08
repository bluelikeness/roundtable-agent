"""ClaudeCodeRunner: 메시지마다 `claude -p` 를 띄우고 stream-json 을 허브 이벤트로 바꾼다 (02 4장).

- 첫 메시지는 새 세션, 이후는 --resume. 분할 조건이면 새 세션 + <session_summary> 주입(04 9장)
- 실행마다 mcp.json·시스템 프롬프트를 runs_dir/<run_id>/ 에 만들고 끝나면 지운다
- 작업 디렉터리는 고정된 빈 디렉터리(--resume 이 cwd 기준으로 세션을 찾는다)
- 전체 타임아웃(기본 600초)이나 stop() 이면 SIGTERM, 유예 뒤 SIGKILL
- 프롬프트는 stdin 으로 넘긴다("-" 로 시작하는 메시지가 옵션으로 읽히지 않게)
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import uuid
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from runner.config import RunnerSettings
from runner.mcp_config import TOKEN_ENV, allowed_tools, build_mcp_config
from runner.shared import (
    HubEvent,
    Runner,
    RunnerSession,
    SessionSystem,
    SessionSystemPayload,
    ToolCallRecord,
    ToolCallSink,
)
from runner.stream import StreamParser
from runner.summary import REASON_TEXT, build_summary, inject_summary, split_reason

log = logging.getLogger("runner.spawn")

# 한 줄에 봇 원문이 통째로 들어오므로 기본(64KiB)보다 넉넉하게
LINE_LIMIT = 16 * 1024 * 1024
STDERR_TAIL = 40
# --resume 대상 세션 파일이 없을 때 Claude Code 가 내는 문구. 이 경우 새 세션으로 한 번 더 띄운다
RESUME_MISSING = "No conversation found"


def render_prompt(template: str, values: dict[str, object]) -> str:
    """{{name}} 자리를 채운다. 모르는 이름은 그대로 둔다(04 8장 주입 변수).

    작성자용 <!-- --> 주석은 Claude 에 넘기지 않는다.
    """
    template = re.sub(r"<!--.*?-->\s*", "", template, flags=re.DOTALL)
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


class ClaudeCodeRunner(Runner):
    def __init__(self, settings: RunnerSettings, on_tool_call: ToolCallSink | None = None):
        self.settings = settings
        self.on_tool_call = on_tool_call
        self._stops: dict[uuid.UUID, asyncio.Event] = {}

    async def stop(self, run_id: uuid.UUID) -> bool:
        ev = self._stops.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    async def start(self, session: RunnerSession, text: str) -> AsyncIterator[HubEvent]:
        now = datetime.now(UTC)
        reason = split_reason(session, now)
        resume = session.claude_session_id if reason is None else None
        prompt = text
        if reason is not None:
            prompt = inject_summary(build_summary(session), text)
            yield self._system(session, f"Claude 세션을 새로 시작했습니다({REASON_TEXT[reason]}). 이전 대화 요약을 넘겼습니다.")

        stop = self._stops.setdefault(session.run_id, asyncio.Event())
        try:
            missing = False
            # 소비자가 중간에 끊어도 하위 제너레이터의 정리(프로세스 종료·run_dir 삭제)가 바로 돌게 aclosing
            async with contextlib.aclosing(self._attempt(session, prompt, resume, stop)) as first:
                async for item in first:
                    if isinstance(item, str):  # RESUME_MISSING
                        missing = True
                        break
                    yield item
            if missing:
                # 러너 볼륨이 바뀌어 세션 파일이 없다. 요약을 붙여 새 세션으로 한 번 더
                yield self._system(session, "이전 Claude 세션을 찾지 못해 새로 시작했습니다. 이전 대화 요약을 넘겼습니다.")
                retry = inject_summary(build_summary(session), text)
                async with contextlib.aclosing(self._attempt(session, retry, None, stop)) as second:
                    async for item in second:
                        if not isinstance(item, str):
                            yield item
        finally:
            self._stops.pop(session.run_id, None)

    def _system(self, session: RunnerSession, text: str) -> HubEvent:
        return SessionSystem(
            type="session.system", session_id=session.session_id, seq=None, ts=datetime.now(UTC),
            payload=SessionSystemPayload(text=text),
        )

    def _args(self, session: RunnerSession, run_dir: Path, resume: str | None) -> list[str]:
        s = self.settings
        args = [
            *s.claude_bin,
            "-p",
            "--output-format", "stream-json", "--verbose",
            "--append-system-prompt-file", str(run_dir / "orchestrator.md"),
            "--mcp-config", str(run_dir / "mcp.json"),
            "--strict-mcp-config",
            "--disallowedTools", s.disallowed_tools,
            "--max-turns", str(s.max_turns),
            "--model", session.model,
        ]
        tools = allowed_tools(session.bots)
        if tools:
            args += ["--allowedTools", ",".join(tools)]
        if resume:
            args += ["--resume", resume]
        return args

    def _prepare(self, session: RunnerSession) -> Path:
        s = self.settings
        s.work_dir.mkdir(parents=True, exist_ok=True)
        run_dir = s.runs_dir / str(session.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "mcp.json").write_text(json.dumps(build_mcp_config(session.bots), ensure_ascii=False), encoding="utf-8")
        template = s.prompt_file.read_text(encoding="utf-8")
        values = {
            "session_id": session.session_id,
            "bot_aliases": ", ".join(b.name for b in session.bots) or "(없음)",
            "debate_max_rounds": s.debate_max_rounds,
            "auto_confirm_limit": s.auto_confirm_limit,
            "max_turns": s.max_turns,
        }
        (run_dir / "orchestrator.md").write_text(render_prompt(template, values), encoding="utf-8")
        return run_dir

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        token = self.settings.read_token()
        if token:
            env[TOKEN_ENV] = token
        # 고정 버전 유지: 실행 중 자동 업데이트를 막는다
        env["DISABLE_AUTOUPDATER"] = "1"
        return env

    async def _emit_records(self, items) -> AsyncIterator[HubEvent]:
        for it in items:
            if isinstance(it, ToolCallRecord):
                if self.on_tool_call is not None:
                    await self.on_tool_call(it)
            else:
                yield it

    async def _attempt(
        self, session: RunnerSession, prompt: str, resume: str | None, stop: asyncio.Event
    ) -> AsyncIterator[HubEvent | str]:
        """프로세스 한 번. resume 대상이 없어서 실패하면 RESUME_MISSING 문자열을 낸다."""
        s = self.settings
        parser = StreamParser(session_id=session.session_id, run_id=session.run_id)
        run_dir = self._prepare(session)
        stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL)
        proc: asyncio.subprocess.Process | None = None
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._args(session, run_dir, resume),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(s.work_dir),
                    env=self._env(),
                    limit=LINE_LIMIT,
                )
            except OSError as e:
                log.error("claude 실행 실패: %s", e)
                async for ev in self._emit_records(parser.finish("failed", "spawn_failed")):
                    yield ev
                return

            assert proc.stdin and proc.stdout and proc.stderr
            proc.stdin.write(prompt.encode("utf-8"))
            with contextlib.suppress(ConnectionError):
                await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(_drain(proc.stderr, stderr_tail))

            loop = asyncio.get_running_loop()
            deadline = loop.time() + s.timeout_s
            stop_wait = asyncio.create_task(stop.wait())
            ended: tuple[str, str] | None = None  # (status, error) — 러너가 끊은 경우
            try:
                while True:
                    read = asyncio.create_task(proc.stdout.readline())
                    done, _ = await asyncio.wait(
                        {read, stop_wait}, timeout=max(0.0, deadline - loop.time()), return_when=asyncio.FIRST_COMPLETED
                    )
                    if read not in done:
                        read.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await read
                        ended = ("failed", "stopped") if stop_wait in done else ("timeout", "timeout")
                        break
                    try:
                        line = read.result()
                    except ValueError:  # 한 줄이 LINE_LIMIT 를 넘음
                        log.error("stream-json 한 줄이 너무 깁니다")
                        ended = ("failed", "line_too_long")
                        break
                    if not line:
                        break
                    async for ev in self._emit_records(parser.feed(line.decode("utf-8", errors="replace"))):
                        yield ev
                    if parser.finished:
                        break
            finally:
                stop_wait.cancel()

            if ended is not None:
                await _terminate(proc, s.term_grace_s)
            rc = await _wait(proc, s.term_grace_s)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(stderr_task, timeout=1)

            if parser.finished:
                return
            if ended is not None:
                status, error = ended
            else:
                tail = "\n".join(stderr_tail)
                if resume and not parser.started and RESUME_MISSING in tail:
                    yield RESUME_MISSING
                    return
                status, error = "failed", f"exit_{rc}"
                # stderr 끝부분만 남긴다(봇 원문·토큰이 섞일 수 있으니 길게 남기지 않는다)
                log.warning("claude 비정상 종료 rc=%s stderr(끝)=%s", rc, tail[-500:])
            async for ev in self._emit_records(parser.finish(status, error)):  # type: ignore[arg-type]
                yield ev
        finally:
            if proc is not None and proc.returncode is None:
                await _terminate(proc, s.term_grace_s)
            shutil.rmtree(run_dir, ignore_errors=True)


async def _drain(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        tail.append(line.decode("utf-8", errors="replace").rstrip())


async def _wait(proc: asyncio.subprocess.Process, timeout: float) -> int | None:
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError:
        await _terminate(proc, timeout)
        return proc.returncode


async def _terminate(proc: asyncio.subprocess.Process, grace_s: float) -> None:
    """SIGTERM → 유예 → SIGKILL."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=grace_s)
