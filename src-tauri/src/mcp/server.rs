//! MCP over stdio: newline-delimited JSON-RPC 2.0, the transport every
//! desktop client (Claude Desktop, Cowork, Cursor, Claude Code) speaks for a
//! local server. Only the tools capability — no resources or prompts; the
//! guidance a model needs rides in `instructions` and the tool descriptions.

use std::io::{BufRead, Write};

use serde_json::{json, Value};

use super::tools::{self, Library};

/// Newest first. We answer with the client's version when we know it, else
/// our newest — the tools surface is identical across all three.
const PROTOCOL_VERSIONS: &[&str] = &["2025-06-18", "2025-03-26", "2024-11-05"];

/// Read requests from `input` until EOF, writing responses to `output`.
pub fn serve(lib: &Library, input: impl BufRead, mut output: impl Write) -> std::io::Result<()> {
    for line in input.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let reply = match serde_json::from_str::<Value>(&line) {
            Ok(msg) => handle(lib, &msg),
            Err(e) => Some(error(Value::Null, -32700, &format!("parse error: {e}"))),
        };
        if let Some(reply) = reply {
            writeln!(output, "{reply}")?;
            output.flush()?;
        }
    }
    Ok(())
}

/// One message in, at most one message out (notifications get none).
pub fn handle(lib: &Library, msg: &Value) -> Option<Value> {
    let id = msg.get("id").cloned();
    let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or(Value::Null);

    // Notifications (`initialized`, `cancelled`, …) and stray responses.
    let id = id?;

    let result = match method {
        "initialize" => {
            let asked = params.get("protocolVersion").and_then(Value::as_str);
            let version = asked
                .filter(|v| PROTOCOL_VERSIONS.contains(v))
                .unwrap_or(PROTOCOL_VERSIONS[0]);
            json!({
                "protocolVersion": version,
                "capabilities": { "tools": { "listChanged": false } },
                "serverInfo": {
                    "name": "church-slides",
                    "title": "Church Slides",
                    "version": env!("CARGO_PKG_VERSION"),
                },
                "instructions": INSTRUCTIONS,
            })
        }
        "ping" => json!({}),
        "tools/list" => json!({ "tools": tools::specs() }),
        "tools/call" => {
            let name = params.get("name").and_then(Value::as_str).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            match lib.call(name, &args) {
                Ok(v) => json!({
                    "content": [{ "type": "text", "text": pretty(&v) }],
                    "structuredContent": v,
                    "isError": false,
                }),
                Err(e) => json!({
                    "content": [{ "type": "text", "text": format!("{e:#}") }],
                    "isError": true,
                }),
            }
        }
        other => return Some(error(id, -32601, &format!("method not found: {other}"))),
    };
    Some(json!({ "jsonrpc": "2.0", "id": id, "result": result }))
}

fn error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

fn pretty(v: &Value) -> String {
    serde_json::to_string_pretty(v).unwrap_or_else(|_| v.to_string())
}

/// What a model should know before touching a worship library. Kept here,
/// next to the tools, rather than in a separate skill file, so every client
/// gets it with no extra install step.
const INSTRUCTIONS: &str = r#"Church Slides is a worship-lyrics presenter. These tools read and edit the user's song library and setlists on this computer. The app updates live while it is open.

SONG FORMAT (body_text)
- Section tags on their own line: [Verse 1], [Verse 2], [Chorus], [Pre-Chorus], [Bridge], [Tag], [Intro], [Outro], [Instrumental]. Short forms work too: [V1], [C], [B].
- A blank line inside a section starts a new slide. Aim for 2 lines per slide (4 at most); keep lines under about 60 characters.
- Write each section ONCE. Repeats belong in slide_order, not in the body.
- No chord symbols, no "x2" / "(repeat)" markers, no stage directions.

SLIDE ORDER (slide_order)
- Comma-separated section references giving the sung arrangement, e.g. "V1, C, V2, C, B, C, C". Omit it to play each section once in written order.

LYRICS AND COPYRIGHT
- Only store lyrics the user has given you (pasted text, a file, a chord chart, an export). Do not write out lyrics of copyrighted songs from memory; if the user asks for a song that isn't in the library and gives no source, say so and ask them to paste or attach the lyrics. Public-domain hymns are fine.
- Fill in author, CCLI number and copyright when the source shows them.

IMPORTING SONG FILES
- For song files on this computer (PDF chord sheets, ChordPro, OnSong, OpenLyrics, OpenSong, SongSelect, text charts), use import_song_files with their full paths or a folder. It keeps chords and keys, won't duplicate songs already in the library, and opens the app if needed. For a big or unfamiliar folder, run it with dry_run first and summarise for the user.
- Afterwards, check what was imported (get_song) and fix section labels or slide order with update_song; the report's warnings point at songs worth checking.
- If the report shows a file's chords went to the wrong song (existing_song isn't the one you meant — a duplicate title, a wrong CCLI number), run it again with attach_to naming the right song id. Don't rename or edit other songs to steer the match.
- Files attached to the conversation aren't on disk where the app can read them. Ask the user where the files are saved; if they'd rather not, read the attachment yourself and use create_song (chords won't be kept that way).
- Whole-library exports (ProPresenter Export.txt, Church Slides backups) aren't imported this way — the user imports those from the app's Library screen.

WORKING WITH THE LIBRARY
- Search before creating, to avoid duplicates (list_songs with a few title words).
- When the user gives a list of songs, look them all up with one find_songs call: the title of each, plus any lyric they quoted as its hint. Where it isn't confident (two copies of a song, a near title), pick the one with chords and more use, or ask.
- To build a service, pick songs with list_songs (it shows setlist_count and last_presented to help avoid over-used songs), then create_setlist with the songs in running order. To change one, update_setlist with the complete new order.
- Titles vary; when the user quotes a line of the song, use it to confirm the match (first_line, or get_song).

GETTING A SERVICE READY FOR THE BAND
A setlist is only done when the band can play from it. After building or changing one, read it back with get_setlist and, in the same pass:
- Chords: every song with has_chords false needs a chord sheet. If the user has a folder of charts (they mentioned one, or you imported from one), find each missing song's sheet there with find_song_files and import it — chords attach to the existing song. Prefer the sheet in the key the user wants, else the newest copy. A folder named for this service that holds most of the set is usually the band's own set: import it whole. Report any song you couldn't find a sheet for.
- A song with chords but no original_key can't be transposed reliably: read its chords (or the sheet) and record the key with update_song chart_key.
- Keys: when the user gives keys for the service ("Goodness of God - Ab"), set them with update_setlist, songs: [{song_id, key}] — one call for the whole set, and it can reorder at the same time. A range like "C-E" usually means the song modulates: set the starting key and mention the change. If no key is given, leave the song in its own key.
- Grouping by key: when the user lets you move songs and keys so the set flows, keep each shift within what they allow, keep the set's shape (opener, closer), and report each change as "up 1" / "down 2" (get_setlist's transpose).
- Ask before deleting anything. Before a run of changes the whole library is snapshotted (the path is in each change's result); give the user that path if they want to undo."#;

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;
    use crate::db::Db;

    fn lib() -> (tempfile::TempDir, Library) {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("t.db")).unwrap());
        (dir, Library::new(db, None))
    }

    fn roundtrip(lib: &Library, lines: &[Value]) -> Vec<Value> {
        let input: String = lines.iter().map(|l| format!("{l}\n")).collect();
        let mut out = Vec::new();
        serve(lib, input.as_bytes(), &mut out).unwrap();
        String::from_utf8(out)
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    #[test]
    fn handshake_list_and_call() {
        let (_d, lib) = lib();
        let replies = roundtrip(
            &lib,
            &[
                json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}),
                json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
                json!({"jsonrpc":"2.0","id":2,"method":"tools/list"}),
                json!({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_songs","arguments":{"query":"amazing"}}}),
                json!({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_song","arguments":{"id":"nope"}}}),
                json!({"jsonrpc":"2.0","id":5,"method":"bogus"}),
            ],
        );
        assert_eq!(replies.len(), 5, "the notification gets no reply");
        assert_eq!(replies[0]["result"]["protocolVersion"], "2024-11-05");
        assert!(replies[1]["result"]["tools"].as_array().unwrap().len() >= 10);
        assert_eq!(replies[2]["result"]["isError"], false);
        assert_eq!(
            replies[2]["result"]["structuredContent"]["songs"][0]["title"],
            "Amazing Grace"
        );
        assert_eq!(replies[3]["result"]["isError"], true);
        assert_eq!(replies[4]["error"]["code"], -32601);
    }

    #[test]
    fn unknown_protocol_version_gets_ours() {
        let (_d, lib) = lib();
        let r = handle(
            &lib,
            &json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"1999-01-01"}}),
        )
        .unwrap();
        assert_eq!(r["result"]["protocolVersion"], PROTOCOL_VERSIONS[0]);
    }

    #[test]
    fn every_tool_in_the_spec_is_dispatched() {
        let (_d, lib) = lib();
        for spec in tools::specs().as_array().unwrap() {
            let name = spec["name"].as_str().unwrap();
            let err = lib.call(name, &json!({})).err().map(|e| e.to_string());
            assert!(
                !err.unwrap_or_default().starts_with("unknown tool"),
                "{name} is listed but not handled"
            );
        }
    }
}
