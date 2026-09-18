const crypto = require("node:crypto");

const HEADER_HEX_LENGTH = 56;
const DEFAULT_TTL_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 5;
const LOCKOUT_MS = 15 * 60 * 1000;
const ATTEMPT_WINDOW_MS = 15 * 60 * 1000;
const MIN_ENV_KEY_LENGTH = 16;
const SWEEP_INTERVAL_MS = 60 * 1000;
const MAX_STORE_ENTRIES = 50000;
// Histerese: o corte desce ao piso, evitando varredura O(n) a cada insert no teto.
const STORE_LOW_WATER = 45000;
const HEX_RE = /^[0-9a-fA-F]+$/;

/**
 * PILAR 4: Resolução fail-closed da chave mestra.
 * Nunca há fallback para constante versionada: ausência de chave aborta a operação.
 */
function getMasterKey(secret) {
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

  const fromEnv = process.env.APP_MASTER_KEY || process.env.SITE_PASSWORD;
  if (typeof fromEnv === "string" && fromEnv.length >= MIN_ENV_KEY_LENGTH) return fromEnv;
  if (fromEnv) {
    throw new Error(
      `Weak master key: APP_MASTER_KEY/SITE_PASSWORD must have at least ${MIN_ENV_KEY_LENGTH} chars`
    );
  }
  throw new Error("Master key not configured: set APP_MASTER_KEY (or SITE_PASSWORD) in the environment");
}

function deriveKey(secret) {
  return crypto.createHash("sha256").update(getMasterKey(secret), "utf8").digest();
}

/**
 * PILAR 3: Varredura amortizada de TTL com teto rígido de entradas.
 * Evita timers (não prende o event loop) e mantém custo O(n) amortizado por janela.
 */
function createSweeper(store, isStale, isProtected, hardCap = true) {
  let lastSweep = 0;
  return function maybeSweep() {
    const now = Date.now();
    // Sweep emergencial por tamanho só existe onde a varredura pode de fato reduzir o
    // store (hardCap=true). Sem hardCap o estado saturado é absorvente: repetir a
    // varredura por requisição seria O(n) no hot path sem remover uma única entrada.
    const needsEmergencySweep = hardCap && store.size > MAX_STORE_ENTRIES;
    if (now - lastSweep < SWEEP_INTERVAL_MS && !needsEmergencySweep) return;
    lastSweep = now;

    for (const [key, value] of store) {
      if (isStale(value, now)) store.delete(key);
    }

    if (store.size <= MAX_STORE_ENTRIES) return;

    let overflow = store.size - STORE_LOW_WATER;

    // Fase 1: evicta FIFO apenas entradas NÃO protegidas (nunca derruba lockout ativo).
    if (typeof isProtected === "function") {
      for (const [key, value] of store) {
        if (isProtected(value, now)) continue;
        store.delete(key);
        if (--overflow <= 0) return;
      }
    }

    // Fase 2: teto rígido de memória, se o excedente for todo protegido.
    // Stores anti-replay usam hardCap=false: derrubar entrada protegida seria fail-open.
    if (!hardCap) return;
    for (const key of store.keys()) {
      store.delete(key);
      if (--overflow <= 0) break;
    }
  };
}

/**
 * PILAR 3: Comparador em tempo constante via HMAC-then-compare com chave efêmera
 * por processo. Digests têm sempre 32 bytes, eliminando qualquer ramo por comprimento.
 */
const TIMING_SAFE_KEY = crypto.randomBytes(32);

function timingSafeEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const digestA = crypto.createHmac("sha256", TIMING_SAFE_KEY).update(a, "utf8").digest();
  const digestB = crypto.createHmac("sha256", TIMING_SAFE_KEY).update(b, "utf8").digest();
  return crypto.timingSafeEqual(digestA, digestB);
}

function encryptPayload(payload, secret) {
  const plaintext = typeof payload === "string" ? payload : JSON.stringify(payload);
  const key = deriveKey(secret);
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);

  const ciphertext = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();

  return iv.toString("hex") + tag.toString("hex") + ciphertext.toString("hex");
}

function isWellFormedPacket(hexPacket) {
  return (
    typeof hexPacket === "string" &&
    hexPacket.length >= HEADER_HEX_LENGTH &&
    hexPacket.length % 2 === 0 &&
    HEX_RE.test(hexPacket)
  );
}

function decryptPayload(hexPacket, secret) {
  if (!isWellFormedPacket(hexPacket)) {
    throw new Error("Invalid ciphertext packet format: minimum 56 hex chars required");
  }

  const key = deriveKey(secret);
  const iv = Buffer.from(hexPacket.slice(0, 24), "hex");
  const tag = Buffer.from(hexPacket.slice(24, 56), "hex");
  const ciphertext = Buffer.from(hexPacket.slice(56), "hex");

  const decipher = crypto.createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);

  // Mensagem de erro do OpenSSL preservada: contratada por test_suite_e2ee.js.
  const decryptedStr = decipher.update(ciphertext, undefined, "utf8") + decipher.final("utf8");
  try {
    return JSON.parse(decryptedStr);
  } catch {
    return decryptedStr;
  }
}

/**
 * PILAR 1/4: `strict: true` impõe a política Zero-Plaintext em rotas sensíveis,
 * rejeitando entradas não cifradas em vez de repassá-las como dado legítimo.
 */
function unpackEncryptedOrPlain(input, secret, options = {}) {
  const strict = !!options.strict;

  if (!input) {
    if (strict) throw new Error("Encrypted payload required: empty input rejected");
    return input;
  }

  if (typeof input === "object" && "encrypted" in input && typeof input.encrypted === "string") {
    return decryptPayload(input.encrypted, secret);
  }

  if (isWellFormedPacket(input)) {
    try {
      return decryptPayload(input, secret);
    } catch (err) {
      if (strict) throw err;
      return input;
    }
  }

  if (strict) throw new Error("Encrypted payload required: plaintext input rejected");
  return input;
}

// PILAR 2: Tokens com TTL assinados (base64url + hmac_sha256)
function toBase64Url(buf) {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function fromBase64Url(str) {
  let base64 = str.replace(/-/g, "+").replace(/_/g, "/");
  while (base64.length % 4 !== 0) base64 += "=";
  return Buffer.from(base64, "base64");
}

function issueSignedToken(payload, ttlMs = DEFAULT_TTL_MS, secret) {
  const master = getMasterKey(secret);
  const now = Date.now();
  const exp = now + ttlMs;

  const dataContainer = { data: payload, iat: now, exp };
  const jsonStr = JSON.stringify(dataContainer);
  const encodedPayload = toBase64Url(Buffer.from(jsonStr, "utf8"));

  const signature = crypto.createHmac("sha256", master).update(encodedPayload).digest("hex");
  return `${encodedPayload}.${signature}`;
}

function verifySignedToken(token, secret) {
  if (typeof token !== "string" || !token.includes(".")) {
    return { valid: false, reason: "Formato de token inválido" };
  }

  const parts = token.split(".");
  if (parts.length !== 2) return { valid: false, reason: "Token malformado" };

  const [encodedPayload, signature] = parts;
  const master = getMasterKey(secret);
  const expectedSignature = crypto.createHmac("sha256", master).update(encodedPayload).digest("hex");

  if (!timingSafeEq(signature, expectedSignature)) {
    return { valid: false, reason: "Assinatura criptográfica inválida" };
  }

  try {
    const rawJson = fromBase64Url(encodedPayload).toString("utf8");
    const container = JSON.parse(rawJson);
    if (!container || typeof container.exp !== "number" || !Number.isFinite(container.exp)) {
      return { valid: false, reason: "Token sem expiração válida" };
    }
    if (Date.now() > container.exp) {
      return { valid: false, expired: true, reason: "Token expirado" };
    }
    return { valid: true, data: container.data, exp: container.exp };
  } catch (err) {
    return { valid: false, reason: err.message };
  }
}

// PILAR 3: token -> timestamp de expiração, permitindo purga por TTL.
const consumedTokensStore = new Map();
const sweepConsumedTokens = createSweeper(
  consumedTokensStore,
  (exp, now) => now > exp,
  // Negação estrita de isStale: em exp === now o token ainda é aceito por
  // verifySignedToken, logo a queima permanece protegida contra evicção.
  (exp, now) => exp >= now,
  false
);

function issueEphemeralToken(payload, ttlMs = DEFAULT_TTL_MS, options = {}) {
  const secret = (typeof options === "object" && options !== null) ? options.secret : options;
  const jti = crypto.randomBytes(16).toString("hex");
  const dataToSign = (typeof payload === "object" && payload !== null && !Array.isArray(payload))
    ? { ...payload, _jti: jti }
    : { payload, _jti: jti };
  const token = issueSignedToken(dataToSign, ttlMs, secret);
  return { token, exp: Date.now() + ttlMs, jti };
}

function verifyEphemeralToken(token, options = {}) {
  const secret = (typeof options === "object" && options !== null) ? options.secret : options;
  const autoConsume = typeof options === "object" && options !== null && !!options.autoConsumeOtt;

  if (autoConsume) {
    sweepConsumedTokens();
    if (consumedTokensStore.has(token)) {
      return { valid: false, status: "consumed", data: null, reason: "Token OTT já consumido" };
    }
    // Fail-closed: sem espaço para registrar a queima, o token NÃO pode ser validado.
    if (consumedTokensStore.size >= MAX_STORE_ENTRIES) {
      return {
        valid: false,
        status: "unavailable",
        data: null,
        reason: "Registro anti-replay saturado: token rejeitado por segurança"
      };
    }
  }

  const res = verifySignedToken(token, secret);
  if (!res.valid) {
    return {
      valid: false,
      status: res.expired ? "expired" : "invalid",
      data: null,
      reason: res.reason,
      expired: !!res.expired
    };
  }

  if (autoConsume) {
    consumedTokensStore.set(token, res.exp);
  }

  let data = res.data;
  if (data && typeof data === "object" && data._jti) {
    const { _jti, ...rest } = data;
    data = rest;
  }

  return {
    valid: true,
    status: "valid",
    data,
    reason: null
  };
}

// PILAR 2: One-Time Tokens (OTT) de Queima Única
const ottStore = new Map();
const sweepOtt = createSweeper(ottStore, (record, now) => now > record.exp);

function issueOneTimeToken(payload, ttlMs = DEFAULT_TTL_MS, secret) {
  const master = getMasterKey(secret);
  const now = Date.now();
  const exp = now + ttlMs;
  const id = crypto.randomBytes(16).toString("hex");

  const message = `ott_${id}_${exp}`;
  const hmac = crypto.createHmac("sha256", master).update(message).digest("hex");
  const token = `${message}_${hmac}`;

  sweepOtt();
  ottStore.set(id, { payload, exp });
  return { token, ottId: id, exp };
}

function consumeOneTimeToken(token, secret) {
  if (typeof token !== "string" || !token.startsWith("ott_")) {
    return { valid: false, reason: "Token OTT malformado" };
  }

  const parts = token.split("_");
  if (parts.length !== 4) return { valid: false, reason: "Estrutura do OTT inválida" };

  const [, id, expStr, hmac] = parts;
  const exp = parseInt(expStr, 10);
  if (!Number.isFinite(exp)) return { valid: false, reason: "Expiração do OTT inválida" };

  const master = getMasterKey(secret);
  const message = `ott_${id}_${exp}`;
  const expectedHmac = crypto.createHmac("sha256", master).update(message).digest("hex");

  if (!timingSafeEq(hmac, expectedHmac)) {
    return { valid: false, reason: "Assinatura HMAC do OTT inválida" };
  }

  const now = Date.now();
  if (now > exp) {
    ottStore.delete(id);
    return { valid: false, expired: true, reason: "OTT expirado" };
  }

  const record = ottStore.get(id);
  if (!record) {
    return { valid: false, consumed: true, reason: "Token já foi consumido" };
  }

  // Queima destrutiva: elimina o payload sensível da memória no ato do resgate.
  ottStore.delete(id);
  return { valid: true, data: record.payload };
}

// PILAR 4: Anti-Brute Force Guard com janela deslizante
const bruteForceStore = new Map();
const sweepBruteForce = createSweeper(
  bruteForceStore,
  (record, now) => now > record.lockedUntil && now - record.lastFailure > ATTEMPT_WINDOW_MS,
  (record, now) => record.lockedUntil > now
);

class BruteForceGuard {
  static getStatus(id) {
    const record = bruteForceStore.get(id);
    if (!record) return { locked: false, remainingMs: 0, attempts: 0 };

    const now = Date.now();
    if (record.lockedUntil > now) {
      return { locked: true, remainingMs: record.lockedUntil - now, attempts: record.count };
    }
    if (record.lockedUntil > 0 || now - record.lastFailure > ATTEMPT_WINDOW_MS) {
      bruteForceStore.delete(id);
      return { locked: false, remainingMs: 0, attempts: 0 };
    }
    return { locked: false, remainingMs: 0, attempts: record.count };
  }

  static recordFailure(id) {
    const now = Date.now();
    sweepBruteForce();

    let record = bruteForceStore.get(id);
    // Janela deslizante: falhas isoladas fora da janela não penalizam o usuário legítimo.
    if (!record || now - record.lastFailure > ATTEMPT_WINDOW_MS) {
      record = { count: 1, lastFailure: now, lockedUntil: 0 };
    } else {
      record.count += 1;
      record.lastFailure = now;
    }

    if (record.count >= MAX_ATTEMPTS) {
      record.lockedUntil = now + LOCKOUT_MS;
      bruteForceStore.set(id, record);
      return { locked: true, remainingMs: LOCKOUT_MS, attempts: record.count };
    }

    bruteForceStore.set(id, record);
    return { locked: false, remainingMs: 0, attempts: record.count };
  }

  static recordSuccess(id) {
    bruteForceStore.delete(id);
  }
}

module.exports = {
  getMasterKey,
  deriveKey,
  timingSafeEq,
  encryptPayload,
  decryptPayload,
  unpackEncryptedOrPlain,
  issueSignedToken,
  verifySignedToken,
  issueEphemeralToken,
  verifyEphemeralToken,
  issueOneTimeToken,
  consumeOneTimeToken,
  BruteForceGuard,
};
