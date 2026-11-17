-- Add a per-song default slide order (Feature 1).
--
-- A compact section-reference spec, e.g. "V1, V2, C, V3, C, V4, C, B, C",
-- defining the default playback sequence. NULL/empty means "each section
-- once, in document order" — the legacy behaviour — so existing rows need
-- no backfill.

ALTER TABLE songs ADD COLUMN slide_order TEXT;
