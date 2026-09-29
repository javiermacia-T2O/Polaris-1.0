use rand::{distr::Alphanumeric, Rng};
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::Duration,
};
use tauri::{Manager, State};
use thiserror::Error;

const RESPONSE_TIMEOUT: Duration = Duration::from_secs(120);

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
    #[error("La respuesta del sidecar no es JSON válido: {0}")]
    InvalidResponse(String),
}

struct SidecarProcess {
    child: Child,
    stdin: ChildStdin,
    responses: mpsc::Receiver<String>,
    token: String,
    sequence: u64,
}

impl SidecarProcess {
    fn start(resource_dir: &Path) -> Result<Self, SidecarProcessError> {
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
        let (sender, responses) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(value) => {
                        if sender.send(value).is_err() {
                            break;
                        }
                    },
                    
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            child,
            stdin,
            responses,
            token,
            sequence: 0,
        })
    }

    fn request(&mut self, operation: &str, params: Value) -> Result<Value, SidecarProcessError> {
        if self
            .child
            .try_wait()
            .map_err(|error| SidecarProcessError::Start(error.to_string()))?
            .is_some()
        {
            return Err(SidecarProcessError::Disconnected);
        }
        self.sequence += 1;
        let id = self.sequence.to_string();
        let request = json!({
            "id": id,
            "token": self.token,
            "operation": operation,
            "params": params
        });
        writeln!(self.stdin, "{request}")
            .and_then(|_| self.stdin.flush())
            .map_err(|error| SidecarProcessError::Write(error.to_string()))?;
        let line = self
            .responses
            .recv_timeout(RESPONSE_TIMEOUT)
            .map_err(|error| {
                if matches!(error, mpsc::RecvTimeoutError::Timeout) {
                    SidecarProcessError::Timeout
                } else {
                    SidecarProcessError::Disconnected
                }
            })?;
        serde_json::from_str(&line)
            .map_err(|error| SidecarProcessError::InvalidResponse(error.to_string()))
    }

    fn stop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for SidecarProcess {
    fn drop(&mut self) {
        self.stop();
    }
}

struct SidecarManager {
    process: Mutex<Option<SidecarProcess>>,
    resource_dir: PathBuf,
}

impl SidecarManager {
    fn request(&self, operation: &str, params: Value) -> Result<Value, String> {
        let mut process = self
            .process
            .lock()
            .map_err(|_| "Bloqueo del sidecar dañado")?;
        if process.is_none() {
            *process =
                Some(SidecarProcess::start(&self.resource_dir).map_err(|error| error.to_string())?);
        }
        let sidecar = process.as_mut().expect("sidecar initialized");
        match sidecar.request(operation, params.clone()) {
            Ok(response) => Ok(response),
            Err(SidecarProcessError::Disconnected) => {
                *process = Some(
                    SidecarProcess::start(&self.resource_dir).map_err(|error| error.to_string())?,
                );
                process
                    .as_mut()
                    .expect("sidecar restarted")
                    .request(operation, params)
                    .map_err(|error| error.to_string())
            }
            Err(error) => Err(error.to_string()),
        }
    }

    fn stop(&self) {
        if let Ok(mut guard) = self.process.lock() {
            if let Some(process) = guard.as_mut() {
                process.stop();
            }
            *guard = None;
        }
    }
}

#[tauri::command]
fn sidecar_request(
    state: State<'_, SidecarManager>,
    operation: String,
    params: Value,
) -> Result<Value, String> {
    state.request(&operation, params)
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
            app.manage(SidecarManager {
                process: Mutex::new(None),
                resource_dir,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_request,
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
                app.state::<SidecarManager>().stop();
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
}
