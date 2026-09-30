use rand::{distr::Alphanumeric, Rng};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, Manager, State};
use thiserror::Error;

/// Default ceiling for a single request. Heavy analyses may override it from
/// the frontend; control operations always use a short timeout.
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(120);
const CONTROL_TIMEOUT: Duration = Duration::from_secs(10);

/// How long the splash may stay up before the coordinator reports a failure.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);

/// Event name used to forward sidecar progress to the webview.
pub const PROGRESS_EVENT: &str = "sidecar://progress";

/// Startup lifecycle events consumed by the splash window.
pub const STARTUP_STAGE_EVENT: &str = "startup://stage";
pub const STARTUP_FAILED_EVENT: &str = "startup://failed";
pub const STARTUP_READY_EVENT: &str = "startup://ready";

/// Window labels declared in `tauri.conf.json`.
pub const MAIN_WINDOW: &str = "main";
pub const SPLASH_WINDOW: &str = "splash";

/// Process start, used to timestamp every startup milestone.
///
/// The trace is written to stderr (which the sidecar also inherits) so a
/// release/debug run can be measured without a debugger attached:
///
/// ```text
/// [startup] setup t=12ms
/// [startup] splash_ready t=180ms
/// [startup] engine_ready t=640ms
/// [startup] frontend_ready t=910ms
/// [startup] ready t=915ms
/// ```
static PROCESS_START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

fn trace(stage: &str, detail: &str) {
    let elapsed = PROCESS_START
        .get()
        .map(|start| start.elapsed().as_millis())
        .unwrap_or(0);
    eprintln!("[startup] {stage} t={elapsed}ms {detail}");
}

/// Receives progress notifications from the sidecar reader thread.
///
/// Keeping this as a plain callback (instead of an `AppHandle`) lets the
/// process and manager be exercised in unit tests without a running Tauri
/// application, while production still forwards to the webview.
type ProgressSink = Arc<dyn Fn(Value) + Send + Sync>;

#[derive(Debug, Error)]
enum SidecarProcessError {
    #[error("No se encontró el sidecar Python")]
    Missing,
    #[error("No se pudo iniciar el sidecar: {0}")]
    Start(String),
    #[error("El sidecar cerró su canal de respuesta")]
    Disconnected,
    #[error("El sidecar no respondió dentro del tiempo permitido")]
    Timeout,
    #[error("No se pudo enviar la solicitud: {0}")]
    Write(String),
}

/// Route one decoded stdout line to the request that owns its `id`.
///
/// Returns `Some(value)` when the line is a progress notification (the caller
/// forwards it to the webview) and `None` when it was a response delivered to
/// a waiting request. Lines without a matching pending request are dropped,
/// which is what keeps a late response from a cancelled request harmless.
fn route_line(
    value: Value,
    pending: &Arc<Mutex<HashMap<String, mpsc::Sender<Value>>>>,
) -> Option<Value> {
    let kind = value
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("response");
    if kind == "progress" {
        return Some(value);
    }
    let Some(id) = value.get("id").and_then(Value::as_str) else {
        return None;
    };
    let sender = pending.lock().ok().and_then(|mut map| map.remove(id));
    if let Some(sender) = sender {
        let _ = sender.send(value);
    }
    None
}

/// One running Python sidecar process.
///
/// The process is shared behind an `Arc` so several requests can be in flight
/// at once. A permanent reader thread routes every stdout line to the pending
/// request that owns its `id`, which is what makes the channel multiplexed:
/// no request ever waits for another one to finish.
struct SidecarProcess {
    child: Mutex<Child>,
    stdin: Mutex<ChildStdin>,
    pending: Arc<Mutex<HashMap<String, mpsc::Sender<Value>>>>,
    token: String,
    sequence: AtomicU64,
}

impl SidecarProcess {
    fn start(resource_dir: &Path, sink: ProgressSink) -> Result<Self, SidecarProcessError> {
        let token: String = rand::rng()
            .sample_iter(&Alphanumeric)
            .take(48)
            .map(char::from)
            .collect();
        let mut command = sidecar_command(resource_dir, &token)?;
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .creation_flags(0x08000000)
            .spawn()
            .map_err(|error| SidecarProcessError::Start(error.to_string()))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| SidecarProcessError::Start("stdin no disponible".into()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| SidecarProcessError::Start("stdout no disponible".into()))?;
        let pending: Arc<Mutex<HashMap<String, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let reader_pending = Arc::clone(&pending);
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(value) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                if let Some(progress) = route_line(value, &reader_pending) {
                    // Progress is fire-and-forget: it never resolves a
                    // request, it only informs the UI.
                    sink(progress);
                }
            }
            // The sidecar died: drop every waiter so pending requests fail
            // fast instead of hanging until their timeout.
            if let Ok(mut map) = reader_pending.lock() {
                map.clear();
            }
        });
        Ok(Self {
            child: Mutex::new(child),
            stdin: Mutex::new(stdin),
            pending,
            token,
            sequence: AtomicU64::new(0),
        })
    }

    fn is_alive(&self) -> bool {
        self.child
            .lock()
            .map(|mut child| child.try_wait().map(|state| state.is_none()).unwrap_or(false))
            .unwrap_or(false)
    }

    fn next_id(&self) -> String {
        self.sequence.fetch_add(1, Ordering::SeqCst).to_string()
    }

    fn write(&self, value: &Value) -> Result<(), SidecarProcessError> {
        let mut stdin = self
            .stdin
            .lock()
            .map_err(|_| SidecarProcessError::Write("stdin bloqueado".into()))?;
        writeln!(stdin, "{value}")
            .and_then(|_| stdin.flush())
            .map_err(|error| SidecarProcessError::Write(error.to_string()))
    }

    /// Send one request and wait for its own response, ignoring progress.
    fn request(
        &self,
        request_id: Option<String>,
        operation: &str,
        params: Value,
        priority: &str,
        timeout: Duration,
    ) -> Result<Value, SidecarProcessError> {
        if !self.is_alive() {
            return Err(SidecarProcessError::Disconnected);
        }
        let id = request_id.unwrap_or_else(|| self.next_id());
        let (sender, receiver) = mpsc::channel();
        if let Ok(mut map) = self.pending.lock() {
            map.insert(id.clone(), sender);
        }
        let request = json!({
            "id": id,
            "type": "request",
            "token": self.token,
            "operation": operation,
            "params": params,
            "priority": priority,
        });
        if let Err(error) = self.write(&request) {
            if let Ok(mut map) = self.pending.lock() {
                map.remove(&id);
            }
            return Err(error);
        }
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            match receiver.recv_timeout(remaining) {
                Ok(value) => return Ok(value),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if let Ok(mut map) = self.pending.lock() {
                        map.remove(&id);
                    }
                    return Err(SidecarProcessError::Timeout);
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return Err(SidecarProcessError::Disconnected);
                }
            }
        }
    }

    /// Ask the sidecar to cancel an in-flight request by id.
    fn cancel(&self, target_id: &str) -> Result<(), SidecarProcessError> {
        if !self.is_alive() {
            return Err(SidecarProcessError::Disconnected);
        }
        let id = self.next_id();
        self.write(&json!({
            "id": id,
            "type": "cancel",
            "token": self.token,
            "target_id": target_id,
        }))
    }

    fn stop(&self) {
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Ok(mut map) = self.pending.lock() {
            map.clear();
        }
    }
}

impl Drop for SidecarProcess {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Owns the sidecar lifecycle and multiplexes requests over it.
///
/// This remains the single source of truth about the Python process: both the
/// startup prewarm and every runtime request go through it, so a startup
/// failure and a runtime crash share one recovery path.
struct SidecarManager {
    process: Mutex<Option<Arc<SidecarProcess>>>,
    resource_dir: PathBuf,
    sink: ProgressSink,
}

impl SidecarManager {
    fn new(resource_dir: PathBuf, sink: ProgressSink) -> Self {
        Self { process: Mutex::new(None), resource_dir, sink }
    }

    fn ensure(&self) -> Result<Arc<SidecarProcess>, String> {
        let mut guard = self
            .process
            .lock()
            .map_err(|_| "Bloqueo del sidecar dañado".to_string())?;
        if let Some(process) = guard.as_ref() {
            if process.is_alive() {
                return Ok(Arc::clone(process));
            }
        }
        let process = Arc::new(
            SidecarProcess::start(&self.resource_dir, Arc::clone(&self.sink))
                .map_err(|error| error.to_string())?,
        );
        *guard = Some(Arc::clone(&process));
        Ok(process)
    }

    fn restart(&self) -> Result<Arc<SidecarProcess>, String> {
        let mut guard = self
            .process
            .lock()
            .map_err(|_| "Bloqueo del sidecar dañado".to_string())?;
        if let Some(process) = guard.take() {
            process.stop();
        }
        let process = Arc::new(
            SidecarProcess::start(&self.resource_dir, Arc::clone(&self.sink))
                .map_err(|error| error.to_string())?,
        );
        *guard = Some(Arc::clone(&process));
        Ok(process)
    }

    fn request(
        &self,
        request_id: Option<String>,
        operation: &str,
        params: Value,
        priority: &str,
        timeout: Duration,
    ) -> Result<Value, String> {
        let process = self.ensure()?;
        match process.request(request_id.clone(), operation, params.clone(), priority, timeout) {
            Ok(response) => Ok(response),
            Err(SidecarProcessError::Disconnected) => {
                // The sidecar crashed mid-request: restart once and retry.
                let process = self.restart()?;
                process
                    .request(request_id, operation, params, priority, timeout)
                    .map_err(|error| error.to_string())
            }
            Err(error) => Err(error.to_string()),
        }
    }

    fn cancel(&self, target_id: &str) -> Result<bool, String> {
        let process = self.ensure()?;
        process.cancel(target_id).map_err(|error| error.to_string())?;
        Ok(true)
    }

    fn stop(&self) {
        if let Ok(mut guard) = self.process.lock() {
            if let Some(process) = guard.take() {
                process.stop();
            }
        }
    }
}

/// The ordered actions a successful reveal performs.
///
/// Pure so the ordering contract can be tested: the main window is shown
/// *before* the splash is closed, so the user never sees an empty desktop
/// between the two windows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RevealAction {
    ShowMain,
    FocusMain,
    CloseSplash,
    EmitReady,
}

fn reveal_plan() -> [RevealAction; 4] {
    [
        RevealAction::ShowMain,
        RevealAction::FocusMain,
        RevealAction::CloseSplash,
        RevealAction::EmitReady,
    ]
}

/// Decide whether the main window should be revealed now.
///
/// Returns `true` only the first time both halves are ready, which is what
/// makes `READY` idempotent even if the engine and the frontend report
/// readiness at the same instant from different threads.
fn reveal_once(engine_ready: bool, frontend_ready: bool, revealed: &AtomicBool) -> bool {
    if !engine_ready || !frontend_ready {
        return false;
    }
    !revealed.swap(true, Ordering::SeqCst)
}

/// The state the coordinator should report after a reveal attempt that did not
/// (yet) reveal the main window. Pure so the transition table can be tested
/// without an `AppHandle`.
fn pending_state(engine_ready: bool, frontend_ready: bool) -> StartupState {
    match (engine_ready, frontend_ready) {
        (true, false) => StartupState::FrontendLoading,
        (false, true) => StartupState::EngineStarting,
        _ => StartupState::Starting,
    }
}

/// Startup lifecycle states. Every transition is driven by a real event
/// (a window finishing its load, the engine answering `health`, the frontend
/// reporting its shell is mounted) — never by a timer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StartupState {
    Starting,
    SplashReady,
    EngineStarting,
    EngineReady,
    FrontendLoading,
    FrontendReady,
    Ready,
    Failed,
}

impl StartupState {
    fn as_str(self) -> &'static str {
        match self {
            StartupState::Starting => "STARTING",
            StartupState::SplashReady => "SPLASH_READY",
            StartupState::EngineStarting => "ENGINE_STARTING",
            StartupState::EngineReady => "ENGINE_READY",
            StartupState::FrontendLoading => "FRONTEND_LOADING",
            StartupState::FrontendReady => "FRONTEND_READY",
            StartupState::Ready => "READY",
            StartupState::Failed => "FAILED",
        }
    }
}

/// Coordinates the parallel startup of the Python engine and the React shell.
///
/// The main window stays hidden until *both* the engine answered `health` and
/// the frontend reported its shell is mounted. `READY` is idempotent: once the
/// main window has been revealed, later readiness signals are ignored.
struct StartupCoordinator {
    state: Mutex<StartupState>,
    engine_ready: AtomicBool,
    frontend_ready: AtomicBool,
    revealed: AtomicBool,
    started_at: Instant,
}

impl StartupCoordinator {
    fn new() -> Self {
        Self {
            state: Mutex::new(StartupState::Starting),
            engine_ready: AtomicBool::new(false),
            frontend_ready: AtomicBool::new(false),
            revealed: AtomicBool::new(false),
            started_at: Instant::now(),
        }
    }

    fn set_state(&self, state: StartupState) {
        if let Ok(mut guard) = self.state.lock() {
            if *guard != state {
                trace("state", state.as_str());
            }
            *guard = state;
        }
    }

    fn state(&self) -> StartupState {
        self.state.lock().map(|guard| *guard).unwrap_or(StartupState::Failed)
    }

    fn elapsed_ms(&self) -> u64 {
        self.started_at.elapsed().as_millis() as u64
    }

    /// Record that the engine answered `health` and reveal the main window if
    /// the frontend is already waiting for it.
    fn mark_engine_ready(&self, app: &AppHandle) {
        self.engine_ready.store(true, Ordering::SeqCst);
        self.set_state(StartupState::EngineReady);
        trace("engine_ready", "");
        self.emit_stage(app, "engine_ready", "Motor local preparado");
        self.try_reveal(app);
    }

    /// Record that the React shell finished its minimal bootstrap.
    fn mark_frontend_ready(&self, app: &AppHandle) {
        self.frontend_ready.store(true, Ordering::SeqCst);
        self.set_state(StartupState::FrontendReady);
        trace("frontend_ready", "");
        self.emit_stage(app, "frontend_ready", "Interfaz preparada");
        self.try_reveal(app);
    }

    /// Reveal the main window exactly once, when both halves are ready.
    fn try_reveal(&self, app: &AppHandle) {
        let engine = self.engine_ready.load(Ordering::SeqCst);
        let frontend = self.frontend_ready.load(Ordering::SeqCst);
        if !reveal_once(engine, frontend, &self.revealed) {
            // The engine is up but the React shell has not signalled yet: the
            // startup is now gated on the frontend alone.
            self.set_state(pending_state(engine, frontend));
            return;
        }
        self.set_state(StartupState::Ready);
        trace("ready", "");
        // Execute the reveal plan in order: show and focus the main window,
        // then close the splash, then announce readiness. The splash is closed
        // only after the main window is visible, so the user never sees an
        // empty desktop between the two.
        for action in reveal_plan() {
            match action {
                RevealAction::ShowMain => {
                    if let Some(main) = app.get_webview_window(MAIN_WINDOW) {
                        let _ = main.show();
                    }
                }
                RevealAction::FocusMain => {
                    if let Some(main) = app.get_webview_window(MAIN_WINDOW) {
                        let _ = main.set_focus();
                    }
                }
                RevealAction::CloseSplash => {
                    if let Some(splash) = app.get_webview_window(SPLASH_WINDOW) {
                        let _ = splash.close();
                    }
                }
                RevealAction::EmitReady => {
                    self.emit_stage(app, "ready", "Polaris lista");
                    let _ = app.emit(
                        STARTUP_READY_EVENT,
                        json!({ "elapsed_ms": self.elapsed_ms() }),
                    );
                }
            }
        }
    }

    fn emit_stage(&self, app: &AppHandle, stage: &str, message: &str) {
        let _ = app.emit(
            STARTUP_STAGE_EVENT,
            json!({ "stage": stage, "message": message, "elapsed_ms": self.elapsed_ms() }),
        );
    }

    fn fail(&self, app: &AppHandle, message: &str) {
        self.set_state(StartupState::Failed);
        trace("failed", message);
        let _ = app.emit(
            STARTUP_FAILED_EVENT,
            json!({ "message": message, "elapsed_ms": self.elapsed_ms() }),
        );
    }
}

/// Spawn the sidecar and wait for its first `health` answer.
///
/// This is the prewarm path: the engine starts during Tauri setup, in
/// parallel with the webview, instead of waiting for a React `api.health()`
/// call. It reuses the FASE 1 request pipeline, so multiplexing, cancellation
/// and restart keep working unchanged.
fn prewarm_engine(manager: Arc<SidecarManager>, coordinator: Arc<StartupCoordinator>,
                  app: AppHandle) {
    coordinator.set_state(StartupState::EngineStarting);
    coordinator.emit_stage(&app, "engine_starting", "Preparando motor local…");
    let result = manager.request(
        None,
        "health",
        json!({}),
        "control",
        CONTROL_TIMEOUT,
    );
    match result {
        Ok(response) if response.get("ok").and_then(Value::as_bool).unwrap_or(false) => {
            coordinator.mark_engine_ready(&app);
        }
        Ok(response) => {
            let message = response
                .get("error")
                .and_then(|error| error.get("message"))
                .and_then(Value::as_str)
                .unwrap_or("El motor local no respondió correctamente.");
            coordinator.fail(&app, message);
        }
        Err(error) => coordinator.fail(&app, &error),
    }
}

/// Decide whether the startup watchdog should give up.
///
/// Pure so the timeout policy can be tested without spawning a thread: the
/// watchdog only fails when the main window has not been revealed yet, the
/// startup has not already failed for another reason, and the deadline has
/// elapsed. It deliberately covers *both* halves — a slow engine and a
/// frontend that never signals readiness are equally fatal, so a controlled
/// frontend failure surfaces a recoverable error instead of hanging forever.
fn watchdog_should_fail(revealed: bool, state: StartupState, elapsed: Duration) -> bool {
    !revealed && state != StartupState::Failed && elapsed >= STARTUP_TIMEOUT
}

/// Watchdog: if the startup never completes, surface a recoverable failure
/// instead of leaving the splash open forever.
fn spawn_startup_watchdog(coordinator: Arc<StartupCoordinator>, app: AppHandle) {
    thread::spawn(move || {
        loop {
            let revealed = coordinator.revealed.load(Ordering::SeqCst);
            let state = coordinator.state();
            if revealed || state == StartupState::Failed {
                return;
            }
            if watchdog_should_fail(revealed, state, coordinator.started_at.elapsed()) {
                coordinator.fail(&app, "El arranque tardó demasiado en completarse.");
                return;
            }
            thread::sleep(Duration::from_millis(200));
        }
    });
}

fn timeout_for(operation: &str, requested: Option<u64>) -> Duration {
    if let Some(millis) = requested {
        return Duration::from_millis(millis.max(1));
    }
    match operation {
        "health" | "cancel_request" | "get_job_status" | "cancel_job" => CONTROL_TIMEOUT,
        _ => RESPONSE_TIMEOUT,
    }
}

#[tauri::command]
async fn sidecar_request(
    state: State<'_, Arc<SidecarManager>>,
    operation: String,
    params: Value,
    priority: Option<String>,
    timeout_ms: Option<u64>,
    request_id: Option<String>,
) -> Result<Value, String> {
    let manager = Arc::clone(state.inner());
    let priority = priority.unwrap_or_else(|| "interactive".to_string());
    let timeout = timeout_for(&operation, timeout_ms);
    // Run the blocking wait off the async runtime so the UI thread is never
    // held while Python works.
    tauri::async_runtime::spawn_blocking(move || {
        manager.request(request_id, &operation, params, &priority, timeout)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
fn cancel_request(state: State<'_, Arc<SidecarManager>>, target_id: String) -> Result<bool, String> {
    state.cancel(&target_id)
}

/// Called by the splash window once it has painted, so the coordinator knows
/// the first real milestone happened.
#[tauri::command]
fn splash_ready(state: State<'_, Arc<StartupCoordinator>>) {
    trace("splash_ready", "");
    state.set_state(StartupState::SplashReady);
}

/// Called by React when the shell is mounted and the essential state is
/// loaded. It never waits for analyses, diagnostics or caches.
#[tauri::command]
fn frontend_ready(state: State<'_, Arc<StartupCoordinator>>, app: AppHandle) {
    state.mark_frontend_ready(&app);
}

/// Retry a failed startup: stop any partially started sidecar, clear the
/// coordinator and prewarm a fresh engine without duplicating processes.
#[tauri::command]
fn retry_startup(
    manager: State<'_, Arc<SidecarManager>>,
    coordinator: State<'_, Arc<StartupCoordinator>>,
    app: AppHandle,
) {
    manager.stop();
    coordinator.engine_ready.store(false, Ordering::SeqCst);
    coordinator.frontend_ready.store(false, Ordering::SeqCst);
    coordinator.revealed.store(false, Ordering::SeqCst);
    coordinator.set_state(StartupState::Starting);
    let manager = Arc::clone(manager.inner());
    let coordinator = Arc::clone(coordinator.inner());
    thread::spawn(move || prewarm_engine(manager, coordinator, app));
}

/// Close the splash and exit the process (used by the splash "Salir" button).
#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn select_dataset() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("Datos", &["csv", "tsv", "txt", "xlsx", "xls", "parquet"])
        .pick_file()
        .map(|path| path.to_string_lossy().into_owned())
}

#[tauri::command]
fn select_export_path(file_name: String, format: String) -> Option<String> {
    let (label, extension) = match format.as_str() {
        "csv" => ("CSV", "csv"),
        "parquet" => ("Parquet", "parquet"),
        _ => return None,
    };
    rfd::FileDialog::new()
        .add_filter(label, &[extension])
        .set_file_name(file_name)
        .save_file()
        .map(|path| path.to_string_lossy().into_owned())
}

/// Persist a chart artifact (base64 PNG) chosen by the user.
///
/// The scientific engine renders charts as PNG bytes; the frontend receives
/// them base64-encoded. This command asks for a destination and writes the
/// decoded bytes, keeping the binary payload out of the JSON IPC channel.
#[tauri::command]
fn save_chart(file_name: String, data_base64: String) -> Result<Option<String>, String> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    let Some(path) = rfd::FileDialog::new()
        .add_filter("PNG", &["png"])
        .set_file_name(file_name)
        .save_file()
    else {
        return Ok(None);
    };
    let bytes = STANDARD
        .decode(data_base64.as_bytes())
        .map_err(|error| format!("Gráfico no válido: {error}"))?;
    std::fs::write(&path, bytes).map_err(|error| format!("No se pudo guardar: {error}"))?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

#[cfg(windows)]
trait WindowsCreationFlags {
    fn creation_flags(&mut self, flags: u32) -> &mut Self;
}

#[cfg(windows)]
impl WindowsCreationFlags for Command {
    fn creation_flags(&mut self, flags: u32) -> &mut Self {
        use std::os::windows::process::CommandExt;
        CommandExt::creation_flags(self, flags)
    }
}

#[cfg(not(windows))]
trait WindowsCreationFlags {
    fn creation_flags(&mut self, _flags: u32) -> &mut Self;
}

#[cfg(not(windows))]
impl WindowsCreationFlags for Command {
    fn creation_flags(&mut self, _flags: u32) -> &mut Self {
        self
    }
}

fn sidecar_command(resource_dir: &Path, token: &str) -> Result<Command, SidecarProcessError> {
    if let Ok(explicit) = std::env::var("MEDICION_SIDECAR") {
        let mut command = Command::new(explicit);
        command.arg("--token").arg(token);
        return Ok(command);
    }
    let executable = std::env::current_exe().map_err(|_| SidecarProcessError::Missing)?;
    let sibling = executable
        .parent()
        .unwrap_or(Path::new("."))
        .join("medicion-sidecar.exe");
    if sibling.is_file() {
        let mut command = Command::new(sibling);
        command.arg("--token").arg(token);
        return Ok(command);
    }
    let bundled = resource_dir.join("sidecar").join("medicion-sidecar.exe");
    if bundled.is_file() {
        let mut command = Command::new(bundled);
        command.arg("--token").arg(token);
        return Ok(command);
    }
    let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let python = workspace.join(".venv").join("Scripts").join("python.exe");
    if python.is_file() {
        let mut command = Command::new(python);
        let python_path = workspace.join("MedicionAgil_Light").join("python");
        let legacy_path = workspace.join("MedicionAgil_Light").join("mmm_app");
        command
            .arg("-m")
            .arg("medicion_core.sidecar")
            .arg("--token")
            .arg(token)
            .env(
                "PYTHONPATH",
                format!("{};{}", python_path.display(), legacy_path.display()),
            );
        return Ok(command);
    }
    Err(SidecarProcessError::Missing)
}

pub fn run() {
    PROCESS_START.get_or_init(Instant::now);
    tauri::Builder::default()
        .setup(|app| {
            trace("setup", "");
            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."));
            let handle = app.handle().clone();
            // Progress is forwarded to the webview from the reader thread.
            let sink: ProgressSink = {
                let handle = handle.clone();
                Arc::new(move |value: Value| {
                    let _ = handle.emit(PROGRESS_EVENT, value);
                })
            };
            let manager = Arc::new(SidecarManager::new(resource_dir, sink));
            let coordinator = Arc::new(StartupCoordinator::new());
            app.manage(Arc::clone(&manager));
            app.manage(Arc::clone(&coordinator));

            // The main window is created hidden by `tauri.conf.json`; the
            // splash is the only thing visible while both halves start.
            if let Some(main) = app.get_webview_window(MAIN_WINDOW) {
                let _ = main.hide();
            }
            if let Some(splash) = app.get_webview_window(SPLASH_WINDOW) {
                let _ = splash.show();
            }

            // Task A: prewarm the Python engine. Task B is the webview itself,
            // which Tauri already loads in parallel. They meet in the
            // coordinator, which reveals the main window once both are ready.
            {
                let manager = Arc::clone(&manager);
                let coordinator = Arc::clone(&coordinator);
                let handle = handle.clone();
                thread::spawn(move || prewarm_engine(manager, coordinator, handle));
            }
            spawn_startup_watchdog(Arc::clone(&coordinator), handle);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_request,
            cancel_request,
            splash_ready,
            frontend_ready,
            retry_startup,
            quit_app,
            select_dataset,
            select_export_path,
            save_chart
        ])
        .build(tauri::generate_context!())
        .expect("error while building Tauri application")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
            ) {
                app.state::<Arc<SidecarManager>>().stop();
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_explicit_sidecar_is_reported() {
        let previous = std::env::var("MEDICION_SIDECAR").ok();
        std::env::set_var("MEDICION_SIDECAR", "Z:\\missing\\sidecar.exe");
        let command = sidecar_command(Path::new("."), "token").unwrap();
        assert!(format!("{command:?}").contains("sidecar.exe"));
        if let Some(value) = previous {
            std::env::set_var("MEDICION_SIDECAR", value);
        } else {
            std::env::remove_var("MEDICION_SIDECAR");
        }
    }

    #[test]
    fn control_operations_use_a_short_timeout() {
        assert_eq!(timeout_for("health", None), CONTROL_TIMEOUT);
        assert_eq!(timeout_for("cancel_request", None), CONTROL_TIMEOUT);
        assert_eq!(timeout_for("run_analysis", None), RESPONSE_TIMEOUT);
        assert_eq!(
            timeout_for("run_analysis", Some(5_000)),
            Duration::from_millis(5_000)
        );
    }

    #[test]
    fn progress_lines_are_forwarded_and_never_resolve_a_request() {
        let pending: Arc<Mutex<HashMap<String, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let (sender, receiver) = mpsc::channel();
        pending.lock().unwrap().insert("1".into(), sender);
        let progress = json!({"id": "1", "type": "progress", "percent": 50});
        let forwarded = route_line(progress, &pending);
        assert!(forwarded.is_some());
        // The request must still be waiting: progress is not a response.
        assert!(receiver.try_recv().is_err());
        assert!(pending.lock().unwrap().contains_key("1"));
    }

    #[test]
    fn responses_are_delivered_to_the_matching_request_only() {
        let pending: Arc<Mutex<HashMap<String, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let (sender_a, receiver_a) = mpsc::channel();
        let (sender_b, receiver_b) = mpsc::channel();
        pending.lock().unwrap().insert("a".into(), sender_a);
        pending.lock().unwrap().insert("b".into(), sender_b);
        let response = json!({"id": "b", "type": "response", "ok": true});
        assert!(route_line(response, &pending).is_none());
        assert!(receiver_a.try_recv().is_err());
        let delivered = receiver_b.try_recv().unwrap();
        assert_eq!(delivered["id"], "b");
        // The resolved request is removed from the pending map.
        assert!(!pending.lock().unwrap().contains_key("b"));
        assert!(pending.lock().unwrap().contains_key("a"));
    }

    #[test]
    fn a_late_response_for_an_unknown_request_is_dropped() {
        let pending: Arc<Mutex<HashMap<String, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let response = json!({"id": "gone", "type": "response", "ok": true});
        assert!(route_line(response, &pending).is_none());
        assert!(pending.lock().unwrap().is_empty());
    }

    #[test]
    fn main_is_not_revealed_until_both_halves_are_ready() {
        let revealed = AtomicBool::new(false);
        // Frontend first: the engine is still starting.
        assert!(!reveal_once(false, true, &revealed));
        assert!(!revealed.load(Ordering::SeqCst));
        // Engine first: the frontend is still loading.
        assert!(!reveal_once(true, false, &revealed));
        assert!(!revealed.load(Ordering::SeqCst));
        // Both ready: reveal exactly once.
        assert!(reveal_once(true, true, &revealed));
        assert!(revealed.load(Ordering::SeqCst));
    }

    #[test]
    fn ready_is_idempotent() {
        let revealed = AtomicBool::new(false);
        assert!(reveal_once(true, true, &revealed));
        // A second readiness signal must never reveal the window again.
        assert!(!reveal_once(true, true, &revealed));
        assert!(!reveal_once(true, true, &revealed));
    }

    #[test]
    fn startup_states_have_stable_names() {
        assert_eq!(StartupState::Starting.as_str(), "STARTING");
        assert_eq!(StartupState::SplashReady.as_str(), "SPLASH_READY");
        assert_eq!(StartupState::EngineStarting.as_str(), "ENGINE_STARTING");
        assert_eq!(StartupState::EngineReady.as_str(), "ENGINE_READY");
        assert_eq!(StartupState::FrontendLoading.as_str(), "FRONTEND_LOADING");
        assert_eq!(StartupState::FrontendReady.as_str(), "FRONTEND_READY");
        assert_eq!(StartupState::Ready.as_str(), "READY");
        assert_eq!(StartupState::Failed.as_str(), "FAILED");
    }

    #[test]
    fn pending_state_tracks_whichever_half_is_still_missing() {
        // Engine up, frontend still loading.
        assert_eq!(pending_state(true, false), StartupState::FrontendLoading);
        // Frontend up, engine still starting.
        assert_eq!(pending_state(false, true), StartupState::EngineStarting);
        // Neither half ready yet.
        assert_eq!(pending_state(false, false), StartupState::Starting);
    }

    #[test]
    fn frontend_before_engine_still_reveals_once_engine_arrives() {
        // Case 2: the React shell signals first, the engine answers later.
        let revealed = AtomicBool::new(false);
        assert!(!reveal_once(false, true, &revealed));
        assert_eq!(pending_state(false, true), StartupState::EngineStarting);
        assert!(reveal_once(true, true, &revealed));
        assert!(revealed.load(Ordering::SeqCst));
    }

    #[test]
    fn engine_before_frontend_still_reveals_once_frontend_arrives() {
        // Case 3: the engine answers first, the shell signals later.
        let revealed = AtomicBool::new(false);
        assert!(!reveal_once(true, false, &revealed));
        assert_eq!(pending_state(true, false), StartupState::FrontendLoading);
        assert!(reveal_once(true, true, &revealed));
        assert!(revealed.load(Ordering::SeqCst));
    }

    #[test]
    fn a_failed_startup_never_reveals_the_main_window() {
        // Case 5/6: the engine never becomes ready, so the main window must
        // stay hidden no matter how many times the frontend reports ready.
        let revealed = AtomicBool::new(false);
        for _ in 0..5 {
            assert!(!reveal_once(false, true, &revealed));
        }
        assert!(!revealed.load(Ordering::SeqCst));
    }

    #[test]
    fn retry_resets_the_reveal_guard_so_a_second_attempt_can_succeed() {
        // Case 7/8: a retry clears the flags; the second attempt reveals once
        // and only once, so no duplicate window is ever shown.
        let revealed = AtomicBool::new(false);
        assert!(!reveal_once(false, true, &revealed));
        // Retry: the coordinator resets the guard.
        revealed.store(false, Ordering::SeqCst);
        assert!(reveal_once(true, true, &revealed));
        assert!(!reveal_once(true, true, &revealed));
    }

    #[test]
    fn a_slow_engine_trips_the_watchdog_only_after_the_deadline() {
        // Case 4: the engine is slow but not failed. The watchdog must stay
        // quiet until the startup timeout elapses, then fail exactly once.
        assert!(!watchdog_should_fail(
            false,
            StartupState::EngineStarting,
            STARTUP_TIMEOUT - Duration::from_millis(1)
        ));
        assert!(watchdog_should_fail(
            false,
            StartupState::EngineStarting,
            STARTUP_TIMEOUT
        ));
    }

    #[test]
    fn a_frontend_that_never_signals_also_trips_the_watchdog() {
        // Case 13: the engine is ready but the React shell never reports
        // readiness (a controlled frontend failure). The startup must fail
        // recoverably instead of leaving the splash open forever.
        assert!(!watchdog_should_fail(
            false,
            StartupState::FrontendLoading,
            STARTUP_TIMEOUT - Duration::from_millis(1)
        ));
        assert!(watchdog_should_fail(
            false,
            StartupState::FrontendLoading,
            STARTUP_TIMEOUT
        ));
    }

    #[test]
    fn the_watchdog_never_fails_a_revealed_or_already_failed_startup() {
        // A revealed startup must never be reported as a timeout, and a
        // startup that already failed must not be failed twice.
        assert!(!watchdog_should_fail(
            true,
            StartupState::Ready,
            STARTUP_TIMEOUT * 2
        ));
        assert!(!watchdog_should_fail(
            false,
            StartupState::Failed,
            STARTUP_TIMEOUT * 2
        ));
    }

    #[test]
    fn a_retry_after_a_failure_can_still_reveal_the_window() {
        // Case 7: the first attempt fails (engine never ready), the retry
        // resets every flag and the second attempt succeeds exactly once.
        let revealed = AtomicBool::new(false);
        let engine_ready = AtomicBool::new(false);
        let frontend_ready = AtomicBool::new(false);
        // First attempt: frontend ready, engine failed.
        frontend_ready.store(true, Ordering::SeqCst);
        assert!(!reveal_once(
            engine_ready.load(Ordering::SeqCst),
            frontend_ready.load(Ordering::SeqCst),
            &revealed
        ));
        // Retry resets the coordinator.
        engine_ready.store(false, Ordering::SeqCst);
        frontend_ready.store(false, Ordering::SeqCst);
        revealed.store(false, Ordering::SeqCst);
        // Second attempt: both halves ready.
        engine_ready.store(true, Ordering::SeqCst);
        frontend_ready.store(true, Ordering::SeqCst);
        assert!(reveal_once(true, true, &revealed));
        assert!(!reveal_once(true, true, &revealed));
    }

    #[test]
    fn two_retries_never_reveal_the_window_twice() {
        // Case 8: even after two retries the reveal guard admits exactly one
        // reveal, so no duplicate main window is ever created.
        let revealed = AtomicBool::new(false);
        for _ in 0..2 {
            revealed.store(false, Ordering::SeqCst);
            assert!(reveal_once(true, true, &revealed));
            assert!(!reveal_once(true, true, &revealed));
        }
    }

    #[test]
    fn the_splash_is_closed_only_after_the_main_window_is_shown() {
        // Case 12: the reveal plan shows and focuses the main window before
        // closing the splash, so there is never an empty desktop between them.
        let plan = reveal_plan();
        let show = plan.iter().position(|a| *a == RevealAction::ShowMain).unwrap();
        let close = plan.iter().position(|a| *a == RevealAction::CloseSplash).unwrap();
        assert!(show < close, "main must be shown before the splash closes");
        // The ready event is emitted last, once the window is already visible.
        let emit = plan.iter().position(|a| *a == RevealAction::EmitReady).unwrap();
        assert!(close < emit, "ready is emitted after the splash closes");
    }

    #[test]
    fn stopping_the_manager_terminates_the_sidecar_process() {
        // Cases 9 and 14: closing from the splash and shutting down during
        // startup both funnel through `SidecarManager::stop` (the `RunEvent`
        // handler). It must actually terminate the Python process so no orphan
        // is left behind, and a later `ensure` must start a fresh one.
        let resource_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let sink: ProgressSink = Arc::new(|_| {});
        let manager = SidecarManager::new(resource_dir, sink);
        let process = match manager.ensure() {
            Ok(process) => process,
            // No sidecar available in this environment: nothing to assert.
            Err(_) => return,
        };
        assert!(process.is_alive(), "the sidecar should be running after ensure");
        manager.stop();
        assert!(!process.is_alive(), "stop must terminate the sidecar process");
        // A stopped manager is not poisoned: the next request starts a new
        // process instead of reusing the dead one.
        let restarted = manager.ensure().expect("ensure after stop should restart");
        assert!(restarted.is_alive(), "ensure must start a fresh sidecar");
        manager.stop();
    }
}
