"""python -m adapters.phone — 폰 게이트웨이 기동(기본 포트 9200)."""

import logging
import os

import uvicorn

from adapters.base import read_token
from adapters.phone.gateway import (
    Gateway,
    apk_dir_from_env,
    read_device_token,
    screen_off_after_from_env,
    state_dir_from_env,
    watch_pkgs_from_env,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
gw = Gateway(
    read_token(),
    read_device_token(),
    watch_pkgs_from_env(),
    apk_dir_from_env(),
    screen_off_after_from_env(),
    state_dir_from_env(),
)
uvicorn.run(gw.app(), host="0.0.0.0", port=int(os.environ.get("PHONE_GATEWAY_PORT", "9200")), ws="websockets-sansio")
