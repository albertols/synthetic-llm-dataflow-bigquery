import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from "lucide-react";
import { Toast as ToastPrimitive } from "radix-ui";
import { useSyncExternalStore } from "react";

import { cn } from "@/lib/cn";

export type ToastTone = "info" | "good" | "warn" | "critical";
export type ToastInput = { title: string; description?: string; tone?: ToastTone; duration?: number };
type ToastItem = ToastInput & { id: number; open: boolean };

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/** Shows a toast from anywhere (no hook needed). Returns its id. Errors use tone "critical" and stay 8 s. */
export function toast(input: ToastInput): number {
  const id = nextId++;
  items = [...items.slice(-3), { ...input, id, open: true }];
  emit();
  return id;
}

export function dismissToast(id: number) {
  items = items.map((item) => (item.id === id ? { ...item, open: false } : item));
  emit();
  setTimeout(() => {
    items = items.filter((item) => item.id !== id);
    emit();
  }, 300);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const TONE: Record<ToastTone, { icon: typeof Info; className: string; label: string }> = {
  info: { icon: Info, className: "text-status-info-text", label: "Info" },
  good: { icon: CircleCheck, className: "text-status-good-text", label: "Done" },
  warn: { icon: TriangleAlert, className: "text-status-warn-text", label: "Warning" },
  critical: { icon: CircleAlert, className: "text-status-critical-text", label: "Error" },
};

/** Mounted once in AppShell; renders the toast queue in a polite live region. */
export function Toaster() {
  const current = useSyncExternalStore(
    subscribe,
    () => items,
    () => items,
  );
  return (
    <ToastPrimitive.Provider swipeDirection="right">
      {current.map((item) => {
        const tone = TONE[item.tone ?? "info"];
        const Icon = tone.icon;
        return (
          <ToastPrimitive.Root
            key={item.id}
            open={item.open}
            duration={item.duration ?? (item.tone === "critical" ? 8000 : 5000)}
            type={item.tone === "critical" ? "foreground" : "background"}
            onOpenChange={(open) => {
              if (!open) dismissToast(item.id);
            }}
            className="flex w-full items-start gap-3 rounded-md border border-border-strong bg-surface-2 p-3 pr-9 shadow-popover data-[state=open]:animate-pop-in"
          >
            <Icon className={cn("mt-0.5 size-4 shrink-0", tone.className)} aria-label={tone.label} />
            <div className="grid gap-0.5">
              <ToastPrimitive.Title className="text-sm font-medium text-text-1">{item.title}</ToastPrimitive.Title>
              {item.description ? (
                <ToastPrimitive.Description className="text-sm text-text-2">
                  {item.description}
                </ToastPrimitive.Description>
              ) : null}
            </div>
            <ToastPrimitive.Close
              aria-label="Dismiss notification"
              className="absolute top-2 right-2 inline-flex size-6 items-center justify-center rounded-sm text-text-3 hover:bg-surface-3 hover:text-text-1"
            >
              <X className="size-3.5" aria-hidden="true" />
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        );
      })}
      <ToastPrimitive.Viewport
        label="Notifications ({hotkey})"
        className="fixed right-0 bottom-0 z-[60] flex w-full max-w-sm flex-col gap-2 p-4 outline-none"
      />
    </ToastPrimitive.Provider>
  );
}
