use rand::{distr::Alphanumeric, Rng};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicU64, Ordering}, mpsc, Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, State};
use thiserror::Error;

// Leave admission capacity for cancellation and health checks under heavy load.
const MAX_PENDING: usize = 64;
const CONTROL_RESERVE: usize = 8;
const MAX_EARLY_CANCELLATIONS: usize = MAX_PENDING * 4;

// Cancellation can arrive before spawn_blocking registers a caller. Retain a
// bounded set of tombstones; requests are UUID-based and never intentionally
// reused by the frontend. Expiry also bounds stale entries in idle sessions.
#[derive(Default)]
struct EarlyCancellations(HashMap<String, Instant>);

impl EarlyCancellations {
    fn remember(&mut self, id: &str) {
        self.expire();
        if !self.0.contains_key(id) && self.0.len() >= MAX_EARLY_CANCELLATIONS {
            if let Some(oldest) = self.0.iter().min_by_key(|(_, time)| **time)
                .map(|(id, _)| id.clone()) {
                self.0.remove(&oldest);
            }
        }
        self.0.insert(id.to_owned(), Instant::now());
    }

    fn take(&mut self, id: &str) -> bool {
        self.expire();
        self.0.remove(id).is_some()
    }

    fn expire(&mut self) {
        self.0.retain(|_, time| time.elapsed() < Duration::from_secs(300));
    }
}

#[derive(Debug, Error, Clone)]
enum SidecarProcessError {
    #[error("No se encontró el sidecar Python")]
    Missing,
    #[error("No se pudo iniciar el sidecar: {0}")]
    Start(String),
    #[error("El sidecar cerró su canal de respuesta")]
    Disconnected,
    #[error("El sidecar no respondió dentro del tiempo permitido")]
    Timeout,
    #[error("Ya hay una solicitud pendiente con ese identificador")]
    DuplicateId,
    #[error("El sidecar tiene demasiadas solicitudes pendientes")]
    Busy,
    #[error("Solicitud cancelada")]
    Cancelled,
    #[error("La aplicación se está cerrando")]
    ShuttingDown,
}

type ResponseSender = mpsc::Sender<Result<Value, SidecarProcessError>>;

#[derive(Default)]
struct PendingRequests {
    closed: bool,
    requests: HashMap<String, ResponseSender>,
    callers: HashMap<String, String>,
    early_cancellations: EarlyCancellations,
}

type Pending = Arc<Mutex<PendingRequests>>;

// All terminal paths share this lock with registration. A request can never be
// inserted after EOF has drained the map and then wait until its timeout.
fn disconnect(pending: &Pending) {
    let mut state = pending.lock().unwrap_or_else(|error| error.into_inner());
    state.closed = true;
    state.callers.clear();
    for (_, sender) in state.requests.drain() {
        let _ = sender.send(Err(SidecarProcessError::Disconnected));
    }
}

fn operation_policy(operation: &str) -> (&'static str, Duration) {
    match operation {
        "health" => ("control", Duration::from_secs(15)),
        "cancel_job" | "get_job_status" => ("control", Duration::from_secs(10)),
        "load_dataset" | "build_table" | "merge_datasets" |
        "export_dataset" | "export_result" | "get_column_profile" |
        "get_diagnostics" => ("heavy", Duration::from_secs(300)),
        _ => ("interactive", Duration::from_secs(30)),
    }
}

struct SidecarProcess {
    child: Mutex<Child>,
    writer: mpsc::SyncSender<Value>,
    pending: Pending,
    token: String,
    sequence: AtomicU64,
}

impl SidecarProcess {
    fn start(resource_dir: &Path) -> Result<Arc<Self>, SidecarProcessError> {
        let token: String = rand::rng().sample_iter(&Alphanumeric)
            .take(48).map(char::from).collect();
        let command = sidecar_command(resource_dir, &token)?;
        Self::spawn(command, token)
    }

    fn spawn(mut command: Command, token: String) -> Result<Arc<Self>, SidecarProcessError> {
        let mut child = command.stdin(Stdio::piped()).stdout(Stdio::piped())
            .stderr(Stdio::inherit()).creation_flags(0x08000000).spawn()
            .map_err(|error| SidecarProcessError::Start(error.to_string()))?;
        let Some(mut stdin) = child.stdin.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(SidecarProcessError::Start("stdin no disponible".into()));
        };
        let Some(stdout) = child.stdout.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(SidecarProcessError::Start("stdout no disponible".into()));
        };
        let pending: Pending = Arc::new(Mutex::new(PendingRequests::default()));
        let readers = Arc::clone(&pending);
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(response) = serde_json::from_str::<Value>(&line) else { continue };
                // Progress/events may share a request ID, but must not resolve it.
                if !response.get("ok").is_some_and(Value::is_boolean) { continue; }
                let Some(id) = response.get("id").and_then(Value::as_str) else { continue };
                let sender = readers.lock().unwrap_or_else(|error| error.into_inner())
                    .requests.remove(id);
                if let Some(sender) = sender { let _ = sender.send(Ok(response)); }
            }
            disconnect(&readers);
        });
        // Pipe writes may block if a wedged child stops reading. Keep them off
        // Tauri and request threads so deadlines and process termination work.
        let (writer, messages) = mpsc::sync_channel::<Value>(MAX_PENDING + CONTROL_RESERVE);
        let writers = Arc::clone(&pending);
        thread::spawn(move || {
            while let Ok(request) = messages.recv() {
                if writers.lock().unwrap_or_else(|error| error.into_inner()).closed { break; }
                if writeln!(stdin, "{request}").and_then(|_| stdin.flush()).is_err() {
                    disconnect(&writers);
                    break;
                }
            }
        });
        Ok(Arc::new(Self { child: Mutex::new(child), writer,
            pending, token, sequence: AtomicU64::new(0) }))
    }

    fn is_closed(&self) -> bool {
        self.pending.lock().unwrap_or_else(|error| error.into_inner()).closed
    }

    fn send(&self, request: Value) -> Result<(), SidecarProcessError> {
        self.writer.try_send(request).map_err(|error| match error {
            mpsc::TrySendError::Full(_) => SidecarProcessError::Busy,
            mpsc::TrySendError::Disconnected(_) => SidecarProcessError::Disconnected,
        })
    }

    fn request(&self, operation: &str, params: Value, request_id: Option<String>) -> Result<Value, SidecarProcessError> {
        self.request_with_timeout(operation, params, request_id, operation_policy(operation).1)
    }

    fn request_with_timeout(&self, operation: &str, params: Value, request_id: Option<String>, timeout: Duration) -> Result<Value, SidecarProcessError> {
        // Use a fresh wire ID even if a caller reuses its ID after a timeout;
        // a late reply can never resolve a later request with that caller ID.
        let id = format!("rust-{}", self.sequence.fetch_add(1, Ordering::Relaxed));
        let caller_id = request_id.unwrap_or_else(|| id.clone());
        let priority = operation_policy(operation).0;
        let request = json!({ "id": id, "token": self.token,
            "type": "request", "operation": operation, "params": params,
            "priority": priority });
        let (sender, receiver) = mpsc::channel();
        {
            let mut state = self.pending.lock().unwrap_or_else(|error| error.into_inner());
            if state.closed { return Err(SidecarProcessError::Disconnected); }
            if state.early_cancellations.take(&caller_id) { return Err(SidecarProcessError::Cancelled); }
            if state.callers.contains_key(&caller_id) { return Err(SidecarProcessError::DuplicateId); }
            let limit = MAX_PENDING + if priority == "control" { CONTROL_RESERVE } else { 0 };
            if state.requests.len() >= limit { return Err(SidecarProcessError::Busy); }
            state.callers.insert(caller_id.clone(), id.clone());
            state.requests.insert(id.clone(), sender);
            // Queue while registration is locked so cancellation cannot overtake
            // the original request between registration and transmission.
            if let Err(error) = self.send(request) {
                state.requests.remove(&id);
                state.callers.remove(&caller_id);
                return Err(error);
            }
        }
        let answer = receiver.recv_timeout(timeout);
        self.remove_pending(&caller_id, &id);
        match answer {
            Ok(Ok(mut response)) => {
                response["id"] = Value::String(caller_id);
                Ok(response)
            }
            Ok(Err(error)) => Err(error),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(SidecarProcessError::Disconnected),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let _ = self.cancel_wire(&id);
                Err(SidecarProcessError::Timeout)
            }
        }
    }

    fn remove_pending(&self, caller_id: &str, wire_id: &str) {
        let mut state = self.pending.lock().unwrap_or_else(|error| error.into_inner());
        state.requests.remove(wire_id);
        if state.callers.get(caller_id).is_some_and(|current| current == wire_id) {
            state.callers.remove(caller_id);
        }
    }

    fn cancel(&self, target_id: &str) -> Result<(), SidecarProcessError> {
        let wire_id = {
            let mut state = self.pending.lock().unwrap_or_else(|error| error.into_inner());
            if let Some(wire_id) = state.callers.get(target_id) {
                Some(wire_id.clone())
            } else {
                state.early_cancellations.remember(target_id);
                None
            }
        };
        if let Some(wire_id) = wire_id { self.cancel_wire(&wire_id) } else { Ok(()) }
    }

    fn cancel_wire(&self, target_id: &str) -> Result<(), SidecarProcessError> {
        if self.is_closed() { return Err(SidecarProcessError::Disconnected); }
        let id = format!("rust-cancel-{}", self.sequence.fetch_add(1, Ordering::Relaxed));
        self.send(json!({"id": id, "token": self.token,
            "type": "cancel", "target_id": target_id}))
    }

    fn stop(&self) {
        disconnect(&self.pending);
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for SidecarProcess {
    fn drop(&mut self) { self.stop(); }
}

#[derive(Default)]
struct ManagerState {
    process: Option<Arc<SidecarProcess>>,
    shutting_down: bool,
    early_cancellations: EarlyCancellations,
}

struct SidecarManager {
    state: Mutex<ManagerState>,
    resource_dir: PathBuf,
}

impl SidecarManager {
    fn request(&self, operation: &str, params: Value, request_id: Option<String>) -> Result<Value, String> {
        let process = {
            let mut guard = self.state.lock().map_err(|_| "Bloqueo del sidecar dañado")?;
            if guard.shutting_down { return Err(SidecarProcessError::ShuttingDown.to_string()); }
            if request_id.as_ref().is_some_and(|id| guard.early_cancellations.take(id)) {
                return Err(SidecarProcessError::Cancelled.to_string());
            }
            if guard.process.as_ref().is_some_and(|process| process.is_closed()) {
                if let Some(stale) = guard.process.take() { stale.stop(); }
            }
            if guard.process.is_none() {
                guard.process = Some(SidecarProcess::start(&self.resource_dir)
                    .map_err(|error| error.to_string())?);
            }
            Arc::clone(guard.process.as_ref().expect("sidecar initialized"))
        };
        match process.request(operation, params, request_id) {
            Ok(response) => Ok(response),
            Err(error) => {
                // A slow operation alone is not proof of a dead transport. Ask
                // the independent control lane before invalidating the session.
                let unavailable = matches!(error, SidecarProcessError::Disconnected)
                    || (matches!(error, SidecarProcessError::Timeout)
                        && (operation == "health" || process.request_with_timeout("health", json!({}), None,
                            Duration::from_secs(3)).map_or(true, |reply| reply.get("ok") != Some(&Value::Bool(true)))));
                if unavailable { self.invalidate(&process); }
                // Never replay mutations automatically after an uncertain result.
                Err(error.to_string())
            }
        }
    }

    fn invalidate(&self, process: &Arc<SidecarProcess>) {
        let removed = {
            let mut guard = self.state.lock().unwrap_or_else(|error| error.into_inner());
            if guard.process.as_ref().is_some_and(|current| Arc::ptr_eq(current, process)) {
                guard.process.take()
            } else { None }
        };
        if let Some(process) = removed { process.stop(); }
    }

    fn cancel(&self, target_id: &str) -> Result<(), String> {
        let process = {
            let mut state = self.state.lock().map_err(|_| "Bloqueo del sidecar dañado")?;
            if state.shutting_down { return Ok(()); }
            if let Some(process) = state.process.as_ref().filter(|process| !process.is_closed()) {
                Some(Arc::clone(process))
            } else {
                state.early_cancellations.remember(target_id);
                None
            }
        };
        if let Some(process) = process {
            process.cancel(target_id).map_err(|error| error.to_string())
        } else { Ok(()) }
    }

    fn stop(&self) {
        let process = {
            let mut guard = self.state.lock().unwrap_or_else(|error| error.into_inner());
            guard.shutting_down = true;
            guard.process.take()
        };
        if let Some(process) = process { process.stop(); }
    }
}

#[tauri::command]
async fn sidecar_request(
    state: State<'_, Arc<SidecarManager>>, operation: String, params: Value,
    request_id: Option<String>,
) -> Result<Value, String> {
    let manager = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || manager.request(&operation, params, request_id))
        .await.map_err(|error| error.to_string())?
}

#[tauri::command]
fn sidecar_cancel(state: State<'_, Arc<SidecarManager>>, request_id: String) -> Result<(), String> {
    state.cancel(&request_id)
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
    tauri::Builder::default()
        .setup(|app| {
            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."));
            app.manage(Arc::new(SidecarManager {
                state: Mutex::new(ManagerState::default()),
                resource_dir,
            }));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_request,
            sidecar_cancel,
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

    // Reuse the test executable as a deterministic child process. This exercises
    // real pipes, EOF, kill/reap, and out-of-order replies without Python or GUI.
    #[test]
    #[ignore]
    fn ipc_child_fixture() {
        if std::env::var_os("POLARIS_IPC_TEST_CHILD").is_none() { return; }
        println!(); // Separate protocol lines from the Rust test harness prefix.
        std::io::stdout().flush().unwrap();
        for line in std::io::stdin().lock().lines() {
            let request: Value = serde_json::from_str(&line.unwrap()).unwrap();
            let id = request["id"].clone();
            if request["type"] == "cancel" {
                println!("{}", json!({"id": request["target_id"], "ok": false,
                    "error": {"code": "cancelled"}}));
                continue;
            }
            if request["operation"] == "crash" { std::process::exit(17); }
            if request["operation"] == "hang" {
                thread::sleep(Duration::from_secs(60));
                continue;
            }
            thread::spawn(move || {
                let delay = request["params"]["delay_ms"].as_u64().unwrap_or(0);
                thread::sleep(Duration::from_millis(delay));
                // An event with an ID must never steal the response channel.
                println!("{}", json!({"id": id, "type": "progress", "progress": 0.5}));
                println!("{}", json!({"id": id, "ok": true, "result": request["params"]}));
            });
        }
    }

    fn fixture() -> Arc<SidecarProcess> {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command.args(["--exact", "tests::ipc_child_fixture", "--ignored", "--nocapture", "--test-threads=1"])
            .env("POLARIS_IPC_TEST_CHILD", "1");
        SidecarProcess::spawn(command, "test-token".into()).unwrap()
    }

    fn await_pending(process: &SidecarProcess, caller_id: &str) {
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if process.pending.lock().unwrap().callers.contains_key(caller_id) { return; }
            assert!(Instant::now() < deadline, "request was not registered");
            thread::sleep(Duration::from_millis(2));
        }
    }

    #[test]
    fn concurrent_responses_are_correlated_and_events_are_ignored() {
        let process = fixture();
        let slow_process = Arc::clone(&process);
        let slow = thread::spawn(move || slow_process.request("echo",
            json!({"delay_ms": 500, "value": "slow"}), Some("slow".into())).unwrap());
        await_pending(&process, "slow");
        let fast = process.request("health", json!({"value": "fast"}), Some("fast".into())).unwrap();
        assert_eq!(fast["result"]["value"], "fast");
        assert!(!slow.is_finished(), "health waited for the slow request");
        assert_eq!(slow.join().unwrap()["result"]["value"], "slow");
        assert!(process.pending.lock().unwrap().requests.is_empty());
        assert!(process.pending.lock().unwrap().callers.is_empty());
        process.stop();
    }

    #[test]
    fn duplicate_caller_ids_do_not_replace_waiters_and_cancel_targets_wire_id() {
        let process = fixture();
        let worker = Arc::clone(&process);
        let slow = thread::spawn(move || worker.request("echo",
            json!({"delay_ms": 1000}), Some("same".into())).unwrap());
        await_pending(&process, "same");
        assert!(matches!(process.request("echo", json!({}), Some("same".into())),
            Err(SidecarProcessError::DuplicateId)));
        process.cancel("same").unwrap();
        let response = slow.join().unwrap();
        assert_eq!(response["id"], "same");
        assert_eq!(response["error"]["code"], "cancelled");
        process.stop();
    }

    #[test]
    fn timeout_removes_waiter_and_late_reply_cannot_resolve_reused_id() {
        let process = fixture();
        // Warm startup before using short deadlines.
        process.request("health", json!({}), None).unwrap();
        assert!(matches!(process.request_with_timeout("echo", json!({"delay_ms": 150, "value": "old"}),
            Some("reused".into()), Duration::from_millis(20)), Err(SidecarProcessError::Timeout)));
        let response = process.request("echo", json!({"delay_ms": 250, "value": "new"}),
            Some("reused".into())).unwrap();
        assert_eq!(response["result"]["value"], "new");
        assert!(process.pending.lock().unwrap().requests.is_empty());
        process.stop();
    }

    #[test]
    fn crash_drains_all_waiters_and_rejects_new_requests_immediately() {
        let process = fixture();
        let worker = Arc::clone(&process);
        let slow = thread::spawn(move || worker.request("echo",
            json!({"delay_ms": 10000}), Some("slow".into())));
        await_pending(&process, "slow");
        assert!(matches!(process.request("crash", json!({}), None), Err(SidecarProcessError::Disconnected)));
        assert!(matches!(slow.join().unwrap(), Err(SidecarProcessError::Disconnected)));
        assert!(matches!(process.request("health", json!({}), None), Err(SidecarProcessError::Disconnected)));
        process.stop();
        assert!(process.child.lock().unwrap().try_wait().unwrap().is_some());
    }

    #[test]
    fn stale_failure_cannot_remove_replacement_and_shutdown_prevents_restart() {
        let old = fixture();
        let replacement = fixture();
        let manager = SidecarManager { state: Mutex::new(ManagerState {
            process: Some(Arc::clone(&replacement)), ..ManagerState::default()
        }), resource_dir: PathBuf::from(".") };
        manager.invalidate(&old);
        assert!(Arc::ptr_eq(manager.state.lock().unwrap().process.as_ref().unwrap(), &replacement));
        manager.request("health", json!({}), None).unwrap();
        manager.stop();
        assert!(manager.request("health", json!({}), None).unwrap_err().contains("cerrando"));
        assert!(replacement.child.lock().unwrap().try_wait().unwrap().is_some());
        old.stop();
    }

    #[test]
    fn cancellation_before_process_creation_prevents_spawn() {
        let manager = SidecarManager { state: Mutex::new(ManagerState::default()),
            resource_dir: PathBuf::from("missing-sidecar") };
        manager.cancel("early").unwrap();
        assert_eq!(manager.request("echo", json!({}), Some("early".into())).unwrap_err(),
            SidecarProcessError::Cancelled.to_string());
        assert!(manager.state.lock().unwrap().process.is_none());
    }

    #[test]
    fn cancellation_after_process_selection_prevents_registration() {
        let process = fixture();
        let manager = SidecarManager { state: Mutex::new(ManagerState {
            process: Some(Arc::clone(&process)), ..ManagerState::default()
        }), resource_dir: PathBuf::from(".") };
        // Deterministically pause at the seam between manager process selection
        // and process request registration, then deliver the frontend cancel.
        let selected = Arc::clone(manager.state.lock().unwrap().process.as_ref().unwrap());
        manager.cancel("early").unwrap();
        assert!(matches!(selected.request("echo", json!({}), Some("early".into())),
            Err(SidecarProcessError::Cancelled)));
        assert!(process.pending.lock().unwrap().requests.is_empty());
        manager.stop();
    }

    #[test]
    fn early_cancellation_storage_is_bounded_and_expires() {
        let mut cancellations = EarlyCancellations::default();
        for id in 0..(MAX_EARLY_CANCELLATIONS * 2) { cancellations.remember(&id.to_string()); }
        assert_eq!(cancellations.0.len(), MAX_EARLY_CANCELLATIONS);
        cancellations.0.insert("expired".into(), Instant::now() - Duration::from_secs(301));
        assert!(!cancellations.take("expired"));
        let latest = (MAX_EARLY_CANCELLATIONS * 2 - 1).to_string();
        assert!(cancellations.take(&latest));
        assert!(!cancellations.take(&latest));
    }

    #[test]
    fn timeout_policies_allow_heavy_work_and_keep_control_fast() {
        assert!(operation_policy("health").1 < operation_policy("get_table_page").1);
        assert!(operation_policy("get_table_page").1 < operation_policy("export_dataset").1);
        assert_eq!(operation_policy("get_job_status").0, "control");
    }
}
