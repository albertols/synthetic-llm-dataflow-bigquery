/**
 * Vitest setup for the web app (jsdom). Fills the browser APIs jsdom lacks
 * and that Radix, ECharts wrappers and the theme code touch.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

class ResizeObserverStub implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub;

if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  });
}

const elementShims: Record<string, unknown> = {
  scrollIntoView: () => {},
  hasPointerCapture: () => false,
  releasePointerCapture: () => {},
};
for (const [name, impl] of Object.entries(elementShims)) {
  if (!(name in Element.prototype)) Object.defineProperty(Element.prototype, name, { value: impl, configurable: true });
}
