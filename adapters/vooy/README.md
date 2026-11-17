# Vooy adapter

[한국어](README.ko.md)

Drives the Vooy app (`com.vooy.mobile`) inside an emulator with `uiautomator2`. Design: `docs/03-adapters.md` §7.
Shared device code lives in `adapters/android_base.py` (shared with Pickle); selectors live in `selectors.yaml`
(the only file to change when the app updates).

> The current setup drives the apps on a real phone through the worker app (see [phone/README.md](../../phone/README.md));
> this document describes the emulator path and its manual checks.

## Setup

| Item | Value |
| --- | --- |
| Dedicated AVD | `vooy` (API 34 Google Play x86_64, 1080×2400, `-gpu swiftshader_indirect`, no snapshots). Created with `scripts/android/avd.py` |
| App install | Copy the APK from a device that already has it (`avd.py sideload`). No Play Store login needed, and auto-updates are avoided |
| Login | `VncLogin`. The operator signs in to the app on screen (noVNC, or the emulator window locally) → `/login/complete`. State stays only on the AVD disk |
| Device connection | Connects to the `ANDROID_SERIAL` device through an adb server outside the container (`ANDROID_ADB_SERVER_HOST/PORT`) |
| compose | `docker compose --profile vooy up -d vooy`. Local ports: MCP 9005, admin 9105 |
| Contract tests | `make test-adapters` runs the same adapter code against a fake device (`adapters/testing/fake_vooy.py`) as `vooy-fake`. For the real bot see "Live contract run" below |

Environment (repo-root `.env`, all optional): `VOOY_ANDROID_SERIAL` (default `emulator-5554`),
`VOOY_ADB_SERVER_HOST` (default `host.docker.internal`), `VOOY_ADB_SERVER_PORT` (default 5037), `VOOY_VNC_URL`.

### Local (Windows) adb connection

On Windows the adb server listens only on `127.0.0.1:5037` by default, so containers cannot reach it. Either:

1. Run the adapter directly on the host (no container). Simplest for local smoke tests.
   ```bash
   # from the repo root
   PYTHONPATH=. HUB_ADAPTER_TOKEN=<value from .env> ANDROID_SERIAL=emulator-5560 ADAPTER_STATE_DIR=.vooy-state MCP_PORT=9005 ADMIN_PORT=9105 uv run --project adapters --group android python -m adapters.vooy
   ```
2. Restart the adb server on all interfaces (`adb kill-server`, then `adb -a nodaemon server start`). This
   disconnects other adb users on the same PC and exposes adb on the LAN, so only do it behind a firewall.

On VM 2 (Linux), set `VOOY_ADB_SERVER_HOST` to the adb server of the emulator host (settled in the P2 VM 2 provisioning).

## Preparing the dedicated AVD (once, by a person)

```bash
python scripts/android/avd.py create vooy
python scripts/android/avd.py start vooy --port 5560
# after boot (~20 s+), copy Vooy from a device that already has it
python scripts/android/avd.py sideload com.vooy.mobile --src emulator-5556 --dst emulator-5560
```
Open Vooy in the emulator window and sign in (a dedicated account is recommended, see open items below). Never
put login details or screenshots in the repository or logs.

## Live contract run (by a person, when needed)

This sends real messages to the bot (about 20 per run). Expired login, confirmation steps, buttons and image
cases are skipped automatically (`LiveVooyDriver`). `test_restart_restores_threads` restarts the compose service
`vooy`, so run it only when the adapter was started with compose.

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml -f adapters/tests/contract/compose.yml --profile vooy up -d --build --wait webhook-sink vooy
cd adapters && CONTRACT_TARGETS=vooy uv run pytest -q tests/contract
```

## Smoke checklist (manual; docs/03 §11 + roadmap P2 gate)

Preparation: Vooy signed in on the dedicated AVD `vooy`, adapter running (compose or directly on the host), tokens
in `.env`. `$T` below is the `X-Hub-Token` value.

1. [ ] **Cold boot**: shut the emulator down and start it again with `avd.py start vooy`. Check in the app that the login survived.
2. [ ] **Probe**: `curl -s -X POST -H "X-Hub-Token: $T" http://127.0.0.1:9105/probe` → `available: true`. If the app was closed, the adapter opens it and goes to the vooy tab chat.
3. [ ] **Question**: from the hub (or an MCP client) ask Vooy for "a Korean restaurant in Gangnam with a table for 2 at lunch tomorrow" → a reply within 30 s, body in `raw`, no timestamps or `U+FFFC`.
4. [ ] **Button parsing**: if the reply has buttons or options, `structured.buttons` matches the `[버튼: X]` markers in `raw`. Other elements (emoji reactions, header icons) are not picked up as buttons.
5. [ ] **`[tap:]`**: press one button with `[tap:<action_id>]` → it is actually pressed in the app and the next reply arrives. Never press payment or booking confirmation buttons.
6. [ ] **Cards and images**: if restaurants come back as cards or images, record what gets captured (card extraction is not implemented; images are screen crops). Decide whether to reflect the screen structure in `selectors.yaml`.
7. [ ] **Typing indicator**: while waiting, check with a `uiautomator2` dump how the bottom vooy tab's `content-desc` (normally "vooy tab, Done") and the streaming node (`agent-message-segments`) change → update `streaming_rids` and `typing_tab_desc_re` in `selectors.yaml`. Right now completion is decided only by 3 s of silence.
8. [ ] **Multiple bubbles**: a reply split over two or more bubbles is combined into one `raw`.
9. [ ] **Expired login**: (optional, dedicated account only) sign out in the app → `ask` returns `error=login_required` and the hub bot panel updates → sign in again → `POST /login/complete` → `available: true`.
10. [ ] **Restart restore**: restart the adapter → `ask` continues on the same `thread_id`.
11. [ ] **Hub registration**: add `- {name: vooy, mcp_url: http://vooy:9000/mcp, admin_url: http://vooy:9100, aliases: [부이]}` to `hub/bots.yaml` and check it shows up in the bot panel (this PR did not touch hub/).

## Known behavior and open items

- **No "new chat"**: in P2 the room info and vooy settings screens were checked; there is no reset or new-chat menu. `reset` clears only adapter state; the bot-side chat continues. Vooy has long-term memory and remembers earlier context.
- **One shared chat**: every `thread_id` uses the app's single chat. Collection only reads bot bubbles below the bubble we sent, so replies do not mix. However, a notification-style message the bot sends in the meantime may be picked up as the reply.
- **Dedicated account**: with a personal account, personal notification messages land in the same chat and may be picked up as replies. A dedicated account (signed in with a dedicated Google account) is recommended — operator decision needed.
- **Rate limits**: 3 s between messages, 300 per day (`android.daily_limit`); a limit notice (`limit_patterns`) yields `rate_limited`.
