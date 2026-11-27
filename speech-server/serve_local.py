"""Run the speech server on this Mac's GPU (MLX on Metal), to check the
protocol end to end without Modal.

    cd sidecar && SPEECH_TOKEN_SECRET=dev uv run --no-sync --with fastapi --with uvicorn \
        python ../speech-server/serve_local.py [port]

Then point the app's engine at ws://127.0.0.1:<port> (default 8790) with a
token from `tokens.py`.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from server import make_api

from auto_presenter.asr.qwen_backend import QwenStreamingBackend
from auto_presenter.follower import next_line

t = time.monotonic()
backend = QwenStreamingBackend(None)
backend.warm()
api = make_api(
    backend,
    secret=os.environ["SPEECH_TOKEN_SECRET"],
    gpu="local-mlx",
    load_s=time.monotonic() - t,
    phone_head=next_line.load_head(next_line.QWEN_MODEL_FILE),
)
uvicorn.run(
    api, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8790, log_level="warning"
)
