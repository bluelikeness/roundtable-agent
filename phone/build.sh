#!/usr/bin/env bash
# 멀티봇 워커 APK 빌드(Gradle 없이 javac·d8·aapt2·apksigner).
#   PHONE_WS_URL=wss://hub.example/phone/ws PHONE_DEVICE_TOKEN=... phone/build.sh <출력 폴더>
# 두 env 는 앱의 기본 설정값이 된다(앱 화면에서 바꿀 수 있다). 토큰이 APK 안에 들어가므로
# 이 APK 는 레포에 넣지 말고 본인 휴대폰에만 설치한다.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/build}"
SDK="${ANDROID_SDK:-$LOCALAPPDATA/Android/Sdk}"
BT="$SDK/build-tools/36.0.0"
JAR="$SDK/platforms/android-35/android.jar"
JBIN="${JAVA_BIN:-/c/Program Files/Android/Android Studio/jbr/bin}"
export JAVA_HOME="$(dirname "$JBIN")"
export PATH="$JBIN:$PATH"

rm -rf "$OUT"; mkdir -p "$OUT/res" "$OUT/classes" "$OUT/dex" "$OUT/gen/dev/multibot/worker"
# 기본 설정값. 자바 문자열로 넣을 수 있게 따옴표·역슬래시를 막는다
for v in "${PHONE_WS_URL:-}" "${PHONE_DEVICE_TOKEN:-}"; do
  case "$v" in *\"*|*\\*) echo "PHONE_WS_URL/PHONE_DEVICE_TOKEN 에 따옴표·역슬래시를 쓸 수 없습니다" >&2; exit 1;; esac
done
cat > "$OUT/gen/dev/multibot/worker/Defaults.java" <<EOF
package dev.multibot.worker;
/** build.sh 가 만든 기본 설정값(레포에 없음). */
final class Defaults {
    static final String URL = "${PHONE_WS_URL:-}";
    static final String TOKEN = "${PHONE_DEVICE_TOKEN:-}";
    private Defaults() {}
}
EOF

"$BT/aapt2" compile --dir "$HERE/res" -o "$OUT/res/res.zip"
# 버전: PHONE_VERSION_CODE/NAME 이 있으면 매니페스트 값을 덮는다(release.sh 가 시각으로 매긴다)
VER_ARGS=()
[ -n "${PHONE_VERSION_CODE:-}" ] && VER_ARGS+=(--version-code "$PHONE_VERSION_CODE" --replace-version)
[ -n "${PHONE_VERSION_NAME:-}" ] && VER_ARGS+=(--version-name "$PHONE_VERSION_NAME")
"$BT/aapt2" link -o "$OUT/base.apk" -I "$JAR" --manifest "$HERE/AndroidManifest.xml" \
  --min-sdk-version 26 --target-sdk-version 35 "${VER_ARGS[@]}" "$OUT/res/res.zip"
javac -encoding UTF-8 --release 17 -classpath "$JAR" -d "$OUT/classes" \
  $(find "$HERE/src" "$OUT/gen" -name '*.java')
"$BT/d8.bat" --release --min-api 26 --lib "$JAR" --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT/dex" && "$JBIN/jar" uf "$OUT/unsigned.apk" classes.dex)
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
"$BT/apksigner.bat" sign --ks "$HOME/.android/debug.keystore" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/multibot-worker.apk" "$OUT/aligned.apk" 2>/dev/null
"$BT/apksigner.bat" verify "$OUT/multibot-worker.apk" 2>/dev/null && echo "OK: $OUT/multibot-worker.apk"
