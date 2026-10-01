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

  function markStep(name, state) {
    var dot = steps.querySelector('[data-step="' + name + '"]');
    if (dot) dot.className = state;
  }

  function showFailure(message) {
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
      if (payload.message) msg.textContent = payload.message;
      if (payload.stage === "engine_starting") markStep("engine", "active");
      if (payload.stage === "engine_ready") markStep("engine", "done");
      if (payload.stage === "frontend_ready") markStep("frontend", "done");
      if (payload.stage === "ready") markStep("ready", "done");
    });
    listen("startup://failed", function (event) {
      showFailure((event.payload || {}).message);
    });
  }

  // Tell Rust the splash painted, so the coordinator records the first real
  // milestone.
  invoke("splash_ready").catch(function () {});

  document.getElementById("retry").addEventListener("click", function () {
    failure.style.display = "none";
    document.querySelector(".bar").style.display = "block";
    steps.style.display = "flex";
    msg.style.display = "block";
    msg.textContent = "Reintentando…";
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