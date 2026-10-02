// The splash talks to Rust through the global Tauri bridge, so it needs no
// bundler and no npm dependency. Every milestone below is a real event emitted
// by the StartupCoordinator; the timer only animates understated helper copy.
//
// This lives in its own file (instead of an inline <script>) because the app
// CSP is `default-src 'self'`, which blocks inline scripts. An inline script
// would never run, so the splash would sit on "Iniciando Polaris…" forever.
(function () {
  var connectionAttempts = 12;
  var msg = document.getElementById("msg");
  var failure = document.getElementById("failure");
  var failureMsg = document.getElementById("failure-msg");
  var details = document.getElementById("details");
  var steps = document.getElementById("steps");
  var messageIndex = 0;
  var messages = [
    "Iniciando Polaris…",
    "Preparando el entorno local…",
    "Cargando los componentes…",
    "Organizando tu espacio de trabajo…"
  ];
  var messageTimer = null;

  function setMessage(text) {
    msg.textContent = text;
    msg.classList.remove("message-change");
    void msg.offsetWidth;
    msg.classList.add("message-change");
  }

  function startMessageCycle() {
    stopMessageCycle();
    messageTimer = window.setInterval(function () {
    messageIndex = (messageIndex + 1) % messages.length;
      setMessage(messages[messageIndex]);
    }, 2200);
  }

  function stopMessageCycle() {
    if (messageTimer !== null) window.clearInterval(messageTimer);
    messageTimer = null;
  }

  startMessageCycle();

  function markStep(name, state) {
    var dot = steps.querySelector('[data-step="' + name + '"]');
    if (dot) dot.className = state;
  }

  function showFailure(message) {
    stopMessageCycle();
    document.querySelector(".bar").style.display = "none";
    steps.style.display = "none";
    msg.style.display = "none";
    failure.style.display = "flex";
    failureMsg.textContent = message || "Polaris no pudo preparar el servicio de análisis.";
    details.textContent = message || "";
  }

  function retry(operation, attempt) {
    return Promise.resolve().then(operation).catch(function (error) {
      if (attempt >= connectionAttempts) throw error;
      var delay = Math.min(1000, 100 * Math.pow(2, attempt - 1));
      return new Promise(function (resolve) {
        window.setTimeout(resolve, delay);
      }).then(function () {
        return retry(operation, attempt + 1);
      });
    });
  }

  // Register the listeners *before* signalling readiness: the engine may
  // already be up, and Rust emits `engine_starting`/`engine_ready` as soon as
  // the prewarm thread runs. Listening first means no milestone is missed.
  function listenFor(eventName, callback) {
    return retry(function () {
      var listen = window.__TAURI__ && window.__TAURI__.event
        && window.__TAURI__.event.listen;
      if (!listen) throw new Error("Tauri event bridge is not ready");
      return listen(eventName, callback);
    }, 1);
  }

  var startupEvents = Promise.all([
    listenFor("startup://stage", function (event) {
      var payload = event.payload || {};
      if (payload.message) setMessage(payload.message);
      if (payload.stage === "engine_starting") markStep("engine", "active");
      if (payload.stage === "engine_ready") markStep("engine", "done");
      if (payload.stage === "frontend_ready") markStep("frontend", "done");
      if (payload.stage === "ready") markStep("ready", "done");
    }),
    listenFor("startup://failed", function (event) {
      showFailure((event.payload || {}).message);
    }),
    listenFor("startup://ready", stopMessageCycle)
  ]);

  startupEvents.then(function () {
    // Register every async Tauri listener before readiness can trigger events.
    return retry(function () {
      var invoke = window.__TAURI__ && window.__TAURI__.core
        && window.__TAURI__.core.invoke;
      if (!invoke) throw new Error("Tauri command bridge is not ready");
      return invoke("splash_ready");
    }, 1);
  }).catch(function () {
    showFailure("No se pudo conectar con el proceso principal.");
  });

  document.getElementById("retry").addEventListener("click", function () {
    failure.style.display = "none";
    document.querySelector(".bar").style.display = "block";
    steps.style.display = "flex";
    msg.style.display = "block";
    setMessage("Reintentando…");
    startMessageCycle();
    markStep("engine", "active");
    markStep("frontend", "");
    markStep("ready", "");
    var invoke = window.__TAURI__ && window.__TAURI__.core
      && window.__TAURI__.core.invoke;
    if (invoke) invoke("retry_startup").catch(function () {});
  });
  document.getElementById("details-toggle").addEventListener("click", function () {
    details.style.display = details.style.display === "block" ? "none" : "block";
  });
  document.getElementById("quit").addEventListener("click", function () {
    var invoke = window.__TAURI__ && window.__TAURI__.core
      && window.__TAURI__.core.invoke;
    if (invoke) invoke("quit_app").catch(function () {});
  });
})();