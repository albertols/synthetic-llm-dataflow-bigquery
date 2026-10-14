import { useRouter, type ErrorComponentProps } from "@tanstack/react-router";
import { RotateCcw, TriangleAlert } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";

function ErrorPanel({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <EmptyState
      icon={TriangleAlert}
      title="Something broke while rendering this view"
      description={
        <>
          <p>The rest of the app still works. Retry, or switch tabs.</p>
          {import.meta.env.DEV ? (
            <pre className="mt-3 max-w-full overflow-x-auto rounded-md bg-surface-3 p-3 text-left font-mono text-xs text-status-critical-text">
              {message}
            </pre>
          ) : null}
        </>
      }
      action={
        <Button variant="secondary" onClick={onRetry}>
          <RotateCcw aria-hidden="true" />
          Retry
        </Button>
      }
    />
  );
}

/** Router-level error view (`defaultErrorComponent`): one broken tab never takes the shell down. */
export function RouteErrorFallback({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  return (
    <ErrorPanel
      error={error}
      onRetry={() => {
        reset();
        void router.invalidate();
      }}
    />
  );
}

type Props = { children: ReactNode };
type State = { error: unknown };

/** Last-resort boundary around the router itself. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  override render() {
    if (this.state.error !== null) {
      return (
        <div className="mx-auto max-w-2xl p-6">
          <ErrorPanel error={this.state.error} onRetry={() => this.setState({ error: null })} />
        </div>
      );
    }
    return this.props.children;
  }
}
