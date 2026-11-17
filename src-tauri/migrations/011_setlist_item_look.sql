-- A song's or passage's own style in one setlist: the display look
-- (`src/lib/displayLook.ts`: theme, font, size, weight) as JSON, or NULL for
-- the default style. Set from the palette button on the item in the setlist.
ALTER TABLE setlist_items ADD COLUMN look TEXT;
