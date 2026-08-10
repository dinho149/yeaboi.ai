//! yeaboi, as a desktop window.
//!
//! The window is a webview pointed at a `yeaboi app` server this process owns.
//! See `sidecar.rs` for why the lifetime of that child is the substance of this
//! crate rather than an implementation detail.

// Hide the console window on Windows in a release build. On macOS and Linux
// this attribute is inert.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use sidecar::{resolve_server_command, Sidecar};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn main() {
    // `yeaboi` from PATH unless YEABOI_SERVER_CMD says otherwise. Still wrong
    // for a distributable — a packaged app cannot assume the CLI is installed
    // at all — and bundling a runtime is the packaging work in desktop/README.md.
    let program = resolve_server_command();
    let sidecar = match Sidecar::start(&program) {
        Ok(sidecar) => sidecar,
        Err(error) => {
            // No window, because there is nothing to show in one: the app has
            // no offline mode, every screen comes from the server, and a blank
            // window with a spinner would be a worse way to say the same thing.
            eprintln!("yeaboi desktop could not start.\n\n{error}");
            std::process::exit(1);
        }
    };

    let url = sidecar.url.clone();

    tauri::Builder::default()
        .setup(move |app| {
            // Parked in Tauri's state so it lives exactly as long as the app,
            // and its Drop — which kills the server — runs on the way out.
            app.manage(sidecar);

            let parsed = url.parse().expect("the sidecar URL was validated on the way in");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
                .title("yeaboi")
                .inner_size(1200.0, 800.0)
                .min_inner_size(720.0, 480.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running yeaboi desktop");
}
