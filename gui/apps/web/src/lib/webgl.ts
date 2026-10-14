let cached: boolean | undefined;

/**
 * Whether this browser can create a WebGL2 context (deck.gl 9 needs WebGL2).
 * Probed once per page with a throw-away canvas whose context is released
 * immediately, so the probe never counts against the browser's context limit.
 */
export function isWebGL2Available(): boolean {
  if (cached !== undefined) return cached;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    cached = !!gl;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    cached = false;
  }
  return cached;
}

/** Tests only: forget the cached probe result. */
export function resetWebGLProbe(): void {
  cached = undefined;
}
