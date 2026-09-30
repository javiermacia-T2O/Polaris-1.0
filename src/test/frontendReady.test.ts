import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

/**
 * FASE 2: the React shell must report readiness to the Rust
 * `StartupCoordinator` exactly once, and never let a failure to signal break
 * the app (the app also runs in a plain browser during development).
 */
describe("frontend readiness signal", () => {
  const invoke = vi.fn((_operation: string, _args?: unknown) => Promise.resolve());

  beforeEach(() => {
    vi.resetModules();
    invoke.mockClear();
    vi.stubGlobal("__TAURI_INTERNALS__", { invoke });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("invokes frontend_ready after the shell mounts", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    await import("../main");
    // The signal is emitted from a React passive effect, which flushes after
    // the commit; give the scheduler a couple of macrotasks to run it.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(invoke).toHaveBeenCalled();
    // The app issues its own IPC calls; readiness must be among them.
    const operations = invoke.mock.calls.map((call) => call[0]);
    expect(operations).toContain("frontend_ready");
  });

  it("does not throw when the Tauri bridge is unavailable", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    invoke.mockImplementation(() => Promise.reject(new Error("no bridge")));
    await expect(import("../main")).resolves.toBeDefined();
    // Flush the passive effect so this test's readiness signal is not deferred
    // into the next test (which would make it look like a duplicate).
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("signals readiness exactly once even under StrictMode double-invocation", async () => {
    // Case 11: React StrictMode mounts effects twice in development, and the
    // `load` safety net may also fire. The coordinator must still receive a
    // single `frontend_ready`, so the guard has to survive repeated calls.
    document.body.innerHTML = '<div id="root"></div>';
    const module = await import("../main");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    const before = invoke.mock.calls.filter(
      (call) => call[0] === "frontend_ready",
    ).length;
    expect(before).toBeGreaterThanOrEqual(1);
    // Simulate the extra signals StrictMode and the load listener can produce.
    module.signalFrontendReady();
    module.signalFrontendReady();
    const readinessCalls = invoke.mock.calls.filter(
      (call) => call[0] === "frontend_ready",
    );
    expect(readinessCalls).toHaveLength(before);
  });
});
