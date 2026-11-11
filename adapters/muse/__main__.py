"""python -m adapters.muse — 뮤즈(Meta AI 앱) 어댑터 기동(휴대폰 경로, 범용 채팅 앱 어댑터)."""

import os
from pathlib import Path

from adapters.base import AdapterConfig, read_token
from adapters.phonechat import PhoneChatAdapter

HERE = Path(__file__).parent
config = AdapterConfig.load(os.environ.get("ADAPTER_CONFIG", HERE / "config.yaml"))
PhoneChatAdapter(
    config, state_dir=os.environ.get("ADAPTER_STATE_DIR", "/data"), token=read_token(), selectors_path=HERE / "selectors.yaml"
).serve()
