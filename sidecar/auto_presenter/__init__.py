"""Church Slides sidecar — AI/ASR engine."""

import os

# One thread for numpy's BLAS, set before anything imports numpy. The engine's
# matrix work is many small products (the score follower steps a few hundred
# cells several times a second), where OpenBLAS's per-core worker threads
# spin rather than help: the frozen engine, which bundles OpenBLAS, used 357%
# of a core while following against 35% with one thread (build perf run,
# Apple M1). Only numpy's BLAS: not OMP_NUM_THREADS / MKL_NUM_THREADS, which
# CTranslate2 (CPU Whisper, Intel and Windows) takes its thread count from.
# An explicit environment value still wins.
for _var in ("OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")
