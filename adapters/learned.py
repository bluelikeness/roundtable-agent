"""기기별로 배운 셀렉터 (기기별 층).

셀렉터는 두 층이다.
  기본 층   저장소의 selectors.yaml. 모두가 git 으로 받는다. 후보를 덧붙이기만 한다(옛 버전용 후보를 지우지 않는다).
  기기별 층 이 파일이 다루는 것. 어댑터 상태 폴더(/data, git 밖)에 저장해 이 허브·이 휴대폰에만 쓰인다.

어댑터가 기본 층 후보로 못 찾은 버튼을 일반 규칙(예: 글을 넣은 뒤 새로 나타난 버튼)으로 찾아 성공하면
그 버튼의 셀렉터를 "기기|패키지@앱 버전" 열쇠로 저장한다. 다음부터는 이것을 먼저 쓴다. 앱 버전이 바뀌면
그 기기·패키지의 옛 기록은 버린다(다시 찾는다). 저장된 셀렉터로 못 찾으면 지운다.

기기는 "모델/안드로이드 API 레벨"이다. 같은 앱 같은 버전이라도 안드로이드 버전에 따라 글 넣는 방법이 다르다
(2026-11-12 갤럭시 노트9·안드로이드 10: 뮤즈 입력창에 set_text 가 안 먹고 붙여넣기만 된다). 그래서 버튼뿐 아니라
"글 넣는 방법"도 처음 보낼 때 시험해 정하고(환경 맞추기) 여기에 저장한다.

계정(로그인 정보)은 열쇠가 아니다. 읽지도 저장하지도 않는다. 같은 버전인데 계정별 실험으로 화면이
다르면 저장된 셀렉터가 실패하고, 지운 뒤 다시 찾는다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def device_id(device: str | None, sdk: int | None) -> str:
    return f"{device or '?'}/{sdk if sdk is not None else '?'}"


def app_key(package: str, version: str | None, device: str | None = None) -> str:
    """기기별 층의 열쇠. device 는 device_id() 값(없으면 기기 구분 없이)."""
    base = f"{package}@{version or '?'}"
    return f"{device}|{base}" if device else base


def _scope(key: str) -> str:
    """같은 기기·같은 패키지면 같은 값(버전만 다른 열쇠를 찾는 데 쓴다)."""
    return key.rsplit("@", 1)[0]


class LearnedSelectors:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("배운 셀렉터 파일을 읽지 못해 비움: %s", e)

    def get(self, key: str, name: str) -> dict[str, Any] | None:
        sel = self._data.get(key, {}).get(name)
        return dict(sel) if isinstance(sel, dict) else None

    def put(self, key: str, name: str, sel: dict[str, Any]) -> None:
        scope = _scope(key)
        # 같은 기기·같은 앱의 다른 버전 기록은 버린다(앱이 업데이트됐다)
        for k in [k for k in self._data if k != key and _scope(k) == scope]:
            del self._data[k]
        if self._data.get(key, {}).get(name) == sel:
            return
        self._data.setdefault(key, {})[name] = dict(sel)
        log.info("셀렉터 배움: %s %s = %s", key, name, sel)
        self._save()

    def drop(self, key: str, name: str) -> None:
        if self._data.get(key, {}).pop(name, None) is not None:
            log.info("배운 셀렉터 버림(더 이상 맞지 않음): %s %s", key, name)
            if not self._data[key]:
                del self._data[key]
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
