import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { App } from "./app/App";
import { api } from "./shared/api";
import "./styles.css";

declare global {
  interface Window {
    __polarisBoot?: { done: () => void };
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000 } },
});

/** Waits for the local engine to answer, then dismisses the boot splash. */
function BootGate({ children }: { children: React.ReactNode }) {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    retry: 12,
    retryDelay: 400,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!health.isSuccess && !health.isError) return;
    const timer = window.setTimeout(() => window.__polarisBoot?.done(), health.isSuccess ? 320 : 0);
    return () => window.clearTimeout(timer);
  }, [health.isSuccess, health.isError]);

  return <>{children}</>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BootGate>
        <App />
      </BootGate>
    </QueryClientProvider>
  </StrictMode>,
);
