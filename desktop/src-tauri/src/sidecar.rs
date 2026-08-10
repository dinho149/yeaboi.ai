//! Owning the Python server that the window points at.
//!
//! The app bundle is not a static page: everything it renders arrives from
//! `/api`, so the desktop shell has to run `yeaboi app` and navigate to it.
//! That makes process lifetime the whole problem, and it has three parts, each
//! of which fails in a way a user would notice:
//!
//! 1. **Finding the port.** 5599 may be taken, so the child is started with
//!    `--port 0` and prints `listening on <url>`. The shell reads that line
//!    rather than guessing, which is why the Python side prints it before any
//!    styled output.
//! 2. **Not hanging forever.** If the child dies at startup — no Python, a bad
//!    install, a failed migration — there is no line to read, and a naive
//!    `read_line` would block until the user force-quits an empty window. So
//!    the wait is bounded and also watches for the child exiting.
//! 3. **Dying with the parent.** A server that outlives the window holds the
//!    user's projects open on a port they cannot see and cannot stop. `Drop`
//!    kills it, and the window's exit handler drops it.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// How long to wait for the server to say where it is.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

pub struct Sidecar {
    child: Child,
    pub url: String,
}

#[derive(Debug)]
pub enum SidecarError {
    Spawn(std::io::Error),
    /// The process started but never announced a URL.
    NoAddress,
    /// The process exited before announcing one.
    Exited(Option<i32>),
}

impl std::fmt::Display for SidecarError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SidecarError::Spawn(e) => write!(f, "could not start `yeaboi app`: {e}"),
            SidecarError::NoAddress => write!(f, "`yeaboi app` did not report a URL in time"),
            SidecarError::Exited(code) => {
                write!(f, "`yeaboi app` exited before starting (code {code:?})")
            }
        }
    }
}

/// Pull the URL out of the server's `listening on <url>` line.
///
/// A free function so it can be tested without spawning anything — the parsing
/// is the part most likely to drift when someone edits the Python side's
/// output, and it is the part a test can hold still.
pub fn parse_listening(line: &str) -> Option<String> {
    let rest = line.trim().strip_prefix("listening on ")?;
    let url = rest.split_whitespace().next()?;
    if url.starts_with("http://") || url.starts_with("https://") {
        Some(url.to_string())
    } else {
        None
    }
}

impl Sidecar {
    /// Start the server and wait for it to say where it is.
    pub fn start(program: &str) -> Result<Self, SidecarError> {
        let mut child = Command::new(program)
            .args(["app", "--port", "0"])
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(SidecarError::Spawn)?;

        let stdout = child.stdout.take().ok_or(SidecarError::NoAddress)?;
        let mut reader = BufReader::new(stdout);
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let mut line = String::new();

        loop {
            if Instant::now() > deadline {
                let _ = child.kill();
                return Err(SidecarError::NoAddress);
            }
            line.clear();
            match reader.read_line(&mut line) {
                // EOF: the pipe closed, so the child is finished talking.
                Ok(0) => {
                    let code = child.wait().ok().and_then(|status| status.code());
                    return Err(SidecarError::Exited(code));
                }
                Ok(_) => {
                    if let Some(url) = parse_listening(&line) {
                        return Ok(Sidecar { child, url });
                    }
                    // Any other line is the server's own chatter; keep reading.
                }
                Err(_) => {
                    let _ = child.kill();
                    return Err(SidecarError::NoAddress);
                }
            }
        }
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        // Best effort by necessity: the child may already be gone, and there is
        // nothing useful to do about a failure while tearing down.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::parse_listening;

    #[test]
    fn reads_the_url_out_of_the_line() {
        assert_eq!(
            parse_listening("listening on http://127.0.0.1:51260"),
            Some("http://127.0.0.1:51260".to_string())
        );
    }

    #[test]
    fn tolerates_a_trailing_newline_and_spaces() {
        assert_eq!(
            parse_listening("  listening on http://127.0.0.1:1\n"),
            Some("http://127.0.0.1:1".to_string())
        );
    }

    #[test]
    fn ignores_other_output() {
        assert_eq!(parse_listening("yeaboi app on http://x"), None);
        assert_eq!(parse_listening(""), None);
    }

    #[test]
    fn refuses_a_non_http_scheme() {
        // The value is handed straight to the webview.
        assert_eq!(parse_listening("listening on file:///etc/passwd"), None);
    }
}
