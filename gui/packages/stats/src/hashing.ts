/**
 * Exact port of `sdfb_core.rag.embedding.HashingEmbedder` (the dependency-free
 * embedder, identity `hashing-384/v1`). Pinned by the golden
 * `hashing_embedder.json`.
 *
 * Per token: SHA-256 of `str(seed) + "\x00" + token` (UTF-8); the bucket is the
 * first 8 digest bytes read big-endian as a uint64, modulo `dim` (BigInt, since
 * a double cannot hold a uint64); the sign is `+1` when digest byte 8 is even.
 * Tokens come from Python's `str.split()`, whose whitespace set differs from
 * JavaScript's `\s` (it includes U+001C–U+001F and U+0085, excludes U+FEFF), so
 * the set is spelled out. No tokens → the whole text is one token. The vector
 * is divided by its L2 norm; an all-zero vector (cancelling signs) becomes
 * `e_0`. Not a semantic embedder: two random texts sit at cosine ≈ ±1/√384.
 */
import { sha256 } from "./sha256";

/** The code points for which Python's `str.isspace()` is true (what `str.split()` splits on). */
export const PY_WHITESPACE: ReadonlySet<number> = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003,
  0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

/** Python's `text.split()` with no arguments. */
export function pySplit(text: string): string[] {
  const out: string[] = [];
  let current = "";
  for (const char of text) {
    if (PY_WHITESPACE.has(char.codePointAt(0)!)) {
      if (current) out.push(current);
      current = "";
    } else current += char;
  }
  if (current) out.push(current);
  return out;
}

/** The tokens the embedder hashes: `text.split() or [text]`. */
export function hashingTokens(text: string): string[] {
  const tokens = pySplit(text);
  return tokens.length ? tokens : [text];
}

const encoder = new TextEncoder();

/** Token → bucket is pure; row texts repeat tokens ("is", column names), so a bounded memo pays off. */
const BUCKETS = new Map<string, { bucket: number; sign: 1 | -1 }>();
const BUCKET_MEMO_MAX = 50_000;

export function hashingBucket(token: string, dim = 384, seed = 0): { bucket: number; sign: 1 | -1 } {
  const key = `${seed}\u0000${dim}\u0000${token}`;
  const known = BUCKETS.get(key);
  if (known) return known;
  const result = computeBucket(token, dim, seed);
  if (BUCKETS.size >= BUCKET_MEMO_MAX) BUCKETS.clear();
  BUCKETS.set(key, result);
  return result;
}

function computeBucket(token: string, dim: number, seed: number): { bucket: number; sign: 1 | -1 } {
  const prefix = encoder.encode(String(seed));
  const body = encoder.encode(token);
  const message = new Uint8Array(prefix.length + 1 + body.length);
  message.set(prefix);
  message[prefix.length] = 0;
  message.set(body, prefix.length + 1);
  const digest = sha256(message);
  let value = 0n;
  for (let i = 0; i < 8; i += 1) value = (value << 8n) | BigInt(digest[i]!);
  return { bucket: Number(value % BigInt(dim)), sign: (digest[8]! & 1) === 0 ? 1 : -1 };
}

export interface HashingOptions {
  dim?: number;
  seed?: number;
}

/** One text → a unit vector (Float64, exactly what Python computes). */
export function hashingEmbed(text: string, { dim = 384, seed = 0 }: HashingOptions = {}): Float64Array {
  if (dim <= 0) throw new Error(`dim must be positive, got ${dim}`);
  const vec = new Float64Array(dim);
  for (const token of hashingTokens(text)) {
    const { bucket, sign } = hashingBucket(token, dim, seed);
    vec[bucket]! += sign;
  }
  let sumSquares = 0;
  for (const v of vec) sumSquares += v * v;
  const norm = Math.sqrt(sumSquares);
  if (norm === 0) {
    vec[0] = 1;
    return vec;
  }
  for (let i = 0; i < dim; i += 1) vec[i] = vec[i]! / norm;
  return vec;
}

/** A batch, as one row-major Float32Array (the wire format of `/api/rag/chunks`). */
export function hashingEmbedBatch(texts: readonly string[], options: HashingOptions = {}): Float32Array {
  const dim = options.dim ?? 384;
  const out = new Float32Array(texts.length * dim);
  texts.forEach((text, i) => out.set(hashingEmbed(text, options), i * dim));
  return out;
}
