"""레포 루트 .env 를 docker compose 와 같은 규칙으로 읽어 테스트 DB 주소를 만든다.

make 의 `include .env` 는 #·$·따옴표를 compose 와 다르게 해석해 값이 어긋날 수 있어서 쓰지 않는다.
"""

from pathlib import Path
from urllib.parse import quote


def parse_env(text: str) -> dict[str, str]:
    """compose .env 규칙의 부분집합: 주석 줄, `export ` 접두, 따옴표 값, 공백 뒤 # 인라인 주석."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
            if line.split("=", 1)[1].strip()[0] == '"':
                value = value.replace('\\"', '"').replace("\\n", "\n")
        else:
            # 따옴표 없는 값은 공백 + # 부터 주석
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1] in " \t":
                    value = value[:i].rstrip()
                    break
        out[key] = value
    return out


def build_test_db_url(env_path: Path) -> str:
    env = parse_env(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    user = env.get("POSTGRES_USER", "multibot")
    password = env.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError(f"{env_path} 에 POSTGRES_PASSWORD 가 없습니다 (.env.example 참고)")
    # 비밀번호의 @ : / 같은 문자가 URL 을 깨지 않도록 인코딩한다
    return f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}@localhost:5433/multibot_test"
