"""허브 이벤트 Pydantic 모델(hub/app/events.py) → JSON Schema.

`make types` 가 이 스크립트로 스키마를 만들고 json-schema-to-typescript 로 TS 타입을 만든다.

    uv run --project hub python scripts/gen_types.py web/src/types/events.schema.json
    uv run --project hub python scripts/gen_types.py --banner web/src/types/events.ts

배너(한국어 주석)는 json2ts 인자로 넘기지 않고 여기서 붙인다. cmd 셸을 거치면 한글 인자가 깨진다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from pydantic import TypeAdapter  # noqa: E402

from app.events import ClientMessage, HubEvent  # noqa: E402


def _strip_property_titles(node):
    """필드마다 붙는 title 을 지운다. 남겨 두면 TS 에 필드별 별칭 타입(SessionId1, Error 등)이 쏟아진다."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for p in props.values():
                if isinstance(p, dict):
                    p.pop("title", None)
        for v in node.values():
            _strip_property_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_property_titles(v)


def build() -> dict:
    defs: dict = {}
    roots = {}
    for name, tp in (("HubEvent", HubEvent), ("ClientMessage", ClientMessage)):
        schema = TypeAdapter(tp).json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        schema.pop("discriminator", None)  # TS 쪽은 type 리터럴로 이미 구분된다
        defs[name] = {"title": name, **schema}
        roots[name] = {"$ref": f"#/$defs/{name}"}
    _strip_property_titles(defs)
    # 루트는 두 유니온을 모두 노출하기 위한 껍데기 객체다
    return {
        "title": "EventsSchema",
        "type": "object",
        "properties": roots,
        "additionalProperties": False,
        "$defs": dict(sorted(defs.items())),
    }


BANNER = "/* 자동 생성 파일(make types). 직접 고치지 않는다. 원본: hub/app/events.py */"


def add_banner(path: Path) -> None:
    body = path.read_text(encoding="utf-8").lstrip()
    if not body.startswith(BANNER):
        path.write_text(BANNER + chr(10) * 2 + body, encoding="utf-8", newline=chr(10))


if __name__ == "__main__":
    if sys.argv[1] == "--banner":
        add_banner(Path(sys.argv[2]))
        print(f"banner {sys.argv[2]}")
        sys.exit(0)
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    # Windows 에서도 LF 로 쓴다(.gitattributes 의 eol=lf 와 맞춤)
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}")
