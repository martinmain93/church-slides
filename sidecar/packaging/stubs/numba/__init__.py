"""Stand-in for numba in the frozen Apple-silicon engine.

mlx-whisper uses numba only to compile two small functions for word timings
(`@numba.jit` on its DTW alignment). The real numba brings LLVM along:
123 MB of the engine for about 15 ms a speech pass. Here `jit` returns the
function unchanged, so the same code runs as plain Python with identical
results. sidecar.spec puts this directory first on the bundle's import path.
"""


def jit(*args, **kwargs):
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]
    return lambda fn: fn


njit = jit
prange = range
