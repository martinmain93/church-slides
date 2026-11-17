-- Per-setlist slide edits (Feature 2).
--
-- When the operator edits a song's slides *within a setlist* (edit text,
-- duplicate, reorder, add), the edited slide list is stored here as a JSON
-- array — WITHOUT touching the library song. NULL means "use the library
-- song's body + slide_order" (the default). The shape is a flat ordered list:
--   [{ "section": { "type": "chorus", "number": 1 }, "lyrics": "…" }, …]

ALTER TABLE setlist_items ADD COLUMN slides_override TEXT;
