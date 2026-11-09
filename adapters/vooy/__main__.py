"""python -m adapters.vooy — Vooy 어댑터 기동."""

import os
from pathlib import Path

from adapters.base import AdapterConfig, read_token
from adapters.vooy.adapter import VooyAdapter

config = AdapterConfig.load(os.environ.get("ADAPTER_CONFIG", Path(__file__).with_name("config.yaml")))
VooyAdapter(config, state_dir=os.environ.get("ADAPTER_STATE_DIR", "/data"), token=read_token()).serve()
