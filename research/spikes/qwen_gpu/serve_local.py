"""Run the speech server on this Mac's GPU (MLX/Metal), to check the protocol.

    cd sidecar && SPEECH_TOKEN_SECRET=dev uv run --no-sync --with fastapi --with uvicorn \
        python ../research/spikes/qwen_gpu/serve_local.py [port]
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn  # noqa: E402

from auto_presenter.asr.qwen_backend import QwenStreamingBackend  # noqa: E402
from auto_presenter.follower import next_line  # noqa: E402
from speech_server import make_api  # noqa: E402

t = time.monotonic()
backend = QwenStreamingBackend(None)
backend.warm()
api = make_api(
    backend, secret=os.environ["SPEECH_TOKEN_SECRET"], gpu="local-mlx", load_s=time.monotonic() - t,
    phone_head=next_line.load_head(next_line.QWEN_MODEL_FILE),
)
uvicorn.run(api, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8790, log_level="warning")
