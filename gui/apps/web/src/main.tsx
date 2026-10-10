import "./styles/globals.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ErrorBoundary } from "./app/ErrorBoundary";
import { ThemeProvider } from "./app/ThemeProvider";
import { applyTheme, readStoredPreference, resolveTheme } from "./lib/theme";
import { createAppRouter } from "./router";

// Stamp the stored theme before the first paint of the app.
applyTheme(resolveTheme(readStoredPreference()));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, refetchOnWindowFocus: false, retry: 1 },
  },
});
const router = createAppRouter(queryClient);

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
);
