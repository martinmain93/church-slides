// Prevents an additional terminal window from appearing on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--mcp") {
        std::process::exit(auto_presenter_lib::run_mcp_stdio(&args));
    }
    auto_presenter_lib::run();
}
