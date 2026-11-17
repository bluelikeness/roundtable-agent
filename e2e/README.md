# UI E2E (P1-7)

[한국어](README.ko.md)

Five Playwright scenarios. They run against mock adapters with the real hub, database, web UI and runner
(`claude -p`) (docs/05-ui.md §8).

```bash
make e2e                       # start the stack → run all 5 → stop the stack
make e2e ARGS="-g T8"          # a single scenario
make e2e E2E_KEEP=1            # keep the stack running afterwards (faster reruns). Stop it with: docker compose -p roundtable-e2e down
```

Requirements: the runner's Claude Code login (`make runner-login`; the e2e stack borrows the production volume
`roundtable_runner-claude`), Node 20+ and pnpm, Playwright Chromium (`cd e2e && pnpm exec playwright install chromium`).
In Git Bash, do not set `MSYS_NO_PATHCONV=1`: it breaks the pnpm path inside make.

## Scenarios

| Test | Orchestrator case | Hub bots.yaml | What the UI must show |
| --- | --- | --- | --- |
| T2 | single bot | `bots/default.yaml` | "running" header, pending bubble replaced in place by the raw answer, summary, no buttons, session title, restored after reload |
| T3 | fan-out to 3 bots | `bots/default.yaml` | header "n/3 replies", 3 raw answers, comparison table (`table`) in the summary |
| T7 | follow-up question passed to the user | `bots/budget.yaml` | "the bot is asking for more information" tag, budget question in the summary, no second call |
| T8→T9 | confirmation request → proceed | `bots/confirm.yaml` | confirmation box (no buttons), no automatic confirmation, clicking `[Proceed]` in the summary → "진행해" → `[tap:]` confirms, old buttons disappear |
| T10 | bot unavailable | `bots/b-down.yaml` | panel shows "login required" with a re-login button, no mock-b bubble, re-login sheet opens and closes with Esc |

## Design notes

- **Why it is not part of `make test`**: it runs the real `claude -p`. It needs the runner login and uses
  subscription quota, and LLM answers vary between runs, so it is not deterministic. Like `make eval`, it is a
  separate target to run after prompt or UI changes and at gate checks.
- **Separate compose project `roundtable-e2e`**: it never touches the production stack (`roundtable`) or the
  evaluation stack (`roundtable-eval`). The database is on tmpfs and only the web UI is exposed, on `127.0.0.1:8090`.
- **Switching scenarios**: bot names (tool names `mcp__mock-a__*`) stay the same; only the `mcp_url` in the
  hub's `bots.yaml` (`e2e/out/bots/bots.yaml`) is pointed at a variant mock container. The hub rereads this file
  on every run and every health check (every 2 seconds in E2E). No hub code was changed. Mock settings are the
  same `runner/eval/mocks/*.yaml` used by the evaluation (no random failures or follow-up questions).
- **Login**: `global-setup.ts` creates a test operator in the E2E database with
  `hub-admin create-operator --password-stdin`. The random password and TOTP secret live only in process memory.
  It signs in through the login page and stores the cookie in `out/state.json`. The cookie is valid only for
  this stack and is never committed.
- **Assertions**: summary wording differs between LLM runs, so only factual keywords are checked. Format (line
  count, first line, "진행할까요?") is judged by `make eval`. Retries are 0.
- Artifacts (`out/`: temporary secrets, cookie, HTML report, failure traces) are never committed.
