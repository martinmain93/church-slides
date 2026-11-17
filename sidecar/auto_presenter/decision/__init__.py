"""Decision layer — bridges the matcher to the WS protocol.

The matcher emits `MatcherDecision` values; the director translates
those into broadcasts and manages the `idle / tracking / following_human`
status that the UI's AI pill renders.
"""
