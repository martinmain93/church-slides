//! Keep the display awake while a service is being presented.
//!
//! With an operator at the laptop, their clicks keep macOS from dimming the
//! screen. When the worship leader drives from their own device and nobody
//! touches the laptop, the display would go to sleep (or the screensaver
//! would start) after the system's idle timeout — and the projector with it.
//! A power assertion held for the length of the service prevents that, and
//! is released the moment the presentation closes (or the app quits: the OS
//! drops assertions with the process).

use std::sync::Mutex;

#[derive(Default)]
pub struct KeepAwake(Mutex<Option<keepawake::KeepAwake>>);

impl KeepAwake {
    /// Hold or release the assertion. Idempotent both ways.
    pub fn set(&self, on: bool) -> Result<(), String> {
        let mut held = self.0.lock().unwrap();
        if !on {
            *held = None; // dropping releases it
            return Ok(());
        }
        if held.is_none() {
            let awake = keepawake::Builder::default()
                .display(true)
                .idle(true)
                .reason("Presenting a service")
                .app_name("Church Slides")
                .app_reverse_domain("com.churchslides.app")
                .create()
                .map_err(|e| format!("couldn't keep the display awake: {e}"))?;
            *held = Some(awake);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn holds_and_releases() {
        let k = KeepAwake::default();
        k.set(true).unwrap();
        k.set(true).unwrap(); // idempotent
        assert!(k.0.lock().unwrap().is_some());
        if std::env::var("KEEP_AWAKE_HOLD_MS").is_ok() {
            let ms = std::env::var("KEEP_AWAKE_HOLD_MS").unwrap().parse().unwrap_or(0);
            std::thread::sleep(std::time::Duration::from_millis(ms));
        }
        k.set(false).unwrap();
        assert!(k.0.lock().unwrap().is_none());
    }
}
