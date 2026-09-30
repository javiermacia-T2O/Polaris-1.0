import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { invoke } from "@tauri-apps/api/core";
import { App } from "./app/App";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000 } },
});

let signalled = false;

/**
 * Tells the Rust StartupCoordinator that the shell is mounted.
 *
 * `frontend_ready` means the React tree rendered and the navigation is
 * available — not that every analysis, dataset count or diagnostic finished.
 * Those are deferred and load after the main window is already visible.
 */
function signalFrontendReady() {
  if (signalled) return;
  signalled = true;
  invoke("frontend_ready").catch(() => {
    // Running outside Tauri (e.g. `vite` in a browser): nothing to signal.
  });
}

/**
 * Reports readiness from a real React commit.
 *
 * A passive effect runs after the tree is committed, which is the honest
 * "the shell is mounted" milestone. `requestAnimationFrame` is deliberately
 * not used: the main window starts hidden, and WebView2 pauses animation
 * frames for non-visible windows, so a rAF-based signal would never fire.
 */
function ReadySignal() {
  useEffect(() => {
    signalFrontendReady();
  }, []);
  return null;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ReadySignal />
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

export { queryClient, signalFrontendReady };
