"""python -m adapters.mock — Mock 어댑터 기동."""

import os
from pathlib import Path

from adapters.base import AdapterConfig, read_token
from adapters.mock.adapter import MockAdapter

config = AdapterConfig.load(os.environ.get("ADAPTER_CONFIG", Path(__file__).with_name("config.yaml")))
MockAdapter(config, state_dir=os.environ.get("ADAPTER_STATE_DIR", "/data"), token=read_token()).serve()
