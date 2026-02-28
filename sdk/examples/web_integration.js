/**
 * VAPI SDK — Web / Browser Integration (ES2022+)
 *
 * Targets: Next.js, React, Vue, vanilla browser, Node.js 18+
 *
 * No npm dependencies for core parsing — uses SubtleCrypto (browser built-in)
 * or node:crypto for SHA-256.
 *
 * Install optional REST client:
 *   npm install @vapi-gg/sdk     (wraps fetch, handles auth, retries)
 *
 * This file demonstrates raw SDK usage without the npm wrapper.
 */

"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VAPI = {
  SDK_VERSION: "1.0.0-phase20",
  RECORD_SIZE: 228,
  BODY_SIZE:   164,
  HASH_SIZE:    32,

  INF: {
    NOMINAL:          0x20,
    DRIVER_INJECT:    0x28,
    WALLHACK_PREAIM:  0x29,
    AIMBOT_BEHAVIORAL:0x2A,
    TEMPORAL_ANOMALY: 0x2B,
    BIO_ANOMALY:      0x30,
  },

  CHEAT_CODES: new Set([0x28, 0x29, 0x2A]),

  inferenceName(code) {
    const names = {
      0x20: "NOMINAL",
      0x28: "DRIVER_INJECT",
      0x29: "WALLHACK_PREAIM",
      0x2A: "AIMBOT_BEHAVIORAL",
      0x2B: "TEMPORAL_ANOMALY",
      0x30: "BIOMETRIC_ANOMALY",
    };
    return names[code] ?? `UNKNOWN_0x${code.toString(16).toUpperCase().padStart(2, "0")}`;
  },

  isCheat(code) { return this.CHEAT_CODES.has(code); },
};

// ---------------------------------------------------------------------------
// SHA-256 — works in browser (SubtleCrypto) and Node.js (node:crypto)
// ---------------------------------------------------------------------------

async function sha256(buffer) {
  if (typeof window !== "undefined" && window.crypto?.subtle) {
    // Browser
    const digest = await window.crypto.subtle.digest("SHA-256", buffer);
    return new Uint8Array(digest);
  } else {
    // Node.js
    const { createHash } = await import("node:crypto");
    const hash = createHash("sha256");
    hash.update(Buffer.from(buffer));
    return new Uint8Array(hash.digest());
  }
}

// ---------------------------------------------------------------------------
// VAPIRecord — parse a 228-byte PoAC record (Uint8Array or ArrayBuffer)
// ---------------------------------------------------------------------------

export class VAPIRecord {
  /**
   * @param {Uint8Array|ArrayBuffer} raw  228-byte PoAC record
   */
  constructor(raw) {
    const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
    if (bytes.length !== VAPI.RECORD_SIZE) {
      throw new Error(`VAPIRecord: expected ${VAPI.RECORD_SIZE} bytes, got ${bytes.length}`);
    }
    this._raw = bytes;

    // Parse scalar fields (big-endian, offset 128)
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.prevPoACHash      = bytes.slice(0, 32);
    this.sensorCommitment  = bytes.slice(32, 64);
    this.modelManifestHash = bytes.slice(64, 96);
    this.worldModelHash    = bytes.slice(96, 128);
    this.inferenceResult   = view.getUint8(128);
    this.actionCode        = view.getUint8(129);
    this.confidence        = view.getUint8(130);
    this.batteryPct        = view.getUint8(131);
    this.monotonicCtr      = view.getUint32(132, false);  // big-endian
    // Note: timestamp_ms is uint64; JS BigInt for full precision
    this.timestampMs       = view.getBigUint64(136, false);
    this.latitude          = view.getFloat64(144, false);
    this.longitude         = view.getFloat64(152, false);
    this.bountyId          = view.getUint32(160, false);
    this.signature         = bytes.slice(164, 228);

    // Hashes are computed async — call await record.init() after construction
    this._recordHash = null;
    this._chainHash  = null;
  }

  /** Compute SHA-256 hashes. Must be called before recordHash/chainHash. */
  async init() {
    this._recordHash = await sha256(this._raw.slice(0, VAPI.BODY_SIZE));
    this._chainHash  = await sha256(this._raw);
    return this;
  }

  get recordHash() {
    if (!this._recordHash) throw new Error("Call await record.init() first");
    return this._recordHash;
  }

  get chainHash() {
    if (!this._chainHash) throw new Error("Call await record.init() first");
    return this._chainHash;
  }

  get inferenceName() { return VAPI.inferenceName(this.inferenceResult); }
  get isClean()       { return !VAPI.isCheat(this.inferenceResult) && this.inferenceResult !== VAPI.INF.TEMPORAL_ANOMALY && this.inferenceResult !== VAPI.INF.BIO_ANOMALY; }
  get isCheat()       { return VAPI.isCheat(this.inferenceResult); }

  /** Verify chain link to previous record. Pass null for genesis check. */
  async verifyChainLink(prev) {
    if (!this._chainHash) await this.init();
    if (prev === null) {
      return this.prevPoACHash.every(b => b === 0);
    }
    if (!prev._chainHash) await prev.init();
    return this.prevPoACHash.every((b, i) => b === prev._chainHash[i]);
  }

  /** Hex string of record hash for display / API calls */
  recordHashHex() {
    return Array.from(this.recordHash).map(b => b.toString(16).padStart(2, "0")).join("");
  }
}

// ---------------------------------------------------------------------------
// VAPISession — primary integration surface
// ---------------------------------------------------------------------------

export class VAPISession {
  constructor({ bridgeUrl = "http://localhost:8080/v1", apiKey = "" } = {}) {
    this._bridgeUrl   = bridgeUrl;
    this._apiKey      = apiKey;
    this._records     = [];
    this._cheatCbs    = [];
    this._sessionId   = null;

    this.totalRecords    = 0;
    this.cleanRecords    = 0;
    this.cheatDetections = 0;
    this.advisoryRecords = 0;
  }

  /** Register a cheat-detection callback. Returns this for chaining. */
  onCheatDetected(cb) {
    this._cheatCbs.push(cb);
    return this;
  }

  /**
   * Ingest a raw 228-byte PoAC record.
   * @param {Uint8Array|ArrayBuffer} raw
   * @returns {Promise<VAPIRecord>}
   */
  async ingestRecord(raw) {
    const rec = await new VAPIRecord(raw).init();
    this._records.push(rec);
    this.totalRecords++;

    if (rec.isCheat) {
      this.cheatDetections++;
      for (const cb of this._cheatCbs) cb(rec);
    } else if (
      rec.inferenceResult === VAPI.INF.TEMPORAL_ANOMALY ||
      rec.inferenceResult === VAPI.INF.BIO_ANOMALY
    ) {
      this.advisoryRecords++;
    } else {
      this.cleanRecords++;
    }
    return rec;
  }

  /** Verify the entire ingested chain is unbroken. */
  async chainIntegrity() {
    if (this._records.length === 0) return true;
    if (!await this._records[0].verifyChainLink(null)) return false;
    for (let i = 1; i < this._records.length; i++) {
      if (!await this._records[i].verifyChainLink(this._records[i - 1])) return false;
    }
    return true;
  }

  summary() {
    return {
      total_records:    this.totalRecords,
      clean_records:    this.cleanRecords,
      cheat_detections: this.cheatDetections,
      advisory_records: this.advisoryRecords,
    };
  }

  // ---- REST helpers ----

  /** Submit a batch of records to the VAPI bridge. */
  async submitBatch(rawRecords) {
    const body = {
      device_id:      this._deviceId ?? "unknown",
      records_b64:    rawRecords.map(r => btoa(String.fromCharCode(...new Uint8Array(r)))),
      session_id:     this._sessionId,
      schema_version: 2,
    };
    return this._post("/records/batch", body);
  }

  /** POST /sessions/{id}/self-verify — returns SDKAttestation JSON */
  async selfVerifyRemote() {
    if (!this._sessionId) throw new Error("Create session first with createSession()");
    const resp = await fetch(`${this._bridgeUrl}/sessions/${this._sessionId}/self-verify`, {
      method: "POST",
      headers: this._authHeaders(),
    });
    if (!resp.ok) throw new Error(`self-verify failed: ${resp.status}`);
    return resp.json();
  }

  /** POST /sessions — create server-side session, store session_id */
  async createSession({ deviceId, profileId = "sony_dualshock_edge_v1", metadata = {} } = {}) {
    this._deviceId = deviceId;
    const body = { device_id: deviceId, profile_id: profileId, metadata };
    const resp = await this._post("/sessions", body);
    this._sessionId = resp.session_id;
    return resp;
  }

  _authHeaders() {
    const h = { "Content-Type": "application/json" };
    if (this._apiKey) h["Authorization"] = `Bearer ${this._apiKey}`;
    return h;
  }

  async _post(path, body) {
    const resp = await fetch(`${this._bridgeUrl}${path}`, {
      method:  "POST",
      headers: this._authHeaders(),
      body:    JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(`VAPI API error ${resp.status}: ${err.message ?? resp.statusText}`);
    }
    return resp.json();
  }
}

// ---------------------------------------------------------------------------
// Webhook signature verification (Node.js server / Edge runtime)
// ---------------------------------------------------------------------------

/**
 * Verify the X-VAPI-Signature header on incoming webhook payloads.
 * @param {string} rawBody   Raw request body string
 * @param {string} signature X-VAPI-Signature header value
 * @param {string} secret    Your webhook secret from VAPI dashboard
 * @returns {Promise<boolean>}
 */
export async function verifyWebhookSignature(rawBody, signature, secret) {
  const enc     = new TextEncoder();
  const keyData = enc.encode(secret);
  const msgData = enc.encode(rawBody);

  const cryptoKey = await crypto.subtle.importKey(
    "raw", keyData,
    { name: "HMAC", hash: "SHA-256" },
    false, ["verify"]
  );

  const sigBytes = Uint8Array.from(
    signature.match(/.{2}/g).map(h => parseInt(h, 16))
  );

  return crypto.subtle.verify("HMAC", cryptoKey, sigBytes, msgData);
}

// ---------------------------------------------------------------------------
// Quick demo (Node.js: node sdk/examples/web_integration.js)
// ---------------------------------------------------------------------------

async function demo() {
  console.log(`VAPI SDK ${VAPI.SDK_VERSION} — Web Integration Demo`);

  // Build a synthetic 228-byte record
  const raw = new Uint8Array(228);
  raw[128] = VAPI.INF.NOMINAL;  // inference = NOMINAL
  raw[130] = 220;               // confidence

  const rec = await new VAPIRecord(raw).init();
  console.log("inference   :", rec.inferenceName);
  console.log("is_clean    :", rec.isClean);
  console.log("record_hash :", rec.recordHashHex().slice(0, 32) + "...");

  // Session with cheat callback
  const session = new VAPISession();
  session.onCheatDetected(r => console.log("[CHEAT]", r.inferenceName));

  await session.ingestRecord(raw);

  // Inject a cheat record
  const cheatRaw = new Uint8Array(228);
  cheatRaw[128] = VAPI.INF.DRIVER_INJECT;
  cheatRaw[130] = 210;
  await session.ingestRecord(cheatRaw);

  console.log("summary:", session.summary());
  console.log("chain intact:", await session.chainIntegrity());
}

// Run demo if executed directly (Node.js)
if (typeof process !== "undefined" && process.argv[1]?.includes("web_integration")) {
  demo().catch(console.error);
}
