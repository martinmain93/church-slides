-- The key a song is played in for one service ("Bb", "Em"), or NULL for the
-- chart's own key. Set from the key badge on the item in the setlist or the
-- operator view, by the worship leader's transpose, or by an assistant. The
-- band screens transpose the chart from its original key to this one.
ALTER TABLE setlist_items ADD COLUMN song_key TEXT;
