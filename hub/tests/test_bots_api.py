"""GET /api/bots: 웹의 @자동완성·봇 색용 봇 목록."""

from app.config import get_settings
from app.main import app


async def test_requires_login(client):
    assert (await client.get("/api/bots")).status_code == 401


async def test_lists_bots_yaml_in_order_without_urls(authed, tmp_path):
    f = tmp_path / "bots.yaml"
    f.write_text(
        "bots:\n"
        "  - {name: mock-b, mcp_url: 'http://b/mcp', admin_url: 'http://b:9100', aliases: [목비], color: '#123456'}\n"
        "  - {name: mock-a, mcp_url: 'http://a/mcp'}\n",
        encoding="utf-8",
    )
    settings = get_settings().model_copy(update={"bots_file": str(f)})
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        r = await authed.get("/api/bots")
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert r.status_code == 200
    # 어댑터 주소는 내보내지 않는다
    assert r.json() == [
        {"name": "mock-b", "aliases": ["목비"], "color": "#123456"},
        {"name": "mock-a", "aliases": [], "color": None},
    ]


async def test_missing_file_is_empty(authed, tmp_path):
    settings = get_settings().model_copy(update={"bots_file": str(tmp_path / "none.yaml")})
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        r = await authed.get("/api/bots")
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert r.status_code == 200 and r.json() == []
