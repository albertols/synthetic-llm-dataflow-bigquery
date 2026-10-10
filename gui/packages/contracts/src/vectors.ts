/**
 * The binary envelope of `GET /api/rag/chunks`: chunk metadata (JSON) and the
 * embeddings as little-endian Float32, in one response.
 *
 *   bytes 0..3   magic "SPV1"
 *   bytes 4..7   uint32 LE: metadata byte length (m)
 *   bytes 8..11  uint32 LE: vector dimension (d)
 *   bytes 12..15 uint32 LE: vector count (n)
 *   16..16+m     UTF-8 JSON: ChunkMeta[] (n entries)
 *   padding      zero bytes up to a multiple of 4
 *   then         n × d Float32 LE, row-major (vector i = meta[i])
 *
 * Dependency-free on purpose: the web app decodes it without zod.
 */
const MAGIC = "SPV1";
const HEADER_BYTES = 16;

export interface VectorEnvelope<M> {
  meta: M[];
  dim: number;
  count: number;
  /** n × dim, row-major. */
  vectors: Float32Array;
}

export function encodeVectorEnvelope<M>(meta: readonly M[], vectors: Float32Array, dim: number): Uint8Array {
  if (dim <= 0 || !Number.isInteger(dim)) throw new Error(`dim must be a positive integer, got ${dim}`);
  if (vectors.length !== meta.length * dim)
    throw new Error(`vectors hold ${vectors.length} floats; ${meta.length} chunks × ${dim} dims expected`);
  const json = new TextEncoder().encode(JSON.stringify(meta));
  const padded = Math.ceil((HEADER_BYTES + json.length) / 4) * 4;
  const out = new Uint8Array(padded + vectors.length * 4);
  const view = new DataView(out.buffer);
  for (let i = 0; i < 4; i += 1) out[i] = MAGIC.charCodeAt(i);
  view.setUint32(4, json.length, true);
  view.setUint32(8, dim, true);
  view.setUint32(12, meta.length, true);
  out.set(json, HEADER_BYTES);
  for (let i = 0; i < vectors.length; i += 1) view.setFloat32(padded + i * 4, vectors[i]!, true);
  return out;
}

export function decodeVectorEnvelope<M>(buffer: ArrayBuffer | Uint8Array): VectorEnvelope<M> {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (bytes.length < HEADER_BYTES) throw new Error("vector envelope: truncated header");
  const magic = String.fromCharCode(bytes[0]!, bytes[1]!, bytes[2]!, bytes[3]!);
  if (magic !== MAGIC) throw new Error(`vector envelope: bad magic "${magic}"`);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const metaLength = view.getUint32(4, true);
  const dim = view.getUint32(8, true);
  const count = view.getUint32(12, true);
  const padded = Math.ceil((HEADER_BYTES + metaLength) / 4) * 4;
  if (bytes.length !== padded + count * dim * 4) throw new Error("vector envelope: length mismatch");
  const meta = JSON.parse(new TextDecoder().decode(bytes.subarray(HEADER_BYTES, HEADER_BYTES + metaLength))) as M[];
  if (meta.length !== count) throw new Error("vector envelope: metadata count mismatch");
  const vectors = new Float32Array(count * dim);
  for (let i = 0; i < vectors.length; i += 1) vectors[i] = view.getFloat32(padded + i * 4, true);
  return { meta, dim, count, vectors };
}
