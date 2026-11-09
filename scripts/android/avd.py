"""봇 전용 AVD 만들기·띄우기·앱 옮겨 설치 (docs/02-architecture.md ADR-8, docs/03-adapters.md 7·9장).

avdmanager(Java 필요) 없이 AVD 정의 파일(<name>.ini, <name>.avd/config.ini)을 직접 쓴다.
로그인 상태는 AVD 디스크(userdata)에만 남는다. 이 스크립트는 로그인·계정을 다루지 않는다.

  python scripts/android/avd.py create vooy              # 전용 AVD 정의 생성(부팅하지 않음)
  python scripts/android/avd.py start vooy --port 5560   # 콜드 부팅(-gpu swiftshader_indirect, 스냅샷 저장 안 함)
  python scripts/android/avd.py sideload com.vooy.mobile --src emulator-5556 --dst emulator-5560
      # 이미 설치된 기기에서 APK(분할 포함)를 받아 전용 AVD 에 설치. Play 스토어 로그인 없이 앱을 넣고,
      # 스토어 자동 업데이트를 피한다(03 7장 리스크). 앱 로그인은 사람이 noVNC/에뮬레이터 창에서 한다.

환경: ANDROID_SDK_ROOT(없으면 %LOCALAPPDATA%/Android/Sdk 또는 ~/Android/Sdk), ANDROID_AVD_HOME(없으면 ~/.android/avd).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SYSTEM_IMAGE = "system-images;android-34;google_apis_playstore;x86_64"

# P0 에서 Vooy·피클이 동작한 설정(pickle_p0)에서 런타임이 채우는 값을 뺀 최소 집합.
CONFIG = {
    "avd.ini.encoding": "UTF-8",
    "AvdId": "{name}",
    "avd.ini.displayname": "{name}",
    "PlayStore.enabled": "true",
    "abi.type": "x86_64",
    "hw.cpu.arch": "x86_64",
    "hw.cpu.ncore": "4",
    "hw.device.manufacturer": "Google",
    "hw.device.name": "pixel_7",
    "hw.lcd.width": "1080",
    "hw.lcd.height": "2400",
    "hw.lcd.density": "420",
    "hw.ramSize": "4096",
    "vm.heapSize": "228M",
    "disk.dataPartition.size": "8G",
    "hw.keyboard": "yes",
    # Vooy 는 React Native + Skia: GPU 를 끄면 흰 화면. CPU 렌더링(swiftshader)으로 GPU 없는 서버에서도 된다(P0)
    "hw.gpu.enabled": "yes",
    "hw.gpu.mode": "swiftshader_indirect",
    # Vooy(Vulkan 앱)를 띄운 세션은 스냅샷 저장이 막힌다(UNSUPPORTED_VK_APP). 항상 콜드 부팅
    "fastboot.forceColdBoot": "yes",
    "fastboot.forceFastBoot": "no",
    "firstboot.saveToLocalSnapshot": "no",
    "firstboot.bootFromLocalSnapshot": "no",
    "image.sysdir.1": "system-images/android-34/google_apis_playstore/x86_64/",
    "tag.id": "google_apis_playstore",
    "tag.display": "Google Play",
    "target": "android-34",
    "showDeviceFrame": "no",
}


def sdk_root() -> Path:
    env = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "Android" / "Sdk" if local else Path.home() / "Android" / "Sdk"


def avd_home() -> Path:
    return Path(os.environ.get("ANDROID_AVD_HOME") or Path.home() / ".android" / "avd")


def tool(rel: str) -> str:
    exe = ".exe" if sys.platform == "win32" else ""
    return str(sdk_root() / f"{rel}{exe}")


def create(name: str, force: bool) -> None:
    img = sdk_root() / Path(*SYSTEM_IMAGE.split(";"))
    if not (img / "system.img").exists():
        sys.exit(f"시스템 이미지가 없습니다: {img}\n  sdkmanager \"{SYSTEM_IMAGE}\" 로 받으세요")
    home = avd_home()
    ini, d = home / f"{name}.ini", home / f"{name}.avd"
    if ini.exists() or d.exists():
        if not force:
            sys.exit(f"이미 있습니다: {ini}. 덮어쓰려면 --force (userdata·로그인 상태가 지워집니다)")
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    ini.write_text(
        f"avd.ini.encoding=UTF-8\npath={d}\npath.rel=avd/{name}.avd\ntarget=android-34\n", encoding="utf-8"
    )
    lines = [f"{k}={v.format(name=name)}" for k, v in CONFIG.items()]
    (d / "config.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"AVD {name} 생성: {d}")


def start(name: str, port: int, headless: bool) -> None:
    args = [
        tool("emulator/emulator"),
        "-avd", name,
        "-port", str(port),
        "-gpu", "swiftshader_indirect",
        "-no-snapshot",  # 스냅샷 로드·저장 모두 안 함(UNSUPPORTED_VK_APP)
        "-no-boot-anim",
        "-no-audio",
    ]
    if headless:
        args.append("-no-window")
    print("기동:", " ".join(args))
    subprocess.Popen(args)
    print(f"시리얼: emulator-{port}. 부팅 확인: adb -s emulator-{port} shell getprop sys.boot_completed")


def adb(*args: str, capture: bool = True) -> str:
    r = subprocess.run([tool("platform-tools/adb"), *args], check=True, capture_output=capture, text=True)
    return r.stdout if capture else ""


def sideload(package: str, src: str, dst: str) -> None:
    paths = [ln.split(":", 1)[1].strip() for ln in adb("-s", src, "shell", "pm", "path", package).splitlines() if ln.startswith("package:")]
    if not paths:
        sys.exit(f"{src} 에 {package} 가 없습니다")
    with tempfile.TemporaryDirectory() as tmp:
        local = []
        for p in paths:
            out = str(Path(tmp) / Path(p).name)
            adb("-s", src, "pull", p, out)
            local.append(out)
        adb("-s", dst, "install-multiple", "-r", *local, capture=False)
    print(f"{package} → {dst} 설치 완료 (APK {len(paths)}개). 앱 로그인은 사람이 합니다")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--force", action="store_true")
    s = sub.add_parser("start")
    s.add_argument("name")
    s.add_argument("--port", type=int, default=5560)
    s.add_argument("--headless", action="store_true")
    sl = sub.add_parser("sideload")
    sl.add_argument("package")
    sl.add_argument("--src", required=True)
    sl.add_argument("--dst", required=True)
    a = ap.parse_args()
    if a.cmd == "create":
        create(a.name, a.force)
    elif a.cmd == "start":
        start(a.name, a.port, a.headless)
    else:
        sideload(a.package, a.src, a.dst)


if __name__ == "__main__":
    main()
