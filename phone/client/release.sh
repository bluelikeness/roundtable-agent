#!/usr/bin/env bash
# 원탁 앱 배포: 빌드 → phone/dist/roundtable.apk → 일회용 다운로드 링크(30분)와 QR.
#   phone/client/release.sh                          (.env 의 PUBLIC_HOST 로 https://<PUBLIC_HOST>/ 를 연다)
#   ROUNDTABLE_URL=https://<주소>/ phone/client/release.sh
# 결과: phone/dist/roundtable.apk, roundtable-qr.png (레포에 넣지 않음)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GW="${PHONE_GATEWAY_LOCAL:-http://127.0.0.1:9200}"
envval() { grep -E "^$1=" "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d '\r' || true; }
PUBLIC_HOST="$(envval PUBLIC_HOST)"
URL="${ROUNDTABLE_URL:-${PUBLIC_HOST:+https://$PUBLIC_HOST/}}"
[ -n "$URL" ] || { echo ".env 에 PUBLIC_HOST=<내 도메인> 을 넣거나 ROUNDTABLE_URL=https://<주소>/ 를 주세요" >&2; exit 1; }
HUB_TOKEN="$(envval HUB_ADAPTER_TOKEN)"
[ -n "$HUB_TOKEN" ] || { echo ".env 에 HUB_ADAPTER_TOKEN 이 필요합니다" >&2; exit 1; }
PUB="${URL%/}"; PUB="$(printf '%s' "$PUB" | sed -E 's#^(https?://[^/]+).*#\1#')"

CODE=$(( $(date -u +%s) - $(date -u -d 2026-01-01 +%s) ))
NAME="$(date +%Y.%m.%d-%H%M%S)"
OUT="$ROOT/phone/client/build"
ROUNDTABLE_URL="$URL" APP_VERSION_CODE="$CODE" APP_VERSION_NAME="$NAME" bash "$ROOT/phone/client/build.sh" "$OUT" >/dev/null
DIST="$ROOT/phone/dist"; mkdir -p "$DIST"
cp "$OUT/roundtable.apk" "$DIST/.roundtable.apk.tmp"
mv -f "$DIST/.roundtable.apk.tmp" "$DIST/roundtable.apk"
echo "원탁 앱 v$NAME ($CODE), $(stat -c %s "$DIST/roundtable.apk") bytes"

LINK_PATH="$(curl -sf -X POST -H "X-Hub-Token: $HUB_TOKEN" "$GW/apk/link?app=client" | python -c 'import sys,json;print(json.load(sys.stdin)["path"])')"
LINK="$PUB$LINK_PATH"
(cd "$ROOT/adapters" && uv run --quiet --with "qrcode[pil]" python -c "import qrcode,sys; qrcode.make(sys.argv[1], box_size=10, border=4).save(sys.argv[2])" "$LINK" "$DIST/roundtable-qr.png")
echo "설치 링크(30분, 한 번만): $LINK"
echo "QR: $DIST/roundtable-qr.png"
