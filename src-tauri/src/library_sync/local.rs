//! The library file's side of sync: which records changed here, what to send,
//! and how to write what the server sends back.
//!
//! An organization's library file carries three small tables and a set of
//! triggers (`install`). Every write to a synced table — from the app, the
//! AI assistant's process or an import — marks its record in `sync_dirty`, in
//! the same transaction, so nothing is ever missed and no write path needs
//! to know about sync. `sync_shadow` holds each record as the server last
//! had it: what's sent is the difference between the record now and its
//! shadow. This computer's own library never gets the triggers, so it has
//! nothing to send.

use std::collections::BTreeSet;

use anyhow::{bail, Result};
use rusqlite::{params, types::ValueRef, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_shadow (
  type TEXT NOT NULL, id TEXT NOT NULL, seq INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0, fields TEXT NOT NULL,
  PRIMARY KEY (type, id));
CREATE TABLE IF NOT EXISTS sync_dirty (
  type TEXT NOT NULL, id TEXT NOT NULL, gen INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (type, id));
-- Holds a row only inside the transaction that writes the server's records,
-- so writing them doesn't mark them changed here.
CREATE TABLE IF NOT EXISTS sync_quiet (one INTEGER);
";

/// (table, record type, the column holding the record's id)
const WATCHED: &[(&str, &str, &str)] = &[
    ("songs", "song", "id"),
    ("song_charts", "song", "song_id"),
    ("song_scriptures", "song", "song_id"),
    ("song_references", "song", "song_id"),
    ("setlists", "setlist", "id"),
    ("setlist_items", "setlist", "setlist_id"),
    ("readings", "reading", "id"),
    ("services", "service", "id"),
    ("service_songs", "service", "service_id"),
    ("library_look", "look", "id"),
];

/// Record types kept in the library file.
pub const TYPES: &[&str] = &["song", "reading", "look", "setlist", "service"];

const SONG_COLS: &[&str] = &[
    "title", "body_text", "ccli", "author", "copyright", "default_tempo_wps", "slide_order", "created_at", "updated_at",
];
const CHART_COLS: &[&str] = &["chart_json", "original_key", "source_format", "updated_at"];
const SCRIPTURE_COLS: &[&str] = &[
    "id", "reference", "translation", "section_key", "anchor_lyric", "every_repeat", "position", "source", "strength", "note",
];
const REFERENCE_COLS: &[&str] = &["id", "kind", "source_name", "data_json", "summary_json", "created_at", "updated_at"];
const SETLIST_COLS: &[&str] = &["name", "created_at", "last_used_at"];
const ITEM_COLS: &[&str] = &["id", "kind", "song_id", "position", "slides_override", "content", "look", "song_key"];
const READING_COLS: &[&str] = &["title", "body_text", "created_at", "updated_at"];
const SERVICE_COLS: &[&str] = &["setlist_id", "started_at"];
const LOOK_COLS: &[&str] = &["display", "scripture", "updated_at"];
const PERFORMANCE_COLS: &[&str] = &["song_id", "started_at_ms", "provenance", "weight", "data_json", "updated_at_ms"];

/// The fields this version of the app reads and writes for a record type.
/// Fields it doesn't know (from a newer app) are left alone, never cleared.
fn known(ty: &str) -> Vec<&'static str> {
    let (cols, extra): (&[&str], &[&str]) = match ty {
        "song" => (SONG_COLS, &["chart", "scriptures", "references"]),
        "setlist" => (SETLIST_COLS, &["items", "refs"]),
        "reading" => (READING_COLS, &[]),
        "service" => (SERVICE_COLS, &["songs"]),
        "look" => (LOOK_COLS, &[]),
        _ => (&[], &[]),
    };
    cols.iter().chain(extra).copied().collect()
}

pub fn is_installed(conn: &Connection) -> bool {
    conn.query_row("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_state'", [], |_| Ok(()))
        .optional()
        .ok()
        .flatten()
        .is_some()
}

/// Give a library file its sync tables and triggers. On a file that had
/// none, everything already in it is marked to go up.
pub fn install(conn: &Connection) -> Result<()> {
    let fresh = !is_installed(conn);
    let mut sql = String::from(SCHEMA);
    for (table, ty, key) in WATCHED {
        for (op, rows) in [("insert", &["NEW"][..]), ("update", &["NEW", "OLD"][..]), ("delete", &["OLD"][..])] {
            let body: String = rows
                .iter()
                .map(|r| {
                    format!(
                        "INSERT INTO sync_dirty (type, id) VALUES ('{ty}', {r}.{key}) \
                         ON CONFLICT (type, id) DO UPDATE SET gen = gen + 1;"
                    )
                })
                .collect();
            sql += &format!(
                "CREATE TRIGGER IF NOT EXISTS sync_{table}_{op} AFTER {} ON {table} \
                 WHEN NOT EXISTS (SELECT 1 FROM sync_quiet) BEGIN {body} END;\n",
                op.to_uppercase()
            );
        }
    }
    if fresh {
        sql += "INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'song', id FROM songs;
                INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'reading', id FROM readings;
                INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'setlist', id FROM setlists;
                INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'service', id FROM services;
                INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'look', id FROM library_look;";
    }
    conn.execute_batch(&format!("BEGIN; {sql} COMMIT;"))?;
    Ok(())
}

// --- State ----------------------------------------------------------------------

fn state(conn: &Connection, key: &str) -> Result<Option<String>> {
    Ok(conn.query_row("SELECT value FROM sync_state WHERE key = ?1", [key], |r| r.get(0)).optional()?)
}

fn set_state(conn: &Connection, key: &str, value: &str) -> Result<()> {
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?1, ?2) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        params![key, value],
    )?;
    Ok(())
}

/// A stable id for this computer's copy of the library.
pub fn device(conn: &Connection) -> Result<String> {
    if let Some(d) = state(conn, "device")? {
        return Ok(d);
    }
    let d = format!("dev_{}", ulid::Ulid::new());
    set_state(conn, "device", &d)?;
    Ok(d)
}

/// The last change number pulled.
pub fn cursor(conn: &Connection) -> Result<i64> {
    Ok(state(conn, "seq")?.and_then(|s| s.parse().ok()).unwrap_or(0))
}

pub fn waiting(conn: &Connection) -> Result<i64> {
    Ok(conn.query_row("SELECT COUNT(*) FROM sync_dirty", [], |r| r.get(0))?)
}

// --- Records ----------------------------------------------------------------------

fn json_of(v: ValueRef<'_>) -> Value {
    match v {
        ValueRef::Null | ValueRef::Blob(_) => Value::Null,
        ValueRef::Integer(i) => Value::from(i),
        ValueRef::Real(f) => Value::from(f),
        ValueRef::Text(t) => Value::from(String::from_utf8_lossy(t).into_owned()),
    }
}

fn sql_of(v: Option<&Value>) -> rusqlite::types::Value {
    use rusqlite::types::Value as S;
    match v {
        None | Some(Value::Null) => S::Null,
        Some(Value::Bool(b)) => S::Integer(*b as i64),
        Some(Value::Number(n)) => n.as_i64().map(S::Integer).unwrap_or_else(|| S::Real(n.as_f64().unwrap_or(0.0))),
        Some(Value::String(s)) => S::Text(s.clone()),
        Some(other) => S::Text(other.to_string()),
    }
}

/// Rows as objects, leaving out null columns.
fn rows(conn: &Connection, table: &str, cols: &[&str], key: &str, id: &str, order: &str) -> Result<Vec<Map<String, Value>>> {
    let sql = format!("SELECT {} FROM {table} WHERE {key} = ?1 {order}", cols.join(", "));
    let mut stmt = conn.prepare_cached(&sql)?;
    let out = stmt
        .query_map([id], |r| {
            let mut m = Map::new();
            for (i, c) in cols.iter().enumerate() {
                let v = json_of(r.get_ref(i)?);
                if !v.is_null() {
                    m.insert((*c).to_string(), v);
                }
            }
            Ok(m)
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(out)
}

fn put_list(m: &mut Map<String, Value>, k: &str, items: Vec<Value>) {
    if !items.is_empty() {
        m.insert(k.into(), Value::Array(items));
    }
}

/// A record as it is in this file, or `None` when it isn't there.
pub fn read(conn: &Connection, ty: &str, id: &str) -> Result<Option<Map<String, Value>>> {
    let main = |table: &str, cols: &[&str]| -> Result<Option<Map<String, Value>>> {
        Ok(rows(conn, table, cols, "id", id, "")?.into_iter().next())
    };
    let obj = |v: Vec<Map<String, Value>>| v.into_iter().map(Value::Object).collect::<Vec<_>>();
    Ok(match ty {
        "song" => main("songs", SONG_COLS)?.map(|mut m| -> Result<_> {
            if let Some(chart) = rows(conn, "song_charts", CHART_COLS, "song_id", id, "")?.into_iter().next() {
                m.insert("chart".into(), Value::Object(chart));
            }
            put_list(&mut m, "scriptures", obj(rows(conn, "song_scriptures", SCRIPTURE_COLS, "song_id", id, "ORDER BY position, id")?));
            put_list(&mut m, "references", obj(rows(conn, "song_references", REFERENCE_COLS, "song_id", id, "ORDER BY id")?));
            Ok(m)
        }).transpose()?,
        "setlist" => main("setlists", SETLIST_COLS)?.map(|mut m| -> Result<_> {
            let items = rows(conn, "setlist_items", ITEM_COLS, "setlist_id", id, "ORDER BY position, id")?;
            let refs: BTreeSet<String> = items
                .iter()
                .filter_map(|i| i.get("song_id").and_then(Value::as_str).map(|s| format!("song:{s}")))
                .collect();
            put_list(&mut m, "items", obj(items));
            put_list(&mut m, "refs", refs.into_iter().map(Value::from).collect());
            Ok(m)
        }).transpose()?,
        "reading" => main("readings", READING_COLS)?,
        "look" => main("library_look", LOOK_COLS)?,
        "service" => main("services", SERVICE_COLS)?.map(|mut m| -> Result<_> {
            let songs = rows(conn, "service_songs", &["song_id"], "service_id", id, "ORDER BY song_id")?;
            put_list(&mut m, "songs", songs.into_iter().filter_map(|mut r| r.remove("song_id")).collect());
            Ok(m)
        }).transpose()?,
        _ => None,
    })
}

fn upsert(conn: &Connection, table: &str, id: &str, cols: &[&str], m: &Map<String, Value>) -> Result<()> {
    let marks: Vec<String> = (2..=cols.len() + 1).map(|i| format!("?{i}")).collect();
    let sets: Vec<String> = cols.iter().map(|c| format!("{c} = excluded.{c}")).collect();
    let sql = format!(
        "INSERT INTO {table} (id, {}) VALUES (?1, {}) ON CONFLICT (id) DO UPDATE SET {}",
        cols.join(", "),
        marks.join(", "),
        sets.join(", ")
    );
    let mut values = vec![rusqlite::types::Value::Text(id.to_string())];
    values.extend(cols.iter().map(|c| sql_of(m.get(*c))));
    conn.execute(&sql, rusqlite::params_from_iter(values))?;
    Ok(())
}

/// Insert a child row: only the columns it has, so defaults apply.
fn insert_child(conn: &Connection, table: &str, parent_col: &str, parent: &str, cols: &[&str], m: &Map<String, Value>) -> Result<()> {
    let present: Vec<&str> = cols.iter().copied().filter(|c| m.get(*c).is_some_and(|v| !v.is_null())).collect();
    let mut names = vec![parent_col];
    names.extend(&present);
    let marks: Vec<String> = (1..=names.len()).map(|i| format!("?{i}")).collect();
    let sql = format!("INSERT INTO {table} ({}) VALUES ({})", names.join(", "), marks.join(", "));
    let mut values = vec![rusqlite::types::Value::Text(parent.to_string())];
    values.extend(present.iter().map(|c| sql_of(m.get(*c))));
    conn.execute(&sql, rusqlite::params_from_iter(values))?;
    Ok(())
}

fn objects(m: &Map<String, Value>, k: &str) -> Vec<Map<String, Value>> {
    m.get(k)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|v| v.as_object().cloned()).collect())
        .unwrap_or_default()
}

/// Write a record into this file, replacing what's there.
pub fn write(conn: &Connection, ty: &str, id: &str, m: &Map<String, Value>) -> Result<()> {
    match ty {
        "song" => {
            upsert(conn, "songs", id, SONG_COLS, m)?;
            conn.execute("DELETE FROM song_charts WHERE song_id = ?1", [id])?;
            if let Some(chart) = m.get("chart").and_then(Value::as_object) {
                insert_child(conn, "song_charts", "song_id", id, CHART_COLS, chart)?;
            }
            conn.execute("DELETE FROM song_scriptures WHERE song_id = ?1", [id])?;
            for s in objects(m, "scriptures") {
                insert_child(conn, "song_scriptures", "song_id", id, SCRIPTURE_COLS, &s)?;
            }
            conn.execute("DELETE FROM song_references WHERE song_id = ?1", [id])?;
            for r in objects(m, "references") {
                insert_child(conn, "song_references", "song_id", id, REFERENCE_COLS, &r)?;
            }
        }
        "setlist" => {
            upsert(conn, "setlists", id, SETLIST_COLS, m)?;
            conn.execute("DELETE FROM setlist_items WHERE setlist_id = ?1", [id])?;
            for item in objects(m, "items") {
                insert_child(conn, "setlist_items", "setlist_id", id, ITEM_COLS, &item)?;
            }
        }
        "reading" => upsert(conn, "readings", id, READING_COLS, m)?,
        "look" => upsert(conn, "library_look", id, LOOK_COLS, m)?,
        "service" => {
            upsert(conn, "services", id, SERVICE_COLS, m)?;
            conn.execute("DELETE FROM service_songs WHERE service_id = ?1", [id])?;
            for s in m.get("songs").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                conn.execute("INSERT OR IGNORE INTO service_songs (service_id, song_id) VALUES (?1, ?2)", params![id, s])?;
            }
        }
        other => bail!("unknown record type {other}"),
    }
    Ok(())
}

/// Remove a record from this file. A song a service here still uses stays
/// (false): the change that uses it brings it back on the server too.
pub fn remove(conn: &Connection, ty: &str, id: &str) -> Result<bool> {
    match ty {
        "song" => {
            let used: bool = conn.query_row("SELECT EXISTS(SELECT 1 FROM setlist_items WHERE song_id = ?1)", [id], |r| r.get(0))?;
            if used {
                return Ok(false);
            }
            conn.execute("DELETE FROM songs WHERE id = ?1", [id])?;
        }
        "setlist" => {
            conn.execute("DELETE FROM setlists WHERE id = ?1", [id])?;
        }
        "reading" => {
            conn.execute("DELETE FROM readings WHERE id = ?1", [id])?;
        }
        // History is never deleted.
        _ => {}
    }
    Ok(true)
}

/// The fields of `now` that differ from `before`, with `null` for a field
/// that's gone. Only fields this app knows are compared.
fn diff(ty: &str, now: &Map<String, Value>, before: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    for k in known(ty) {
        let (a, b) = (now.get(k), before.get(k));
        if a != b {
            out.insert(k.to_string(), a.cloned().unwrap_or(Value::Null));
        }
    }
    out
}

fn merged(base: &Map<String, Value>, set: &Map<String, Value>) -> Map<String, Value> {
    let mut m = base.clone();
    for (k, v) in set {
        if v.is_null() {
            m.remove(k);
        } else {
            m.insert(k.clone(), v.clone());
        }
    }
    m
}

struct Shadow {
    seq: i64,
    deleted: bool,
    fields: Map<String, Value>,
}

fn shadow(conn: &Connection, ty: &str, id: &str) -> Result<Option<Shadow>> {
    conn.query_row(
        "SELECT seq, deleted, fields FROM sync_shadow WHERE type = ?1 AND id = ?2",
        [ty, id],
        |r| Ok((r.get::<_, i64>(0)?, r.get::<_, bool>(1)?, r.get::<_, String>(2)?)),
    )
    .optional()?
    .map(|(seq, deleted, f)| Ok(Shadow { seq, deleted, fields: serde_json::from_str(&f)? }))
    .transpose()
}

fn set_shadow(conn: &Connection, ty: &str, id: &str, seq: i64, deleted: bool, fields: &Map<String, Value>) -> Result<()> {
    conn.execute(
        "INSERT INTO sync_shadow (type, id, seq, deleted, fields) VALUES (?1, ?2, ?3, ?4, ?5) \
         ON CONFLICT (type, id) DO UPDATE SET seq = excluded.seq, deleted = excluded.deleted, fields = excluded.fields",
        params![ty, id, seq, deleted, serde_json::to_string(fields)?],
    )?;
    Ok(())
}

// --- Push ----------------------------------------------------------------------

/// One local change on its way up.
#[derive(Debug, Clone)]
pub struct Pending {
    pub op: String,
    pub ty: String,
    pub id: String,
    gen: i64,
    pub base: i64,
    /// `None` is a delete.
    pub set: Option<Map<String, Value>>,
}

impl Pending {
    pub fn to_json(&self) -> Value {
        let mut c = serde_json::json!({ "op": self.op, "type": self.ty, "id": self.id, "base": self.base });
        match &self.set {
            Some(set) => c["set"] = Value::Object(set.clone()),
            None => c["delete"] = Value::Bool(true),
        }
        c
    }
}

const MAX_CHANGES: usize = 500;
const MAX_PUSH_BYTES: usize = 3_500_000;

/// What to send next, oldest-safe first: songs and readings before the
/// services that use them, deletes after the services that stopped using
/// them. Records whose change came to nothing leave the list here.
pub fn prepare_push(conn: &Connection) -> Result<Vec<Pending>> {
    let dirty: Vec<(String, String, i64)> = conn
        .prepare("SELECT type, id, gen FROM sync_dirty")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let mut out: Vec<(u8, Pending)> = Vec::new();
    for (ty, id, gen) in dirty {
        if !TYPES.contains(&ty.as_str()) {
            conn.execute("DELETE FROM sync_dirty WHERE type = ?1 AND id = ?2 AND gen = ?3", params![ty, id, gen])?;
            continue;
        }
        let local = read(conn, &ty, &id)?;
        let sh = shadow(conn, &ty, &id)?;
        let alive = sh.as_ref().filter(|s| !s.deleted);
        let base = sh.as_ref().map(|s| s.seq).unwrap_or(0);
        let (rank, set) = match (&local, alive) {
            (None, None) => (0, None::<Option<Map<String, Value>>>),
            (None, Some(_)) => (2, Some(None)),
            (Some(now), _) => {
                let empty = Map::new();
                let before = alive.map(|s| &s.fields).unwrap_or(&empty);
                let d = diff(&ty, now, before);
                if d.is_empty() && alive.is_some() {
                    (0, None)
                } else {
                    (if ty == "setlist" { 1 } else if ty == "service" { 3 } else { 0 }, Some(Some(if alive.is_some() { d } else { now.clone() })))
                }
            }
        };
        match set {
            None => {
                conn.execute("DELETE FROM sync_dirty WHERE type = ?1 AND id = ?2 AND gen = ?3", params![ty, id, gen])?;
            }
            Some(set) => out.push((rank, Pending { op: format!("op_{}", ulid::Ulid::new()), ty, id, gen, base, set })),
        }
    }
    out.sort_by_key(|(rank, _)| *rank);
    let mut batch = Vec::new();
    let mut bytes = 0;
    for (_, p) in out {
        let size = p.to_json().to_string().len();
        if batch.len() >= MAX_CHANGES || (!batch.is_empty() && bytes + size > MAX_PUSH_BYTES) {
            break;
        }
        bytes += size;
        batch.push(p);
    }
    Ok(batch)
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PushResult {
    pub op: String,
    pub status: String,
    #[serde(default)]
    pub seq: Option<i64>,
    #[serde(default)]
    pub conflict: Option<bool>,
    #[serde(default)]
    pub code: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
}

/// What the push changed that the person should hear about.
#[derive(Debug, Default)]
pub struct PushOutcome {
    pub refused: Vec<(String, String)>,
    pub conflicts: usize,
}

fn quietly<T>(conn: &Connection, f: impl FnOnce() -> Result<T>) -> Result<T> {
    conn.execute_batch("BEGIN IMMEDIATE; PRAGMA defer_foreign_keys = ON; INSERT INTO sync_quiet VALUES (1);")?;
    let result = f().and_then(|v| {
        // A service whose song the server never had can't stay pointing at
        // nothing: the item goes rather than the sync sticking.
        conn.execute_batch(
            "DELETE FROM setlist_items WHERE song_id IS NOT NULL AND song_id NOT IN (SELECT id FROM songs);
             DELETE FROM sync_quiet;",
        )?;
        Ok(v)
    });
    match result {
        Ok(v) => {
            conn.execute_batch("COMMIT")?;
            Ok(v)
        }
        Err(e) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(e)
        }
    }
}

/// Record the server's answers. An applied change becomes the shadow; a
/// refused one is undone here, keeping the server's version.
pub fn apply_push(conn: &Connection, sent: &[Pending], results: &[PushResult]) -> Result<PushOutcome> {
    quietly(conn, || {
        let mut outcome = PushOutcome::default();
        for p in sent {
            let Some(r) = results.iter().find(|r| r.op == p.op) else { continue };
            if p.ty == "performance" {
                // Sent (or refused for good): either way, not sent again.
                set_shadow(conn, &p.ty, &p.id, r.seq.unwrap_or(0), false, &Map::new())?;
                if r.status != "applied" {
                    log::info!("learned timing {} not kept by the server: {:?}", p.id, r.code);
                }
                continue;
            }
            let sh = shadow(conn, &p.ty, &p.id)?;
            let still_dirty = conn
                .execute("DELETE FROM sync_dirty WHERE type = ?1 AND id = ?2 AND gen = ?3", params![p.ty, p.id, p.gen])?
                == 0;
            if r.status == "applied" {
                let seq = r.seq.unwrap_or(0);
                match &p.set {
                    None if seq == 0 => {
                        conn.execute("DELETE FROM sync_shadow WHERE type = ?1 AND id = ?2", params![p.ty, p.id])?;
                    }
                    None => {
                        let fields = sh.map(|s| s.fields).unwrap_or_default();
                        set_shadow(conn, &p.ty, &p.id, seq, true, &fields)?;
                    }
                    Some(set) => {
                        let base = sh.filter(|s| !s.deleted).map(|s| s.fields).unwrap_or_default();
                        set_shadow(conn, &p.ty, &p.id, seq, false, &merged(&base, set))?;
                    }
                }
                if r.conflict == Some(true) {
                    outcome.conflicts += 1;
                }
            } else {
                outcome.refused.push((
                    r.code.clone().unwrap_or_default(),
                    r.message.clone().unwrap_or_else(|| "The server didn't take a change.".into()),
                ));
                // Edited again since it was sent: that newer edit goes up next.
                if still_dirty {
                    continue;
                }
                match sh {
                    Some(s) if !s.deleted => write(conn, &p.ty, &p.id, &s.fields)?,
                    _ => {
                        remove(conn, &p.ty, &p.id)?;
                    }
                }
            }
        }
        Ok(outcome)
    })
}

// --- Pull ----------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct Pulled {
    #[serde(rename = "type")]
    pub ty: String,
    pub id: String,
    pub seq: i64,
    #[serde(default)]
    pub deleted: bool,
    #[serde(default)]
    pub fields: Map<String, Value>,
}

/// Write the server's records, keeping this computer's unsent changes on top
/// of them. Returns how many records changed here.
pub fn apply_pull(conn: &Connection, records: &[Pulled], seq: i64) -> Result<usize> {
    quietly(conn, || {
        let mut changed = 0;
        let learning = learning_attached(conn);
        for rec in records {
            if rec.ty == "performance" {
                if learning && !rec.deleted && valid_id(&rec.id) {
                    write_performance(conn, &rec.id, &rec.fields)?;
                    set_shadow(conn, &rec.ty, &rec.id, rec.seq, false, &Map::new())?;
                }
                continue;
            }
            if !TYPES.contains(&rec.ty.as_str()) {
                continue;
            }
            let dirty: bool = conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM sync_dirty WHERE type = ?1 AND id = ?2)",
                [&rec.ty, &rec.id],
                |r| r.get(0),
            )?;
            // This computer's unsent change: `None` = not dirty,
            // `Some(None)` = deleted here, `Some(Some(fields))` = changed here.
            let pending = if dirty {
                let before = shadow(conn, &rec.ty, &rec.id)?.filter(|s| !s.deleted).map(|s| s.fields);
                Some(read(conn, &rec.ty, &rec.id)?.map(|now| match &before {
                    Some(b) => diff(&rec.ty, &now, b),
                    None => now,
                }))
            } else {
                None
            };
            set_shadow(conn, &rec.ty, &rec.id, rec.seq, rec.deleted, &rec.fields)?;
            match (rec.deleted, pending) {
                // Words changed here on a song deleted there: they go up, and
                // the server keeps them as a revision before saying no.
                (true, Some(Some(_))) => {}
                (true, Some(None)) => {
                    conn.execute("DELETE FROM sync_dirty WHERE type = ?1 AND id = ?2", [&rec.ty, &rec.id])?;
                }
                (true, None) => {
                    if read(conn, &rec.ty, &rec.id)?.is_some() {
                        remove(conn, &rec.ty, &rec.id)?;
                        changed += 1;
                    }
                }
                // Deleted here, not yet sent: stays deleted.
                (false, Some(None)) => {}
                (false, Some(Some(mine))) => {
                    write(conn, &rec.ty, &rec.id, &merged(&rec.fields, &mine))?;
                    changed += 1;
                }
                (false, None) => {
                    if read(conn, &rec.ty, &rec.id)?.as_ref() != Some(&rec.fields) {
                        write(conn, &rec.ty, &rec.id, &rec.fields)?;
                        changed += 1;
                    }
                }
            }
        }
        set_state(conn, "seq", &seq.to_string())?;
        Ok(changed)
    })
}

/// The server's library was reset (or this file came from elsewhere): send
/// everything here again and pull from the start.
pub fn reset(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "BEGIN;
         DELETE FROM sync_shadow;
         INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'song', id FROM songs;
         INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'reading', id FROM readings;
         INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'setlist', id FROM setlists;
         INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'service', id FROM services;
         INSERT OR IGNORE INTO sync_dirty (type, id) SELECT 'look', id FROM library_look;
         DELETE FROM sync_state WHERE key = 'seq';
         COMMIT;",
    )?;
    Ok(())
}

// --- Learned timing ------------------------------------------------------------
//
// Each performance the engine learns from is a row in `learning.db`, beside
// the library file (the engine owns that file). Once a row is final it never
// changes, so sync only sends new final rows and writes other computers'
// rows in: nothing to merge. Rows sent or received are remembered in
// `sync_shadow` (type `performance`, no fields kept).

/// The engine's schema for `learning.db` (sidecar/auto_presenter/learning/store.py).
const LEARNING_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS learning.performances (
  id            TEXT PRIMARY KEY,
  song_id       TEXT NOT NULL,
  started_at_ms INTEGER NOT NULL,
  provenance    TEXT NOT NULL,
  weight        REAL NOT NULL,
  final         INTEGER NOT NULL,
  data_json     TEXT NOT NULL,
  updated_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS learning.performances_song ON performances(song_id, started_at_ms);
";

/// The engine's learning file for a library file.
pub fn learning_path(library: &std::path::Path) -> std::path::PathBuf {
    library.with_file_name("learning.db")
}

/// Run `f` with the library's `learning.db` attached as `learning`.
pub fn with_learning<T>(conn: &Connection, path: &std::path::Path, f: impl FnOnce() -> Result<T>) -> Result<T> {
    conn.execute("ATTACH DATABASE ?1 AS learning", [path.to_string_lossy()])?;
    let result = conn.execute_batch(LEARNING_SCHEMA).map_err(anyhow::Error::from).and_then(|_| f());
    let _ = conn.execute_batch("DETACH DATABASE learning");
    result
}

fn learning_attached(conn: &Connection) -> bool {
    conn.query_row("SELECT 1 FROM pragma_database_list WHERE name = 'learning'", [], |_| Ok(()))
        .optional()
        .ok()
        .flatten()
        .is_some()
}

fn valid_id(id: &str) -> bool {
    (1..=100).contains(&id.len()) && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Final performances learned here that haven't gone up (with `learning` attached).
pub fn prepare_performances(conn: &Connection, limit: usize) -> Result<Vec<Pending>> {
    if !learning_attached(conn) {
        return Ok(vec![]);
    }
    let sql = format!(
        "SELECT id, {} FROM learning.performances p WHERE final = 1 \
         AND NOT EXISTS (SELECT 1 FROM sync_shadow s WHERE s.type = 'performance' AND s.id = p.id) \
         ORDER BY started_at_ms LIMIT ?1",
        PERFORMANCE_COLS.join(", ")
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt
        .query_map([limit as i64], |r| {
            let id: String = r.get(0)?;
            let mut m = Map::new();
            for (i, c) in PERFORMANCE_COLS.iter().enumerate() {
                m.insert((*c).to_string(), json_of(r.get_ref(i + 1)?));
            }
            Ok((id, m))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(rows
        .into_iter()
        .filter(|(id, _)| valid_id(id))
        .map(|(id, set)| Pending { op: format!("op_{}", ulid::Ulid::new()), ty: "performance".into(), id, gen: 0, base: 0, set: Some(set) })
        .collect())
}

/// Write another computer's performance into `learning.db`.
fn write_performance(conn: &Connection, id: &str, m: &Map<String, Value>) -> Result<()> {
    let mut values = vec![rusqlite::types::Value::Text(id.to_string())];
    values.extend(PERFORMANCE_COLS.iter().map(|c| sql_of(m.get(*c))));
    conn.execute(
        &format!(
            "INSERT INTO learning.performances (id, {}, final) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 1) ON CONFLICT (id) DO NOTHING",
            PERFORMANCE_COLS.join(", ")
        ),
        rusqlite::params_from_iter(values),
    )?;
    Ok(())
}
