//! Minimal bounded observability for the native engine.
//!
//! The Python supervisor owns process lifecycle.  This module only emits rare
//! native-internal transitions and never accepts arbitrary messages or fields.

use serde::ser::{SerializeMap, Serializer};
use std::fmt::Write as _;
use std::os::fd::RawFd;
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, Ordering};

const MAX_LINE_BYTES: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Profile {
    Quiet,
    Normal,
    Verbose,
    Debug,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Format {
    Pretty,
    Json,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Detail {
    Quiet,
    Normal,
    Verbose,
    Debug,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Level {
    Debug,
    Info,
    Warning,
    Error,
    Critical,
}

impl Level {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Debug => "DEBUG",
            Self::Info => "INFO",
            Self::Warning => "WARNING",
            Self::Error => "ERROR",
            Self::Critical => "CRITICAL",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Config {
    pub profile: Profile,
    pub format: Format,
    pub no_color: bool,
    pub generation: u64,
}

impl Config {
    pub fn parse(
        profile: &str,
        format: &str,
        no_color: bool,
        generation: &str,
    ) -> Result<Self, ConfigError> {
        let profile = match profile {
            "quiet" => Profile::Quiet,
            "normal" => Profile::Normal,
            "verbose" => Profile::Verbose,
            "debug" => Profile::Debug,
            _ => return Err(ConfigError),
        };
        let format = match format {
            "pretty" => Format::Pretty,
            "json" => Format::Json,
            _ => return Err(ConfigError),
        };
        let generation = generation.parse::<u64>().map_err(|_| ConfigError)?;
        Ok(Self {
            profile,
            format,
            no_color,
            generation,
        })
    }

    pub const fn enabled(self, detail: Detail) -> bool {
        detail_rank(detail) <= profile_rank(self.profile)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConfigError;

static CONFIG: OnceLock<Config> = OnceLock::new();
static EMERGENCY_ACTIVE: AtomicBool = AtomicBool::new(false);

pub fn initialize_from_environment() -> Result<Config, ConfigError> {
    let profile = std::env::var("LOG_PROFILE").map_err(|_| ConfigError)?;
    let format = std::env::var("LOG_FORMAT").map_err(|_| ConfigError)?;
    let generation = std::env::var("COMET_ENGINE_GENERATION").map_err(|_| ConfigError)?;
    let config = Config::parse(
        &profile,
        &format,
        std::env::var_os("NO_COLOR").is_some(),
        &generation,
    )?;
    CONFIG.set(config).map_err(|_| ConfigError)?;
    Ok(config)
}

pub fn environment_format() -> Format {
    match std::env::var("LOG_FORMAT").as_deref() {
        Ok("json") => Format::Json,
        _ => Format::Pretty,
    }
}

pub fn enabled(detail: Detail) -> bool {
    CONFIG.get().is_some_and(|config| config.enabled(detail))
}

const fn profile_rank(profile: Profile) -> u8 {
    match profile {
        Profile::Quiet => 0,
        Profile::Normal => 1,
        Profile::Verbose => 2,
        Profile::Debug => 3,
    }
}

const fn detail_rank(detail: Detail) -> u8 {
    match detail {
        Detail::Quiet => 0,
        Detail::Normal => 1,
        Detail::Verbose => 2,
        Detail::Debug => 3,
    }
}

#[derive(Clone, Copy, Debug)]
pub enum FieldValue<'a> {
    Token(&'a str),
    Unsigned(u64),
}

#[derive(Clone, Copy, Debug)]
pub struct Field<'a> {
    pub name: &'static str,
    pub value: FieldValue<'a>,
}

impl<'a> Field<'a> {
    pub const fn token(name: &'static str, value: &'a str) -> Self {
        Self {
            name,
            value: FieldValue::Token(value),
        }
    }

    pub const fn unsigned(name: &'static str, value: u64) -> Self {
        Self {
            name,
            value: FieldValue::Unsigned(value),
        }
    }
}

pub fn emit(
    detail: Detail,
    level: Level,
    event: &'static str,
    message: &'static str,
    fields: &[Field<'_>],
) {
    let Some(config) = CONFIG.get().copied() else {
        emergency("logging.renderer.failed", Format::Pretty);
        return;
    };
    if !config.enabled(detail) {
        return;
    }
    let payload = match render(config, level, event, message, fields) {
        Ok(payload) => payload,
        Err(()) => {
            emergency("logging.renderer.failed", config.format);
            return;
        }
    };
    if !write_once(libc::STDERR_FILENO, &payload) {
        emergency("logging.sink.failed", config.format);
    }
}

fn render(
    config: Config,
    level: Level,
    event: &str,
    message: &str,
    fields: &[Field<'_>],
) -> Result<Vec<u8>, ()> {
    let timestamp = timestamp_now()?;
    let payload = match config.format {
        Format::Json => render_json(config, &timestamp, level, event, message, fields)?,
        Format::Pretty => render_pretty(config, &timestamp, level, message, fields),
    };
    Ok(payload)
}

fn render_json(
    config: Config,
    timestamp: &str,
    level: Level,
    event: &str,
    message: &str,
    fields: &[Field<'_>],
) -> Result<Vec<u8>, ()> {
    let mut output = Vec::with_capacity(512);
    {
        let mut serializer = serde_json::Serializer::new(&mut output);
        let mut map = serializer
            .serialize_map(Some(8 + fields.len()))
            .map_err(|_| ())?;
        map.serialize_entry("timestamp", timestamp)
            .map_err(|_| ())?;
        map.serialize_entry("level", level.as_str())
            .map_err(|_| ())?;
        map.serialize_entry("event", event).map_err(|_| ())?;
        map.serialize_entry("message", message).map_err(|_| ())?;
        map.serialize_entry("category", "USENET").map_err(|_| ())?;
        map.serialize_entry("process_role", "usenet_engine")
            .map_err(|_| ())?;
        map.serialize_entry("pid", &std::process::id())
            .map_err(|_| ())?;
        map.serialize_entry("engine_generation", &config.generation)
            .map_err(|_| ())?;
        for field in fields {
            match field.value {
                FieldValue::Token(value) => {
                    map.serialize_entry(field.name, value).map_err(|_| ())?
                }
                FieldValue::Unsigned(value) => {
                    map.serialize_entry(field.name, &value).map_err(|_| ())?
                }
            }
        }
        map.end().map_err(|_| ())?;
    }
    output.push(b'\n');
    Ok(output)
}

fn render_pretty(
    config: Config,
    timestamp: &str,
    level: Level,
    message: &str,
    fields: &[Field<'_>],
) -> Vec<u8> {
    let mut output = String::with_capacity(512);
    let color = !config.no_color;
    if color {
        let _ = write!(
            output,
            "{timestamp} | \x1b[38;5;208m📦 USENET\x1b[0m | {}{}\x1b[0m | {message}",
            level_color(level),
            level.as_str()
        );
    } else {
        let _ = write!(
            output,
            "{timestamp} | 📦 USENET | {} | {message}",
            level.as_str()
        );
    }
    let _ = write!(output, " | generation={}", config.generation);
    for field in fields {
        let label = match field.name {
            "duration_ms" => "duration",
            "error_code" => "error",
            name => name,
        };
        let _ = write!(output, " {label}=");
        match field.value {
            FieldValue::Token(value) => output.push_str(value),
            FieldValue::Unsigned(value) => {
                let _ = write!(output, "{value}");
                if field.name == "duration_ms" {
                    output.push_str("ms");
                }
            }
        }
    }
    output.push('\n');
    output.into_bytes()
}

fn timestamp_now() -> Result<String, ()> {
    let mut timespec = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, &mut timespec) } != 0 {
        return Err(());
    }
    timestamp_from_epoch(timespec.tv_sec)
}

fn timestamp_from_epoch(seconds: libc::time_t) -> Result<String, ()> {
    let mut broken_down = unsafe { std::mem::zeroed::<libc::tm>() };
    if unsafe { libc::gmtime_r(&seconds, &mut broken_down) }.is_null() {
        return Err(());
    }
    Ok(format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        broken_down.tm_year + 1900,
        broken_down.tm_mon + 1,
        broken_down.tm_mday,
        broken_down.tm_hour,
        broken_down.tm_min,
        broken_down.tm_sec
    ))
}

const fn level_color(level: Level) -> &'static str {
    match level {
        Level::Debug => "\x1b[36m",
        Level::Info => "\x1b[32m",
        Level::Warning => "\x1b[33m",
        Level::Error => "\x1b[31m",
        Level::Critical => "\x1b[1;31m",
    }
}

pub fn install_panic_hook() {
    std::panic::set_hook(Box::new(|_| {
        emergency(
            "runtime.panic.detected",
            CONFIG.get().map_or(Format::Pretty, |config| config.format),
        );
    }));
}

pub fn install_silent_panic_hook() {
    std::panic::set_hook(Box::new(|_| {}));
}

pub fn emergency(event: &'static str, format: Format) {
    if EMERGENCY_ACTIVE.swap(true, Ordering::AcqRel) {
        return;
    }
    let (level, message) = match event {
        "logging.renderer.failed" => ("ERROR", "Logging renderer failed"),
        "logging.sink.failed" => ("ERROR", "Logging sink failed"),
        "runtime.panic.detected" => ("CRITICAL", "Native runtime panic detected"),
        _ => ("CRITICAL", "Native runtime bootstrap failed"),
    };
    let timestamp = timestamp_now().unwrap_or_else(|()| "1970-01-01 00:00:00".into());
    let payload = match format {
        Format::Pretty => {
            format!("{timestamp} | 📦 USENET | {level} | {message}\n").into_bytes()
        }
        Format::Json => format!(
            "{{\"timestamp\":\"{timestamp}\",\"level\":\"{level}\",\"event\":\"{event}\",\"message\":\"{message}\"}}\n"
        )
        .into_bytes(),
    };
    let _ = write_once(libc::STDERR_FILENO, &payload);
    EMERGENCY_ACTIVE.store(false, Ordering::Release);
}

fn write_once(fd: RawFd, payload: &[u8]) -> bool {
    if payload.len() > MAX_LINE_BYTES {
        return false;
    }
    let mut result = unsafe { libc::write(fd, payload.as_ptr().cast(), payload.len()) };
    if result < 0 && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
        result = unsafe { libc::write(fd, payload.as_ptr().cast(), payload.len()) };
    }
    result == payload.len() as isize
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::alloc::{GlobalAlloc, Layout, System};
    use std::cell::Cell;
    use std::fs;
    use std::hint::black_box;

    struct CountingAllocator;

    thread_local! {
        static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
        static DEALLOCATIONS: Cell<usize> = const { Cell::new(0) };
    }

    unsafe impl GlobalAlloc for CountingAllocator {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            ALLOCATIONS.set(ALLOCATIONS.get().saturating_add(1));
            unsafe { System.alloc(layout) }
        }

        unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
            DEALLOCATIONS.set(DEALLOCATIONS.get().saturating_add(1));
            unsafe { System.dealloc(pointer, layout) };
        }
    }

    #[global_allocator]
    static TEST_ALLOCATOR: CountingAllocator = CountingAllocator;

    #[test]
    fn parses_the_exact_profile_format_matrix() {
        for profile in ["quiet", "normal", "verbose", "debug"] {
            for format in ["pretty", "json"] {
                assert!(Config::parse(profile, format, false, "1").is_ok());
            }
        }
        assert!(Config::parse("trace", "json", false, "1").is_err());
        assert!(Config::parse("normal", "text", false, "1").is_err());
        assert!(Config::parse("normal", "json", false, "0").is_ok());
    }

    #[test]
    fn renders_fixed_utc_seconds() {
        assert_eq!(
            timestamp_from_epoch(0).expect("render epoch"),
            "1970-01-01 00:00:00"
        );
        assert_eq!(
            timestamp_from_epoch(1_784_979_296).expect("render timestamp"),
            "2026-07-25 11:34:56"
        );
    }

    #[test]
    fn both_renderers_are_bounded_single_lines() {
        let fields = [
            Field::token("error_code", "socket_failure"),
            Field::unsigned("duration_ms", 42),
        ];
        for format in [Format::Pretty, Format::Json] {
            let payload = render(
                Config {
                    profile: Profile::Debug,
                    format,
                    no_color: true,
                    generation: 7,
                },
                Level::Error,
                "native.socket.failed",
                "Native socket failed",
                &fields,
            )
            .expect("render");
            assert!(payload.len() <= MAX_LINE_BYTES);
            assert_eq!(payload.iter().filter(|byte| **byte == b'\n').count(), 1);
            if format == Format::Json {
                let value: serde_json::Value =
                    serde_json::from_slice(&payload).expect("parse JSON");
                assert_eq!(value["engine_generation"], 7);
                assert_eq!(value["duration_ms"], 42);
            } else {
                let line = std::str::from_utf8(&payload).expect("pretty UTF-8");
                assert!(line.contains(" | 📦 USENET | ERROR | Native socket failed"));
            }
        }
    }

    #[test]
    fn one_million_filtered_debug_checks_do_not_allocate() {
        let config = Config::parse("normal", "json", true, "1").expect("config");
        black_box(config.enabled(Detail::Debug));
        let allocations = ALLOCATIONS.get();
        let deallocations = DEALLOCATIONS.get();
        for _ in 0..1_000_000 {
            black_box(config.enabled(Detail::Debug));
        }
        assert_eq!(ALLOCATIONS.get() - allocations, 0);
        assert_eq!(DEALLOCATIONS.get() - deallocations, 0);
    }

    fn resident_bytes() -> u64 {
        let status = fs::read_to_string("/proc/self/status").expect("read process status");
        status
            .lines()
            .find_map(|line| {
                line.strip_prefix("VmRSS:")
                    .and_then(|value| value.split_ascii_whitespace().next())
                    .and_then(|value| value.parse::<u64>().ok())
            })
            .expect("VmRSS is available")
            * 1024
    }

    #[test]
    #[ignore = "dedicated serial RSS quality gate"]
    fn filtered_debug_rss_growth_is_below_two_mebibytes() {
        let config = Config::parse("normal", "json", true, "1").expect("config");
        for _ in 0..10_000 {
            black_box(config.enabled(Detail::Debug));
        }
        let before = resident_bytes();
        for _ in 0..1_000_000 {
            black_box(config.enabled(Detail::Debug));
        }
        let after = resident_bytes();
        assert!(
            after <= before + 2 * 1024 * 1024,
            "filtered debug RSS grew by {} bytes",
            after.saturating_sub(before)
        );
    }

    #[test]
    fn canonical_pipe_supports_atomic_maximum_lines() {
        let mut descriptors = [0; 2];
        assert_eq!(unsafe { libc::pipe(descriptors.as_mut_ptr()) }, 0);
        let pipe_buf = unsafe { libc::fpathconf(descriptors[1], libc::_PC_PIPE_BUF) };
        unsafe {
            libc::close(descriptors[0]);
            libc::close(descriptors[1]);
        }
        assert!(pipe_buf >= MAX_LINE_BYTES as libc::c_long);
    }
}
