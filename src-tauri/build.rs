fn main() {
    // The online band relay (relay/dist/worker.js) is embedded in the app and
    // uploaded to the church's Cloudflare account. `pnpm build` produces it;
    // in a dev build that hasn't run it, embed a stub so the app still
    // compiles — setting up the relay then says to run `pnpm build:relay`.
    let worker = std::path::Path::new("../relay/dist/worker.js");
    if !worker.exists() {
        std::fs::create_dir_all(worker.parent().unwrap()).ok();
        std::fs::write(worker, "// stub: run `pnpm build:relay`\n").ok();
    }
    println!("cargo:rerun-if-changed=../relay/dist/worker.js");
    // The third-party notices (a bundled resource) are written by
    // scripts/third_party_notices.py, which `tauri build` runs first. A dev
    // build gets a placeholder so the resource exists.
    let notices = std::path::Path::new("resources/THIRD-PARTY-NOTICES.txt");
    if !notices.exists() {
        std::fs::create_dir_all(notices.parent().unwrap()).ok();
        std::fs::write(
            notices,
            "Church Slides: third-party notices\n\n\
             This development build has no notices. Release builds list every \
             open-source component here; generate them with:\n\n    \
             uv run --no-project python scripts/third_party_notices.py\n",
        )
        .ok();
    }
    tauri_build::build();
}
