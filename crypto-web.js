/**
 * Web Cryptographic Engine (Browser & Isomorphic Node) — módulo canônico.
 * File: crypto-web.js
 * Paridade estrita com crypto.server.js: IV 12B | TAG 16B | CIPHERTEXT (hex).
 */

const HEADER_HEX_LENGTH = 56;
const MIN_ENV_KEY_LENGTH = 16;
const HEX_RE = /^[0-9a-fA-F]+$/;
const FIXED_CMP_WINDOW = 1024;

/**
 * PILAR 4: resolução fail-closed da chave mestra (espelha getMasterKey do servidor).
 * Nunca existe constante versionada: ausência de chave aborta a operação.
 */
function resolveClientMasterKey(secret) {
  if (secret !== undefined && secret !== null) {
    if (typeof secret !== "string") {
      throw new TypeError("Master key must be a non-empty string");
    }
    if (secret.length < MIN_ENV_KEY_LENGTH) {
      throw new Error(
        `Weak master key: explicit secret must have at least ${MIN_ENV_KEY_LENGTH} chars`
      );
    }
    return secret;
  }

  const injected =
    typeof globalThis.__APP_MASTER_KEY__ === "string"
      ? globalThis.__APP_MASTER_KEY__
      : typeof process !== "undefined" && process.env
        ? process.env.APP_MASTER_KEY || process.env.SITE_PASSWORD
        : undefined;

  if (typeof injected === "string" && injected.length >= MIN_ENV_KEY_LENGTH) return injected;
  if (injected) {
    throw new Error(
      `Weak master key: injected key must have at least ${MIN_ENV_KEY_LENGTH} chars`
    );
  }
  throw new Error(
    "Master key not configured: provide an explicit secret or inject globalThis.__APP_MASTER_KEY__"
  );
}

function bufToHex(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}

function hexToBuf(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0) {
    throw new Error("Invalid hex string length");
  }
  if (hex.length > 0 && !HEX_RE.test(hex)) {
    throw new Error("Invalid hex string: non-hexadecimal characters");
  }
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
  }
  return bytes;
}

function isWellFormedPacket(hexPacket) {
  return (
    typeof hexPacket === "string" &&
    hexPacket.length >= HEADER_HEX_LENGTH &&
    hexPacket.length % 2 === 0 &&
    HEX_RE.test(hexPacket)
  );
}

async function deriveClientKey(secret) {
  const enc = new TextEncoder();
  const rawHash = await globalThis.crypto.subtle.digest(
    "SHA-256",
    enc.encode(resolveClientMasterKey(secret))
  );
  return await globalThis.crypto.subtle.importKey(
    "raw",
    rawHash,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"]
  );
}

async function encryptPayloadClient(data, secret) {
  const plaintext = typeof data === "string" ? data : JSON.stringify(data);
  const key = await deriveClientKey(secret);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));

  const enc = new TextEncoder();
  const encryptedBuffer = await globalThis.crypto.subtle.encrypt(
    { name: "AES-GCM", iv, tagLength: 128 },
    key,
    enc.encode(plaintext)
  );

  const totalLen = encryptedBuffer.byteLength;
  if (totalLen < 16) throw new Error("AEAD output too short: missing authentication tag");
  const cipherBytes = new Uint8Array(encryptedBuffer, 0, totalLen - 16);
  const tagBytes = new Uint8Array(encryptedBuffer, totalLen - 16, 16);

  return bufToHex(iv) + bufToHex(tagBytes) + bufToHex(cipherBytes);
}

async function decryptPayloadClient(hexPayload, secret) {
  if (!isWellFormedPacket(hexPayload)) {
    throw new Error("Invalid ciphertext packet format: minimum 56 hex chars required");
  }

  const key = await deriveClientKey(secret);
  const iv = hexToBuf(hexPayload.slice(0, 24));
  const tag = hexToBuf(hexPayload.slice(24, 56));
  const ciphertext = hexToBuf(hexPayload.slice(56));

  // Web Crypto exige CIPHERTEXT || TAG contíguos (Node separa via setAuthTag).
  const combined = new Uint8Array(ciphertext.length + tag.length);
  combined.set(ciphertext, 0);
  combined.set(tag, ciphertext.length);

  const decryptedBuffer = await globalThis.crypto.subtle.decrypt(
    { name: "AES-GCM", iv, tagLength: 128 },
    key,
    combined
  );

  const decryptedStr = new TextDecoder().decode(decryptedBuffer);
  try {
    return JSON.parse(decryptedStr);
  } catch {
    return decryptedStr;
  }
}

/**
 * PILAR 4: comparação síncrona com custo fixo (FIXED_CMP_WINDOW), sem ramo por
 * comprimento e sem early-exit. Entradas acima da janela são rejeitadas apenas
 * após o laço completo, preservando tempo constante.
 */
function timingSafeEqClientSync(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  let diff = a.length ^ b.length;
  for (let i = 0; i < FIXED_CMP_WINDOW; i++) {
    const charA = i < a.length ? a.charCodeAt(i) : 0;
    const charB = i < b.length ? b.charCodeAt(i) : 0;
    diff |= charA ^ charB;
  }
  const withinWindow = a.length <= FIXED_CMP_WINDOW && b.length <= FIXED_CMP_WINDOW;
  return diff === 0 && withinWindow;
}

/**
 * PILAR 4: HMAC-then-compare com chave efêmera por contexto, espelhando
 * timingSafeEq do servidor. Digests têm sempre 32 bytes: zero ramo por tamanho.
 */
let timingSafeKeyPromise = null;

function getTimingSafeKey() {
  if (timingSafeKeyPromise === null) {
    timingSafeKeyPromise = globalThis.crypto.subtle.generateKey(
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );
  }
  return timingSafeKeyPromise;
}

async function timingSafeEqClient(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const enc = new TextEncoder();
  const key = await getTimingSafeKey();
  const macA = new Uint8Array(await globalThis.crypto.subtle.sign("HMAC", key, enc.encode(a)));
  const macB = new Uint8Array(await globalThis.crypto.subtle.sign("HMAC", key, enc.encode(b)));

  let diff = 0;
  for (let i = 0; i < macA.length; i++) {
    diff |= macA[i] ^ macB[i];
  }
  return diff === 0;
}

async function envelopeFormPayload(formData, secret) {
  const encrypted = await encryptPayloadClient(formData, secret);
  return { data: { encrypted } };
}

const api = {
  bufToHex,
  hexToBuf,
  isWellFormedPacket,
  resolveClientMasterKey,
  deriveClientKey,
  encryptPayloadClient,
  decryptPayloadClient,
  timingSafeEqClientSync,
  timingSafeEqClient,
  envelopeFormPayload,
  // Aliases de retrocompatibilidade (contrato de test_suite_e2ee.js).
  encryptClientPayload: encryptPayloadClient,
  decryptClientPayload: decryptPayloadClient,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else {
  globalThis.HermesCryptoWeb = api;
}
