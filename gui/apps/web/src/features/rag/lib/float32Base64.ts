/**
 * Float32 arrays as base64 of their little-endian bytes: how a projection
 * layout travels to and from the BFF's layout cache (`/api/x/rag/projection`).
 * 3,000 points × 3 coordinates × 4 bytes = 36,000 bytes = 48,000 base64
 * characters, inside the BFF's 64 KB body limit; the same layout as a JSON
 * number array is about 81 KB. Written byte by byte with a DataView, so the
 * encoding is little-endian on any host.
 */
const CHUNK = 0x8000;

export function float32ToBase64(values: Float32Array): string {
  const bytes = new Uint8Array(values.length * 4);
  const view = new DataView(bytes.buffer);
  values.forEach((v, i) => view.setFloat32(i * 4, v, true));
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK)
    binary += String.fromCharCode(...bytes.subarray(i, Math.min(i + CHUNK, bytes.length)));
  return btoa(binary);
}

/** The inverse; null when the text is not base64 or its byte length is not a multiple of 4. */
export function base64ToFloat32(text: string): Float32Array | null {
  let binary: string;
  try {
    binary = atob(text);
  } catch {
    return null;
  }
  if (binary.length % 4 !== 0) return null;
  const view = new DataView(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i += 1) view.setUint8(i, binary.charCodeAt(i));
  const out = new Float32Array(binary.length / 4);
  for (let i = 0; i < out.length; i += 1) out[i] = view.getFloat32(i * 4, true);
  return out;
}
