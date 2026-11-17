# Replaces pyinstaller-hooks-contrib's hook-av.py, which collects every PyAV
# submodule (and, on Windows, the av.libs FFmpeg DLLs). The frozen engine
# carries stubs/av instead; see its docstring for why.
hiddenimports: list[str] = []
