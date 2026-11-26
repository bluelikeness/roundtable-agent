#!/usr/bin/env bash
# 원탁 앱 APK 빌드(Gradle 없이 javac·d8·aapt2·apksigner). 워커(phone/build.sh)와 같은 도구를 쓴다.
#   ROUNDTABLE_URL=https://<내 도메인>/ phone/client/build.sh <출력 폴더>
# APK 에는 원탁 주소만 들어간다(토큰 없음). 그래도 주소가 각자의 도메인이라 레포에는 넣지 않는다.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/build}"
SDK="${ANDROID_SDK:-$LOCALAPPDATA/Android/Sdk}"
BT="$SDK/build-tools/36.0.0"
JAR="$SDK/platforms/android-35/android.jar"
JBIN="${JAVA_BIN:-/c/Program Files/Android/Android Studio/jbr/bin}"
export JAVA_HOME="$(dirname "$JBIN")"
export PATH="$JBIN:$PATH"

URL="${ROUNDTABLE_URL:?ROUNDTABLE_URL=https://<주소>/ 가 필요합니다}"
case "$URL" in https://*|http://*) ;; *) echo "ROUNDTABLE_URL 은 http(s):// 로 시작해야 합니다" >&2; exit 1;; esac
case "$URL" in *\"*|*\*) echo "ROUNDTABLE_URL 에 따옴표·역슬래시를 쓸 수 없습니다" >&2; exit 1;; esac
VNAME="${APP_VERSION_NAME:-dev}"

rm -rf "$OUT"; mkdir -p "$OUT/res" "$OUT/classes" "$OUT/dex" "$OUT/gen/dev/roundtable/app"
cat > "$OUT/gen/dev/roundtable/app/Defaults.java" <<JAVA
package dev.roundtable.app;
/** build.sh 가 만든 설정값(레포에 없음). */
final class Defaults {
    static final String URL = "$URL";
    static final String VERSION = "$VNAME";
    private Defaults() {}
}
JAVA

"$BT/aapt2" compile --dir "$HERE/res" -o "$OUT/res/res.zip"
VER_ARGS=()
[ -n "${APP_VERSION_CODE:-}" ] && VER_ARGS+=(--version-code "$APP_VERSION_CODE" --replace-version)
[ -n "${APP_VERSION_NAME:-}" ] && VER_ARGS+=(--version-name "$APP_VERSION_NAME")
"$BT/aapt2" link -o "$OUT/base.apk" -I "$JAR" --manifest "$HERE/AndroidManifest.xml" \
  --min-sdk-version 26 --target-sdk-version 35 "${VER_ARGS[@]}" "$OUT/res/res.zip"
javac -encoding UTF-8 --release 17 -classpath "$JAR" -d "$OUT/classes" \
  $(find "$HERE/src" "$OUT/gen" -name '*.java')
"$BT/d8.bat" --release --min-api 26 --lib "$JAR" --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT/dex" && "$JBIN/jar" uf "$OUT/unsigned.apk" classes.dex)
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
"$BT/apksigner.bat" sign --ks "$HOME/.android/debug.keystore" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/roundtable.apk" "$OUT/aligned.apk" 2>/dev/null
"$BT/apksigner.bat" verify "$OUT/roundtable.apk" 2>/dev/null && echo "OK: $OUT/roundtable.apk"
