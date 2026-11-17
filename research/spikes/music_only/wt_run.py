"""Run a spike script against *this checkout's* sidecar code (not the venv's
editable install), with the shared spike data dir.

    <venv>/bin/python research/spikes/music_only/wt_run.py SCRIPT.py [args...]
"""
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
os.environ.setdefault("SPIKE_MUSIC_DIR", str(ROOT / "build" / "spike_music_only"))
sys.path.insert(0, str(ROOT / "sidecar"))
sys.path.insert(0, str(HERE))
script = HERE / sys.argv[1]
sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name="__main__")
