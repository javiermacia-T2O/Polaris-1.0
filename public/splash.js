// The splash talks to Rust through the global Tauri bridge, so it needs no
// bundler and no npm dependency. Every milestone below is a real event emitted
// by the StartupCoordinator; nothing advances on a timer.
//
// This lives in its own file (instead of an inline <script>) because the app
// CSP is `default-src 'self'`, which blocks inline scripts. An inline script
// would never run, so the splash would sit on "Iniciando Polaris…" forever.
(function () {
  var tauri = window.__TAURI__;
  var invoke = tauri && tauri.core && tauri.core.invoke;
  var listen = tauri && tauri.event && tauri.event.listen;
  var msg = document.getElementById("msg");
  var failure = document.getElementById("failure");
  var failureMsg = document.getElementById("failure-msg");
  var details = document.getElementById("details");
  var steps = document.getElementById("steps");
  var hintTimer = null;
  var hintIndex = 0;
  var hints = ["Preparando Polaris…", "Comprobando los componentes locales…", "Iniciando el entorno de trabajo…"];

  function startHints(nextHints, immediate) {
    hints = nextHints;
    hintIndex = 0;
    if (immediate) msg.textContent = immediate;
    if (hintTimer) window.clearInterval(hintTimer);
    hintTimer = window.setInterval(function () {
      msg.textContent = hints[hintIndex % hints.length];
      hintIndex += 1;
    }, 1650);
  }

  function stopHints(finalMessage) {
    if (hintTimer) window.clearInterval(hintTimer);
    hintTimer = null;
    if (finalMessage) msg.textContent = finalMessage;
  }

  function markStep(name, state) {
    var dot = steps.querySelector('[data-step="' + name + '"]');
    if (dot) dot.className = state;
  }

  function showFailure(message) {
    stopHints();
    document.querySelector(".bar").style.display = "none";
    steps.style.display = "none";
    msg.style.display = "none";
    failure.style.display = "flex";
    failureMsg.textContent = message || "Polaris no pudo preparar el servicio de análisis.";
    details.textContent = message || "";
  }

  if (!invoke) {
    // Without the bridge there is nothing to coordinate; keep the splash
    // honest instead of pretending to progress.
    showFailure("No se pudo conectar con el proceso principal.");
    return;
  }

  // Register the listeners *before* signalling readiness: the engine may
  // already be up, and Rust emits `engine_starting`/`engine_ready` as soon as
  // the prewarm thread runs. Listening first means no milestone is missed.
  if (listen) {
    listen("startup://stage", function (event) {
      var payload = event.payload || {};
      if (payload.stage === "engine_starting") {
        markStep("engine", "active");
        startHints(["Cargando el motor analítico…", "Preparando el procesamiento local…", "Optimizando recursos del equipo…"], payload.message);
      }
      if (payload.stage === "engine_ready") {
        markStep("engine", "done");
        markStep("frontend", "active");
        startHints(["Preparando la interfaz…", "Organizando el espacio de trabajo…", "Conectando datos y análisis…"], payload.message);
      }
      if (payload.stage === "frontend_ready") {
        markStep("frontend", "done");
        markStep("ready", "active");
        startHints(["Ultimando los detalles…", "Comprobando la vista inicial…", "Polaris está casi listo…"], payload.message);
      }
      if (payload.stage === "ready") {
        markStep("ready", "done");
        stopHints(payload.message || "Polaris listo");
      }
    });
    listen("startup://failed", function (event) {
      showFailure((event.payload || {}).message);
    });
  }

  // Tell Rust the splash painted, so the coordinator records the first real
  // milestone.
  markStep("engine", "active");
  startHints(hints, "Iniciando Polaris…");
  invoke("splash_ready").catch(function () {});

  document.getElementById("retry").addEventListener("click", function () {
    failure.style.display = "none";
    document.querySelector(".bar").style.display = "block";
    steps.style.display = "flex";
    msg.style.display = "block";
    startHints(["Reintentando el inicio…", "Comprobando el motor local…", "Restableciendo la conexión…"], "Reintentando…");
    markStep("engine", "active");
    markStep("frontend", "");
    markStep("ready", "");
    invoke("retry_startup").catch(function () {});
  });
  document.getElementById("details-toggle").addEventListener("click", function () {
    details.style.display = details.style.display === "block" ? "none" : "block";
  });
  document.getElementById("quit").addEventListener("click", function () {
    invoke("quit_app").catch(function () {});
  });
})();
