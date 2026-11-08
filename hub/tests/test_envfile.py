"""레포 .env 를 compose 규칙으로 읽는지."""

from tests.envfile import build_test_db_url, parse_env


def test_parse_env_rules():
    env = parse_env(
        "# 주석\n"
        "export A=1\n"
        "B=ab#cd\n"  # 공백 없는 # 은 값의 일부
        "C=value # 인라인 주석\n"
        "D='single # kept'\n"
        'E="double"\n'
        "\n"
        "NOEQ\n"
    )
    assert env == {"A": "1", "B": "ab#cd", "C": "value", "D": "single # kept", "E": "double"}


def test_password_url_encoded(tmp_path):
    p = tmp_path / ".env"
    p.write_text("POSTGRES_USER=multibot\nPOSTGRES_PASSWORD=p@ss:w/rd\n", encoding="utf-8")
    assert build_test_db_url(p) == "postgresql+asyncpg://multibot:p%40ss%3Aw%2Frd@localhost:5433/multibot_test"
