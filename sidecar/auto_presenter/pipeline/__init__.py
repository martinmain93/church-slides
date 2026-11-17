"""End-to-end pipelines wiring capture, ASR, and downstream broadcast.

`AudioSession` composes an `AudioCapture` + `ASRBackend` + the
`SlidingWindowWrapper` streaming layer into a single object the server
can start/stop on a client's request. Transcript events flow out to a
caller-supplied async callback (the server uses that to push them to
connected WebSocket clients).
"""
