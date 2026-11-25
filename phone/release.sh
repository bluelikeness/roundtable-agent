#!/usr/bin/env bash
# 워커 앱 배포: 빌드 → phone/dist 게시(자동 업데이트용) → 일회용 다운로드 링크와 QR → 연결된 휴대폰에 업데이트 알림.
#   phone/release.sh                                  (.env 의 PUBLIC_HOST 로 https://<PUBLIC_HOST> 를 쓴다)
#   PHONE_PUBLIC_URL=https://<주소> phone/release.sh   (다른 주소로 줄 때만)
# 기기 토큰(PHONE_DEVICE_TOKEN)·허브 토큰(HUB_ADAPTER_TOKEN)은 레포 루트 .env 에서 읽는다.
# 결과: phone/dist/roundtable-worker.apk, latest.json, qr.png (전부 레포에 넣지 않음)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW="${PHONE_GATEWAY_LOCAL:-http://127.0.0.1:9200}"
envval() { grep -E "^$1=" "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d '\r' || true; }
# 공개 주소는 각자의 도메인. 레포에는 넣지 않고 .env 의 PUBLIC_HOST 하나로 정한다
PUBLIC_HOST="$(envval PUBLIC_HOST)"
PUB="${PHONE_PUBLIC_URL:-${PUBLIC_HOST:+https://$PUBLIC_HOST}}"
[ -n "$PUB" ] || { echo ".env 에 PUBLIC_HOST=<내 도메인> 을 넣거나 PHONE_PUBLIC_URL=https://<주소> 를 주세요" >&2; exit 1; }
PUB="${PUB%/}"
DEV_TOKEN="$(envval PHONE_DEVICE_TOKEN)"
HUB_TOKEN="$(envval HUB_ADAPTER_TOKEN)"
[ -n "$DEV_TOKEN" ] && [ -n "$HUB_TOKEN" ] || { echo ".env 에 PHONE_DEVICE_TOKEN·HUB_ADAPTER_TOKEN 이 필요합니다" >&2; exit 1; }

host="${PUB#https://}"; host="${host#http://}"; host="${host%%/*}"
case "$PUB" in https://*) WS="wss://$host/phone/ws";; *) WS="ws://$host/phone/ws";; esac
# 버전 번호: 2026-01-01 부터 초 단위(늘 커진다. int 상한까지 60년 넘게 남는다)
CODE=$(( $(date -u +%s) - $(date -u -d 2026-01-01 +%s) ))
NAME="$(date +%Y.%m.%d-%H%M%S)"

OUT="$ROOT/phone/build"
PHONE_WS_URL="$WS" PHONE_DEVICE_TOKEN="$DEV_TOKEN" PHONE_VERSION_CODE="$CODE" PHONE_VERSION_NAME="$NAME" \
  bash "$ROOT/phone/build.sh" "$OUT" >/dev/null
DIST="$ROOT/phone/dist"; mkdir -p "$DIST"
cp "$OUT/roundtable-worker.apk" "$DIST/.roundtable-worker.apk.tmp"
mv -f "$DIST/.roundtable-worker.apk.tmp" "$DIST/roundtable-worker.apk"
SHA="$(sha256sum "$DIST/roundtable-worker.apk" | cut -d' ' -f1)"
SIZE="$(stat -c %s "$DIST/roundtable-worker.apk")"
printf '{"versionCode": %s, "versionName": "%s", "sha256": "%s", "size": %s, "host": "%s"}\n' \
  "$CODE" "$NAME" "$SHA" "$SIZE" "$host" > "$DIST/latest.json"
echo "게시: v$NAME ($CODE), $SIZE bytes, 서버 $WS"

# 일회용 다운로드 링크(30분, 한 번만) + QR
LINK_PATH="$(curl -sf -X POST -H "X-Hub-Token: $HUB_TOKEN" "$GW/apk/link" | python -c 'import sys,json;print(json.load(sys.stdin)["path"])')"
URL="$PUB$LINK_PATH"
(cd "$ROOT/adapters" && uv run --quiet --with "qrcode[pil]" python -c "import qrcode,sys; qrcode.make(sys.argv[1], box_size=10, border=4).save(sys.argv[2])" "$URL" "$DIST/qr.png")
echo "설치 링크(30분, 한 번만): $URL"
echo "QR: $DIST/qr.png"

# 연결된 휴대폰마다 바로 업데이트 확인(여러 폰이 함께 붙는다)
PHONES="$(curl -sf -H "X-Hub-Token: $HUB_TOKEN" "$GW/state" | python -c 'import sys,json;[print(p) for p in json.load(sys.stdin).get("phones") or []]' || true)"
sent=0
while IFS= read -r ph; do
  [ -n "$ph" ] || continue
  body="$(python -c 'import json,sys;print(json.dumps({"cmd":"update_check","timeout_s":10,"device":sys.argv[1]}))' "$ph")"
  if curl -sf -X POST -H "X-Hub-Token: $HUB_TOKEN" -H "content-type: application/json" -d "$body" "$GW/rpc" >/dev/null; then
    sent=$((sent + 1))
  fi
done <<< "$PHONES"
if [ "$sent" -gt 0 ]; then
  echo "연결된 휴대폰 ${sent}대에 업데이트 확인을 보냈습니다"
else
  echo "연결된 휴대폰 없음(다음 연결·6시간 주기에 스스로 확인)"
fi
