/**
 * Client-side cryptographic engine — shim de compatibilidade.
 * A implementação canônica única vive em crypto-web.js; este arquivo apenas
 * reexporta os nomes históricos (encryptClientPayload/decryptClientPayload).
 */

const impl =
  typeof module !== "undefined" && module.exports
    ? require("./crypto-web")
    : globalThis.HermesCryptoWeb;

if (!impl) {
  throw new Error("crypto-web.js must be loaded before crypto.client.js");
}

const {
  bufToHex,
  hexToBuf,
  isWellFormedPacket,
  deriveClientKey,
  encryptClientPayload,
  decryptClientPayload,
  envelopeFormPayload,
} = impl;

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    bufToHex,
    hexToBuf,
    isWellFormedPacket,
    deriveClientKey,
    encryptClientPayload,
    decryptClientPayload,
    envelopeFormPayload,
  };
}
