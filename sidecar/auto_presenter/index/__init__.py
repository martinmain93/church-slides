"""Setlist-scoped indexing for the matcher, plus a library-wide fingerprint
index for songs that were never in tonight's set.

The UI still pushes the resolved setlist with `StartAudioSessionRequest`.
Off-setlist recovery additionally reads `library.db` (read-only) to build
`index.fingerprint.FingerprintIndex`.
"""
