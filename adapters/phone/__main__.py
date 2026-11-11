"""python -m adapters.phone — 폰 게이트웨이 기동(기본 포트 9200)."""

import logging
import os

import uvicorn

from adapters.base import read_token
from adapters.phone.gateway import Gateway, read_device_token, watch_pkgs_from_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
gw = Gateway(read_token(), read_device_token(), watch_pkgs_from_env())
uvicorn.run(gw.app(), host="0.0.0.0", port=int(os.environ.get("PHONE_GATEWAY_PORT", "9200")), ws="websockets-sansio")
