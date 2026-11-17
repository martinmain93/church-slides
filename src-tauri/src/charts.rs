//! Chord charts (table `song_charts`, migration 006).
//!
//! The chart itself is opaque JSON here — its shape belongs to the frontend
//! (`src/lib/chords/chart.ts`), which parses, aligns and transposes it. This
//! layer only stores it, and creates the library song an imported chart
//! arrives with in the same transaction, so an import never leaves a song
//! without the chords it was imported for.

use std::collections::HashMap;

use anyhow::Result;
use rusqlite::params;

use crate::db::{Db, NewSong};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ChartRow {
    pub song_id: String,
    pub chart_json: String,
    pub original_key: Option<String>,
    pub source_format: Option<String>,
    pub updated_at: i64,
}

/// One song from an import. Either attaches a chart to a song already in the
/// library (`existing_song_id`) or creates the song (`song`) with it.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct ChartImportItem {
    pub existing_song_id: Option<String>,
    pub song: Option<NewSong>,
    pub chart_json: String,
    pub original_key: Option<String>,
    pub source_format: Option<String>,
    /// With `existing_song_id`: licensing facts from the imported file, filled
    /// in only where the library song has none (never overwritten).
    #[serde(default)]
    pub fill_metadata: Option<FillMetadata>,
    /// With `existing_song_id`: the song's own lyrics with its sections named
    /// from the chart. Applied only while the body is still `previous_body`.
    #[serde(default)]
    pub relabel: Option<Relabel>,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct Relabel {
    pub body_text: String,
    pub slide_order: Option<String>,
    pub previous_body: String,
}

#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct FillMetadata {
    pub ccli: Option<String>,
    pub author: Option<String>,
    pub copyright: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ChartImportSummary {
    pub songs_created: usize,
    pub charts_attached: usize,
    /// The song id each item ended up on, in input order.
    pub song_ids: Vec<String>,
}

fn unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

const UPSERT: &str = "INSERT INTO song_charts (song_id, chart_json, original_key, source_format, updated_at) \
     VALUES (?1, ?2, ?3, ?4, ?5) \
     ON CONFLICT(song_id) DO UPDATE SET chart_json = excluded.chart_json, \
        original_key = excluded.original_key, source_format = excluded.source_format, \
        updated_at = excluded.updated_at";

pub fn get_charts(db: &Db, song_ids: &[String]) -> Result<HashMap<String, ChartRow>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT song_id, chart_json, original_key, source_format, updated_at \
         FROM song_charts WHERE song_id = ?1",
    )?;
    let mut out = HashMap::new();
    for id in song_ids {
        let mut rows = stmt.query(params![id])?;
        if let Some(row) = rows.next()? {
            out.insert(
                id.clone(),
                ChartRow {
                    song_id: row.get(0)?,
                    chart_json: row.get(1)?,
                    original_key: row.get(2)?,
                    source_format: row.get(3)?,
                    updated_at: row.get(4)?,
                },
            );
        }
    }
    Ok(out)
}

/// Song id → written key, for every song that has a chart. Lets the library
/// badge songs with chords without loading the charts.
pub fn list_chart_keys(db: &Db) -> Result<HashMap<String, Option<String>>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare("SELECT song_id, original_key FROM song_charts")?;
    let rows = stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?)))?
        .collect::<Result<HashMap<_, _>, _>>()?;
    Ok(rows)
}

/// A chart's musical facts, for library search and the library's badges:
/// key, tempo (beats per minute) and time signature as written in the chart,
/// and whether it places any chord at all (a chart can hold just a key or a
/// tempo for a song nobody has charted yet).
#[derive(Debug, Clone, serde::Serialize, PartialEq)]
pub struct ChartMeta {
    pub key: Option<String>,
    pub tempo: Option<f64>,
    pub time: Option<String>,
    pub has_chords: bool,
}

/// Song id -> key, tempo and time signature, for every song with a chart.
pub fn list_chart_meta(db: &Db) -> Result<HashMap<String, ChartMeta>> {
    let conn = db.conn().lock().unwrap();
    let mut stmt = conn.prepare(
        "SELECT song_id, original_key, \
                CAST(json_extract(chart_json, '$.tempo') AS REAL), \
                json_extract(chart_json, '$.time'), \
                chart_json LIKE '%\"chord\":%' \
         FROM song_charts",
    )?;
    let rows = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                ChartMeta {
                    key: row.get(1)?,
                    tempo: row.get(2)?,
                    time: row.get(3)?,
                    has_chords: row.get(4)?,
                },
            ))
        })?
        .collect::<Result<HashMap<_, _>, _>>()?;
    Ok(rows)
}

/// Record the key a chart is written in, when the source didn't say (a sheet
/// saved as plain "I will rejoice.pdf"). It's what every per-service key
/// transposes from, so only the chart's own key changes: no chord moves.
/// False if the song has no chart.
pub fn set_chart_key(db: &Db, song_id: &str, key: Option<&str>) -> Result<bool> {
    let conn = db.conn().lock().unwrap();
    let n = conn.execute(
        "UPDATE song_charts SET original_key = ?2, \
            chart_json = json_set(chart_json, '$.key', ?2), updated_at = ?3 \
         WHERE song_id = ?1",
        params![song_id, key, unix_ms()],
    )?;
    Ok(n > 0)
}

pub fn delete_chart(db: &Db, song_id: &str) -> Result<()> {
    let conn = db.conn().lock().unwrap();
    conn.execute("DELETE FROM song_charts WHERE song_id = ?1", params![song_id])?;
    Ok(())
}

pub fn import_charts(db: &Db, items: Vec<ChartImportItem>) -> Result<ChartImportSummary> {
    let mut conn = db.conn().lock().unwrap();
    let tx = conn.transaction()?;
    let now = unix_ms();
    let mut summary = ChartImportSummary { songs_created: 0, charts_attached: 0, song_ids: Vec::new() };

    for item in items {
        let song_id = match (item.existing_song_id, item.song) {
            (Some(id), _) => {
                let exists: i64 =
                    tx.query_row("SELECT COUNT(*) FROM songs WHERE id = ?1", params![id], |r| r.get(0))?;
                if exists == 0 {
                    anyhow::bail!("no song with id {id}");
                }
                if let Some(fill) = &item.fill_metadata {
                    tx.execute(
                        "UPDATE songs SET \
                            ccli = COALESCE(NULLIF(TRIM(ccli), ''), ?2), \
                            author = COALESCE(NULLIF(TRIM(author), ''), ?3), \
                            copyright = COALESCE(NULLIF(TRIM(copyright), ''), ?4) \
                         WHERE id = ?1",
                        params![id, fill.ccli, fill.author, fill.copyright],
                    )?;
                }
                if let Some(r) = &item.relabel {
                    tx.execute(
                        "UPDATE songs SET body_text = ?2, slide_order = ?3, updated_at = ?5 \
                         WHERE id = ?1 AND body_text = ?4",
                        params![id, r.body_text, r.slide_order, r.previous_body, now],
                    )?;
                }
                summary.charts_attached += 1;
                id
            }
            (None, Some(song)) => {
                let id = format!("song_{}", ulid::Ulid::new().to_string().to_lowercase());
                tx.execute(
                    "INSERT INTO songs (id, title, body_text, ccli, author, copyright, \
                                        default_tempo_wps, slide_order, created_at, updated_at) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?9)",
                    params![
                        id,
                        song.title,
                        song.body_text,
                        song.ccli,
                        song.author,
                        song.copyright,
                        song.default_tempo_wps,
                        song.slide_order,
                        now,
                    ],
                )?;
                summary.songs_created += 1;
                id
            }
            (None, None) => anyhow::bail!("import item has neither a song nor an existing song id"),
        };
        tx.execute(
            UPSERT,
            params![song_id, item.chart_json, item.original_key, item.source_format, now],
        )?;
        summary.song_ids.push(song_id);
    }
    tx.commit()?;
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("t.db")).unwrap();
        (dir, db)
    }

    fn new_song(title: &str) -> NewSong {
        NewSong {
            title: title.into(),
            body_text: "[verse 1]\nhello".into(),
            ccli: None,
            author: None,
            copyright: None,
            default_tempo_wps: None,
            slide_order: None,
        }
    }

    #[test]
    fn chart_meta_reads_key_tempo_and_time_from_the_chart() {
        let (_d, db) = fresh();
        let summary = import_charts(
            &db,
            vec![ChartImportItem {
                existing_song_id: None,
                song: Some(new_song("Waltz")),
                chart_json: "{\"key\":\"D\",\"tempo\":72,\"time\":\"6/8\",\"sections\":[]}".into(),
                original_key: Some("D".into()),
                source_format: None,
                fill_metadata: None,
                relabel: None,
            }],
        )
        .unwrap();
        let meta = list_chart_meta(&db).unwrap();
        assert_eq!(
            meta[&summary.song_ids[0]],
            ChartMeta {
                key: Some("D".into()),
                tempo: Some(72.0),
                time: Some("6/8".into()),
                has_chords: false,
            }
        );
    }

    #[test]
    fn import_creates_songs_and_attaches_charts() {
        let (_d, db) = fresh();
        let existing = "song_amazing_grace".to_string();
        let summary = import_charts(
            &db,
            vec![
                ChartImportItem {
                    existing_song_id: None,
                    song: Some(new_song("New")),
                    chart_json: "{\"a\":1}".into(),
                    original_key: Some("G".into()),
                    source_format: Some("chordpro".into()),
                    fill_metadata: None,
                    relabel: None,
                },
                ChartImportItem {
                    existing_song_id: Some(existing.clone()),
                    song: None,
                    chart_json: "{\"b\":2}".into(),
                    original_key: None,
                    source_format: None,
                    fill_metadata: Some(FillMetadata {
                        ccli: Some("22025".into()),
                        author: Some("John Newton".into()),
                        copyright: None,
                    }),
                    relabel: None,
                },
            ],
        )
        .unwrap();
        assert_eq!(summary.songs_created, 1);
        assert_eq!(summary.charts_attached, 1);
        let charts = get_charts(&db, &summary.song_ids).unwrap();
        assert_eq!(charts.len(), 2);
        assert_eq!(charts[&existing].chart_json, "{\"b\":2}");
        assert_eq!(list_chart_keys(&db).unwrap()[&summary.song_ids[0]], Some("G".into()));
        // The seed song had no CCLI number: filled in. It had an author: kept.
        let seed = db.get_song(&existing).unwrap().unwrap();
        assert_eq!(seed.ccli.as_deref(), Some("22025"));
        assert_eq!(seed.author.as_deref(), Some("John Newton"));
    }

    #[test]
    fn deleting_a_song_deletes_its_chart() {
        let (_d, db) = fresh();
        let summary = import_charts(
            &db,
            vec![ChartImportItem {
                existing_song_id: None,
                song: Some(new_song("Gone")),
                chart_json: "{}".into(),
                original_key: None,
                source_format: None,
                fill_metadata: None,
                relabel: None,
            }],
        )
        .unwrap();
        db.delete_song(&summary.song_ids[0]).unwrap();
        assert!(get_charts(&db, &summary.song_ids).unwrap().is_empty());
    }

    #[test]
    fn a_failed_item_rolls_back_the_whole_import() {
        let (_d, db) = fresh();
        let before = db.list_songs().unwrap().len();
        let err = import_charts(
            &db,
            vec![
                ChartImportItem {
                    existing_song_id: None,
                    song: Some(new_song("Should vanish")),
                    chart_json: "{}".into(),
                    original_key: None,
                    source_format: None,
                    fill_metadata: None,
                    relabel: None,
                },
                ChartImportItem {
                    existing_song_id: Some("song_missing".into()),
                    song: None,
                    chart_json: "{}".into(),
                    original_key: None,
                    source_format: None,
                    fill_metadata: None,
                    relabel: None,
                },
            ],
        );
        assert!(err.is_err());
        assert_eq!(db.list_songs().unwrap().len(), before);
    }

    #[test]
    fn relabel_applies_only_while_the_song_is_unchanged() {
        let (_d, db) = fresh();
        let id = db.create_song(new_song("Song")).unwrap();
        let body = db.get_song(&id).unwrap().unwrap().body_text;
        let item = |previous: &str| ChartImportItem {
            existing_song_id: Some(id.clone()),
            song: None,
            chart_json: "{}".into(),
            original_key: None,
            source_format: None,
            fill_metadata: None,
            relabel: Some(Relabel {
                body_text: "[Chorus]\nnew".into(),
                slide_order: Some("C, C".into()),
                previous_body: previous.into(),
            }),
        };
        import_charts(&db, vec![item("edited meanwhile")]).unwrap();
        assert_eq!(db.get_song(&id).unwrap().unwrap().body_text, body);
        import_charts(&db, vec![item(&body)]).unwrap();
        let song = db.get_song(&id).unwrap().unwrap();
        assert_eq!(song.body_text, "[Chorus]\nnew");
        assert_eq!(song.slide_order.as_deref(), Some("C, C"));
    }

}
