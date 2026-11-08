"""python -m runner → 러너 HTTP 서비스 기동."""

import logging
import os

import uvicorn

from runner.app import build_app

if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("RUNNER_LOG_LEVEL", "INFO"))
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("RUNNER_PORT", "8700")))
