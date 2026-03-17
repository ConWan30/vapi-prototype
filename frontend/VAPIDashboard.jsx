/**
 * VAPI PROTOCOL DASHBOARD
 * Verified Autonomous Physical Intelligence — v3 Whitepaper Interface
 *
 * Aesthetic: "Classified Cryptographic Hardware Terminal"
 * — Forensic oscilloscope meets military targeting display
 * — Deep void-black + electric orange + cyan on JetBrains Mono
 * — Scan-line overlays, CRT flicker, grid-structure layouts
 * — Every data point sourced directly from whitepaper v3
 *
 * Phase 40: rhythm_hash 4-deque commit · L5 4-button + pooled IBI ·
 *           l5_source in PITL metadata · calibrator 4-button coverage
 */

import { useState, useEffect, useRef } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, Tooltip, Cell,
  LineChart, Line, Area, AreaChart, CartesianGrid,
} from "recharts";

/* ─── WHITEPAPER-ACCURATE DATA ──────────────────────────────────────────── */

const PITL_LAYERS = [
  { id: "L0",  name: "Physical Presence",      type: "STRUCTURAL", code: "—",      signal: "HID-connected, live input",            status: "ACTIVE",  margin: null,  detail: "Controller must be HID-connected with live input stream" },
  { id: "L1",  name: "PoAC Chain Integrity",   type: "STRUCTURAL", code: "—",      signal: "SHA-256 linkage, monotonic counter",    status: "ACTIVE",  margin: null,  detail: "Hash-chain linkage + monotonic counter + timestamp freshness" },
  { id: "L2",  name: "HID Injection Oracle",   type: "HARD CHEAT", code: "0x28",   signal: "IMU gravity + gyro noise floor",        status: "ACTIVE",  margin: 14000, detail: "Gyro std < 0.001 rad/s threshold. Live margin: 10,000× on active play" },
  { id: "L2B", name: "IMU-Button Coupling",    type: "ADVISORY",   code: "0x31",   signal: "5–80ms precursor window",              status: "ACTIVE",  margin: null,  detail: "IMU micro-disturbance absent before button rising edge → decoupled. Threshold: coupled_fraction < 0.55" },
  { id: "L2C", name: "Stick-IMU Correlation",  type: "ADVISORY",   code: "0x32",   signal: "Pearson cross-corr 10–60ms",           status: "ACTIVE",  margin: null,  detail: "abs(max_causal_corr) of stick velocity vs. gyro_z at causal lags. Threshold < 0.15. abs() mandatory — anti-correlation is physical coupling. Dead-zone stick games (e.g. NCAA CFB 26): right stick stays at 128 → oracle returns None → p_L2C = 0.5 neutral prior. Formula runs as effective 4-signal; l2c_inactive flag emitted in PITL metadata and visible in HUMANITY tile." },
  { id: "L3",  name: "Behavioral ML",          type: "HARD CHEAT", code: "0x29/2A",signal: "9-feature temporal classifier",        status: "ACTIVE",  margin: null,  detail: "30→64→32→6 INT8 net. Targets MACRO (σ² < 1.0ms²) + AIMBOT (jerk > 2.0)" },
  { id: "L4",  name: "Biometric Fingerprint",  type: "ADVISORY",   code: "0x30",   signal: "12-feature Mahalanobis (Phase 46/57)", status: "ACTIVE",  margin: null,  detail: "Anomaly threshold: 6.726 (mean+3σ, N=74, Phase 46). Continuity: 5.097. Index 9: accel_magnitude_spectral_entropy (bot-vs-human, gravity-invariant, 1000Hz-exclusive). Index 11: press_timing_jitter_variance (Phase 57) — normalised IBI variance; human 0.001–0.05, bot macro <0.00005. Zero-variance features auto-excluded (ZERO_VAR_THRESHOLD=1e-4)." },
  { id: "L5",  name: "Temporal Rhythm",        type: "ADVISORY",   code: "0x2B",   signal: "4-btn IBI · CV · entropy · 60Hz quant",status: "ACTIVE",  margin: null,  detail: "Phase 39: 4-button priority Cross(1.373)>L2_dig(1.333)>R2(1.176)>Triangle(1.138). Pooled IBI fallback ≥5 samples/button. Fires on ≥2/3: CV<0.08, entropy<1.0bit, quant>0.55. l5_source persisted in PITL metadata." },
  { id: "L6",  name: "Active Haptic C-R",      type: "ADVISORY",   code: "—",      signal: "Motorized trigger resistance",         status: "CALIBRATED", margin: null,  detail: "6 profiles calibrated (Phase 43, N=43 captures, PROFILE_VERSION 2). Per-profile onset_ms/settle_ms thresholds wired. DISABLED — L6_CHALLENGES_ENABLED=false. RIGID_MAX uncalibrated (N=2)." },
];

const ADVERSARIAL_DATA = [
  { attack: "IMU Injection",     detection: 100, n: 10, layer: "L2",      color: "#00ff88" },
  { attack: "Timing Macro",      detection: 100, n: 10, layer: "L5",      color: "#00ff88" },
  { attack: "Quant-Masked Bot",  detection: 100, n: 15, layer: "L5",      color: "#00ff88" },
  { attack: "Warmup Attack",     detection: 60,  n: 10, layer: "L5+Arch", color: "#ff9500" },
  { attack: "Replay (Chain)",    detection: 20,  n: 5,  layer: "L1",      color: "#ff9500" },
  { attack: "Bio Transplant",    detection: 0,   n: 5,  layer: "L4",           color: "#ff2d55" },
  { attack: "Randomized IMU Bot", detection: 0,  n: 5,  layer: "L4+L2B (live)", color: "#ff9500" },
  { attack: "Threshold-Aware Bot",detection: 100, n: 5, layer: "L4",            color: "#00ff88" },
  { attack: "Spectral Mimicry",  detection: 0,   n: 5,  layer: "L2B (live)",    color: "#ff9500" },
];

const HARDWARE_METRICS = [
  { metric: "USB Polling Rate",          value: "1,002 Hz",      spec: "1000 Hz ±15%",  pass: true },
  { metric: "Injection Det. Margin",     value: "14,000×",       spec: "—",             pass: true },
  { metric: "Gyro Margin (Active)",      value: "10,000×",       spec: "> 0.02 LSB",    pass: true },
  { metric: "Accel Variance (Held)",     value: "278,239 LSB²",  spec: "> 0",           pass: true },
  { metric: "Gyro Std (Active)",         value: "201.65 LSB",    spec: "> 0.02 LSB",    pass: true },
  { metric: "Gyro Std (Stationary)",     value: "< 50 LSB",      spec: "< 50 LSB",      pass: true },
  { metric: "Report Counter Violations", value: "0 / 200",       spec: "0",             pass: true },
  { metric: "L4 FP Rate",               value: "2.9%",          spec: "~3σ expected",  pass: true },
];

const CALIBRATION = {
  sessions: 74,
  players: 3,
  l4Anomaly: 6.726,
  l4Continuity: 5.097,
  l5CV: 0.08,
  l5Entropy: 1.0,
  l2bCoupled: 0.55,
  l2cMaxCorr: 0.15,
  separationRatio: 0.362,
  humanCVMean: 1.184,
  humanEntropyMean: 2.085,
  // Phase 39/40 additions
  l5Buttons: 4,
  l5PoolMinPerButton: 5,
  l5CrossCoverage: 0.838,    // 62/74 sessions (N=74 calibrator run)
  rhythmHashDeques: 4,
  l5SourcePersisted: true,
};

const L5_BUTTON_COVERAGE = [
  { button: "Cross",    cv: 1.523, sessions: 62, pct: 83.8, color: "#ff6b00" },
  { button: "L2_dig",  cv: 1.657, sessions: 14, pct: 18.9, color: "#ff9500" },
  { button: "R2",      cv: 1.181, sessions: 52, pct: 70.3, color: "#00d4ff" },
  { button: "Triangle",cv: 1.360, sessions: 14, pct: 18.9, color: "#c4cdd6" },
];

const RADAR_DATA = [
  { feature: "Trigger Onset",   score: 82 },
  { feature: "Micro-Tremor",    score: 91 },
  { feature: "Grip Asymmetry",  score: 76 },
  { feature: "Stick Autocorr",  score: 88 },
  { feature: "Tremor FFT",      score: 79 },
  { feature: "Temporal CV",     score: 94 },
  { feature: "Entropy",         score: 87 },
  { feature: "IMU Coupling",    score: 93 },
  { feature: "Spectral Entropy", score: 72 },  // accel_magnitude_spectral_entropy; Phase 46, N=74, bot-vs-human detection effectiveness
];

const CONTRACT_STACK = [
  { name: "PoACVerifier",        addr: "0x26178AD9…", gas: "81,245",  status: "LIVE" },
  { name: "PHGCredential",       addr: "0x0Af852f5…", gas: "110,000", status: "LIVE" },
  { name: "TournamentGateV3",    addr: "0x6fEe9F6f…", gas: "~72,000", status: "LIVE" },
  { name: "PITLSessionRegistry", addr: "0x8da0A497…", gas: "—",       status: "LIVE" },
  { name: "FederatedThreatReg",  addr: "0xe837FaB5…", gas: "65,000",  status: "LIVE" },
  { name: "SkillOracle",         addr: "0xABA74481…", gas: "—",       status: "LIVE" },
];

const MODE6_DATA = Array.from({ length: 24 }, (_, i) => ({
  cycle: `C${i + 1}`,
  anomaly:    +(6.726 + (Math.sin(i * 0.4) * 0.3 + (i > 12 ? -0.08 * (i - 12) : 0))).toFixed(3),
  continuity: +(5.097 + (Math.cos(i * 0.4) * 0.2)).toFixed(3),
}));

/* ─── UTILITY HOOKS ──────────────────────────────────────────────────────── */

function useCounter(target, duration = 1800) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    let start = null;
    const tick = (ts) => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / duration, 1);
      setVal(Math.floor(p * target));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [target, duration]);
  return val;
}

function usePulse(interval = 2400) {
  const [pulse, setPulse] = useState(false);
  useEffect(() => {
    const t = setInterval(() => {
      setPulse(true);
      setTimeout(() => setPulse(false), 300);
    }, interval);
    return () => clearInterval(t);
  }, [interval]);
  return pulse;
}

/* ─── BRIDGE CONNECTION ──────────────────────────────────────────────────── */

const BRIDGE_URL = "http://localhost:8080";

function useBridgeData() {
  const [snapshot, setSnapshot] = useState(null);
  const [mode,     setMode]     = useState("DEMO"); // "LIVE" | "DEMO"
  const [records,  setRecords]  = useState([]);
  const wsRef = useRef(null);
  const fetchAbortRef = useRef(null);        // Phase 54: cancel in-flight fetch on rapid events
  const reconnectDelayRef = useRef(5000);    // Phase 54: exponential backoff state

  useEffect(() => {
    let active = true;
    let pollTimer = null;

    async function fetchSnapshot() {
      // Phase 54: cancel any in-flight fetch before launching a new one
      if (fetchAbortRef.current) fetchAbortRef.current.abort();
      fetchAbortRef.current = new AbortController();
      try {
        const res = await fetch(`${BRIDGE_URL}/dashboard/snapshot`,
          { signal: fetchAbortRef.current.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (active) { setSnapshot(data); setMode("LIVE"); }
      } catch (e) {
        if (e?.name === "AbortError") return; // cancelled by newer fetch — ignore
        if (active) setMode(prev => prev === "LIVE" ? "LIVE" : "DEMO");
      }
    }

    fetchSnapshot();
    pollTimer = setInterval(fetchSnapshot, 30000);

    function connectWs() {
      try {
        const ws = new WebSocket(`ws://localhost:8080/ws/records`);
        ws.onopen    = () => {
          reconnectDelayRef.current = 5000;  // Phase 54: reset backoff on successful connect
          if (active) setMode("LIVE");
        };
        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data);
            if (msg.type === "controller_registered") {
              // Controller is live — force a snapshot refresh to update hardware.controller_connected
              if (active) fetchSnapshot();
            } else {
              if (active) setRecords(prev => [{ ...msg, _ts: Date.now() }, ...prev].slice(0, 10));
            }
          } catch {}
        };
        ws.onerror = () => {};
        ws.onclose = () => {
          if (active) {
            // Phase 54: exponential backoff — 5s → 10s → 30s → 60s cap
            const delay = reconnectDelayRef.current;
            reconnectDelayRef.current = Math.min(delay * 2, 60000);
            setTimeout(connectWs, delay);
          }
        };
        wsRef.current = ws;
      } catch {}
    }
    connectWs();

    return () => {
      active = false;
      clearInterval(pollTimer);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  return { snapshot, mode, records };
}

// Phase 44: raw frame stream hook — connects to /ws/frames (20 Hz batches)
function useFrameData(enabled) {
  const [accelHistory, setAccelHistory] = useState([]);
  const [latestFrame,  setLatestFrame]  = useState(null);
  const wsRef = useRef(null);
  const reconnectDelayRef = useRef(5000);  // Phase 54: exponential backoff state

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    function connect() {
      try {
        const ws = new WebSocket(`ws://localhost:8080/ws/frames`);
        ws.onopen  = () => { reconnectDelayRef.current = 5000; }; // Phase 54: reset on connect
        ws.onmessage = (e) => {
          if (!active) return;
          try {
            const msg = JSON.parse(e.data);
            if (msg.type !== "frames" || !msg.frames?.length) return;
            const last = msg.frames[msg.frames.length - 1];
            setLatestFrame(last);
            setAccelHistory(prev => {
              const next = [...prev, ...msg.frames.map((f, i) => ({
                t: prev.length + i,
                v: f.accel_mag ?? 0,
              }))].slice(-60);
              return next;
            });
          } catch {}
        };
        ws.onerror = () => {};
        ws.onclose = () => {
          if (active) {
            // Phase 54: exponential backoff — 5s → 10s → 30s → 60s cap
            const delay = reconnectDelayRef.current;
            reconnectDelayRef.current = Math.min(delay * 2, 60000);
            setTimeout(connect, delay);
          }
        };
        wsRef.current = ws;
      } catch {}
    }
    connect();
    return () => { active = false; if (wsRef.current) wsRef.current.close(); };
  }, [enabled]);

  return { accelHistory, latestFrame };
}

/* ─── SUB-COMPONENTS ─────────────────────────────────────────────────────── */

function ScanLines() {
  return (
    <div style={{
      position: "fixed", inset: 0, pointerEvents: "none", zIndex: 9999,
      backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.06) 2px, rgba(0,0,0,0.06) 4px)",
    }} />
  );
}

function GridNoise() {
  return (
    <div style={{
      position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
      backgroundImage: `
        linear-gradient(rgba(255,107,0,0.015) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,107,0,0.015) 1px, transparent 1px)
      `,
      backgroundSize: "40px 40px",
    }} />
  );
}

function Badge({ type }) {
  const styles = {
    "STRUCTURAL": { bg: "rgba(0,212,255,0.12)", color: "#00d4ff", border: "1px solid rgba(0,212,255,0.3)" },
    "HARD CHEAT": { bg: "rgba(255,45,85,0.12)",  color: "#ff2d55", border: "1px solid rgba(255,45,85,0.3)" },
    "ADVISORY":   { bg: "rgba(255,149,0,0.12)",  color: "#ff9500", border: "1px solid rgba(255,149,0,0.3)" },
  };
  const s = styles[type] || styles["ADVISORY"];
  return (
    <span style={{
      ...s, fontSize: 9, fontFamily: "'JetBrains Mono', monospace",
      padding: "2px 6px", borderRadius: 2, letterSpacing: "0.08em", whiteSpace: "nowrap",
    }}>
      {type}
    </span>
  );
}

function StatusDot({ status }) {
  const color = status === "ACTIVE" ? "#00ff88" : status === "PENDING" ? "#ff9500" : status === "INACTIVE" ? "#ff9500" : status === "CALIBRATED" ? "#ff9500" : "#ff2d55";
  return (
    <span style={{
      display: "inline-block", width: 7, height: 7, borderRadius: "50%",
      background: color, boxShadow: `0 0 8px ${color}`, flexShrink: 0,
      animation: status === "ACTIVE" ? "statusPulse 2.4s ease-in-out infinite" : "none",
    }} />
  );
}

function SectionLabel({ children }) {
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
      color: "#ff6b00", letterSpacing: "0.25em", textTransform: "uppercase",
      marginBottom: 16, display: "flex", alignItems: "center", gap: 10,
    }}>
      <span style={{ width: 20, height: 1, background: "#ff6b00", opacity: 0.6, display: "inline-block" }} />
      {children}
      <span style={{ flex: 1, height: 1, background: "linear-gradient(90deg, rgba(255,107,0,0.4), transparent)", display: "inline-block" }} />
    </div>
  );
}

function Panel({ children, style = {} }) {
  return (
    <div style={{
      background: "rgba(8,15,20,0.85)",
      border: "1px solid rgba(255,107,0,0.18)",
      borderRadius: 2,
      padding: 20,
      backdropFilter: "blur(4px)",
      position: "relative",
      ...style,
    }}>
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0, height: 1,
        background: "linear-gradient(90deg, transparent, rgba(255,107,0,0.5), transparent)",
      }} />
      {children}
    </div>
  );
}

function StatBox({ label, value, sub, accent = "#ff6b00", mono = false }) {
  return (
    <div style={{
      padding: "14px 16px",
      background: "rgba(255,107,0,0.04)",
      border: "1px solid rgba(255,107,0,0.12)",
      borderRadius: 2,
    }}>
      <div style={{
        fontFamily: mono ? "'JetBrains Mono', monospace" : "'Rajdhani', sans-serif",
        fontSize: mono ? 22 : 26,
        fontWeight: 700,
        color: accent,
        lineHeight: 1,
        letterSpacing: mono ? "0.04em" : "0",
      }}>{value}</div>
      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74", marginTop: 6, letterSpacing: "0.15em", textTransform: "uppercase" }}>{label}</div>
      {sub && <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

/* ─── SECTION: PITL STACK ────────────────────────────────────────────────── */

function PITLStack({ l6Status, l2cInactive }) {
  const [active, setActive] = useState(null);
  return (
    <Panel>
      <SectionLabel>Physical Input Trust Layer — 9-Level Stack</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {PITL_LAYERS.map((layer, i) => {
          let liveStatus = layer.status;
          if (layer.id === "L6" && l6Status !== undefined) liveStatus = l6Status;
          if (layer.id === "L2C" && l2cInactive === true) liveStatus = "INACTIVE";
          const layer_ = { ...layer, status: liveStatus };
          return (
          <div
            key={layer.id}
            onClick={() => setActive(active === i ? null : i)}
            style={{
              display: "grid",
              gridTemplateColumns: "44px 1fr auto auto",
              alignItems: "center",
              gap: 12,
              padding: "10px 12px",
              background: active === i ? "rgba(255,107,0,0.07)" : "rgba(255,255,255,0.02)",
              border: `1px solid ${active === i ? "rgba(255,107,0,0.3)" : "rgba(255,255,255,0.05)"}`,
              borderRadius: 2,
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: "#ff6b00", fontWeight: 700 }}>{layer.id}</div>
            <div>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 13, fontWeight: 600, color: "#c4cdd6", marginBottom: 2 }}>{layer.name}</div>
              {active === i && (
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: "#5a6a74", marginTop: 4, lineHeight: 1.5 }}>
                  {layer.detail}
                  {layer.margin && <span style={{ color: "#ff9500", marginLeft: 8 }}>↑ {layer.margin.toLocaleString()}× margin</span>}
                </div>
              )}
              {(!active || active !== i) && (
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>{layer.signal}</div>
              )}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Badge type={layer.type} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <StatusDot status={layer_.status} />
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: layer_.status === "PENDING" ? "#ff9500" : layer_.status === "INACTIVE" ? "#ff9500" : layer_.status === "CALIBRATED" ? "#ff9500" : layer_.status === "DISABLED" ? "#ff2d55" : "#3d5060" }}>{layer_.status === "INACTIVE" ? "INACTIVE (dead zone)" : layer_.status}</span>
            </div>
          </div>
          );
        })}
      </div>
      <div style={{ marginTop: 12, fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
        ↑ click any layer to expand · L6 disabled by default (L6_CHALLENGES_ENABLED=false) · inference codes committed to PoAC chain
      </div>
    </Panel>
  );
}

/* ─── SECTION: L5 BUTTON COVERAGE (Phase 39/40) ─────────────────────────── */

function L5ButtonCoverage() {
  return (
    <Panel>
      <SectionLabel>L5 Multi-Button IBI Coverage — Phase 39 · N=74 Sessions</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {L5_BUTTON_COVERAGE.map((b) => (
          <div key={b.button} style={{ display: "grid", gridTemplateColumns: "80px 1fr 90px 60px", gap: 10, alignItems: "center" }}>
            <div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: b.color, fontWeight: 700 }}>{b.button}</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>CV={b.cv.toFixed(3)}</div>
            </div>
            <div style={{ position: "relative", height: 6, background: "rgba(255,255,255,0.05)", borderRadius: 1 }}>
              <div style={{
                position: "absolute", left: 0, top: 0, height: "100%",
                width: `${b.pct}%`,
                background: b.color,
                borderRadius: 1,
                boxShadow: `0 0 8px ${b.color}40`,
              }} />
            </div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700, color: b.color, textAlign: "right" }}>
              {b.pct}%
            </div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
              {b.sessions}/74 sess
            </div>
          </div>
        ))}
      </div>
      <div style={{
        marginTop: 12, padding: "10px 12px",
        background: "rgba(0,212,255,0.04)",
        border: "1px solid rgba(0,212,255,0.1)",
        borderRadius: 2,
        fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74",
      }}>
        <span style={{ color: "#00d4ff" }}>priority:</span> Cross→L2_dig→R2→Triangle (IBI-CV descending) ·{" "}
        <span style={{ color: "#ff6b00" }}>pooled fallback:</span> ≥5 samples/button merged when no single button ≥20 ·{" "}
        <span style={{ color: "#00d4ff" }}>rhythm_hash:</span> SHA-256(Cross‖L2‖R2‖Triangle) with 0xFFFFFFFF separator · source persisted in PITL metadata
      </div>
    </Panel>
  );
}

/* ─── SECTION: ADVERSARIAL MATRIX ───────────────────────────────────────── */

function AdversarialMatrix() {
  return (
    <Panel>
      <SectionLabel>Adversarial Detection Matrix — Real Hardware N=55</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {ADVERSARIAL_DATA.map((d) => (
          <div key={d.attack} style={{ display: "grid", gridTemplateColumns: "160px 1fr 60px 80px", gap: 12, alignItems: "center" }}>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: "#8a9baa" }}>{d.attack}</div>
            <div style={{ position: "relative", height: 6, background: "rgba(255,255,255,0.05)", borderRadius: 1 }}>
              <div style={{
                position: "absolute", left: 0, top: 0, height: "100%",
                width: `${d.detection}%`,
                background: d.color,
                borderRadius: 1,
                boxShadow: `0 0 8px ${d.color}40`,
                transition: "width 1s ease",
              }} />
            </div>
            <div style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 13, fontWeight: 700,
              color: d.color, textAlign: "right",
            }}>
              {d.detection}%
            </div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
              N={d.n} · {d.layer}
            </div>
          </div>
        ))}
      </div>
      <div style={{
        marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
        gap: 8, borderTop: "1px solid rgba(255,107,0,0.1)", paddingTop: 12,
      }}>
        {[["#00ff88", "100% — Injection / Macro / Quant"], ["#ff9500", "60% — Warmup (sessions 1–6)"], ["#ff2d55", "0% — Transplant (architectural)"]].map(([color, label]) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: 1, background: color, flexShrink: 0 }} />
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74" }}>{label}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ─── SECTION: BIOMETRIC RADAR ───────────────────────────────────────────── */

function BiometricRadar() {
  return (
    <Panel>
      <SectionLabel>L4 Biometric Feature Space — 11-Signal Mahalanobis</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, alignItems: "center" }}>
        <div style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={RADAR_DATA} margin={{ top: 10, right: 10, bottom: 10, left: 10 }}>
              <PolarGrid stroke="rgba(255,107,0,0.15)" />
              <PolarAngleAxis dataKey="feature" tick={{ fontSize: 9, fontFamily: "'JetBrains Mono', monospace", fill: "#5a6a74" }} />
              <Radar name="Calibrated" dataKey="score" stroke="#ff6b00" fill="#ff6b00" fillOpacity={0.12} strokeWidth={1.5} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {[
            ["Anomaly Threshold",  "6.726",  "mean+3σ (N=74)"],
            ["Continuity Thresh",  "5.097",  "mean+2σ"],
            ["Dist Mean",          "1.839",  "across N=74"],
            ["Dist Std",           "1.629",  ""],
            ["Separation Ratio",   "0.362",  "inter-person ⚠"],
            ["False Positive Rate","2.9%",   "3/74 sessions"],
          ].map(([label, val, note]) => (
            <div key={label} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", borderBottom: "1px solid rgba(255,255,255,0.04)", paddingBottom: 5 }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74" }}>{label}</span>
              <div style={{ textAlign: "right" }}>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: val === "0.362" ? "#ff9500" : "#c4cdd6", fontWeight: 700 }}>{val}</span>
                {note && <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginLeft: 6 }}>{note}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 12, fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
        2 features structurally zero: trigger_resistance_change_rate, touch_position_variance — auto-excluded via ZERO_VAR_THRESHOLD=1e-4. accel_magnitude_spectral_entropy (index 9, Phase 46): active N=74 sessions, mean 4.93 bits, bot-vs-human only — does NOT improve separation ratio 0.362. L4 is intra-player anomaly detector, not inter-player identifier.
      </div>
    </Panel>
  );
}

/* ─── SECTION: MODE 6 LIVING CALIBRATION ────────────────────────────────── */

function LivingCalibration({ chartData }) {
  const data = (chartData && chartData.length > 0) ? chartData : MODE6_DATA;
  return (
    <Panel>
      <SectionLabel>Mode 6 — Living Calibration (Phase 38) · α=0.95 · ±15%/cycle</SectionLabel>
      <div style={{ height: 140 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            <defs>
              <linearGradient id="anomalyGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#ff6b00" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#ff6b00" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="contGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#00d4ff" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(255,107,0,0.06)" strokeDasharray="3 3" />
            <XAxis dataKey="cycle" tick={{ fontSize: 8, fontFamily: "'JetBrains Mono', monospace", fill: "#3d5060" }} tickLine={false} />
            <YAxis domain={[4.8, 7.8]} tick={{ fontSize: 8, fontFamily: "'JetBrains Mono', monospace", fill: "#3d5060" }} tickLine={false} />
            <Tooltip
              contentStyle={{ background: "#080f14", border: "1px solid rgba(255,107,0,0.3)", borderRadius: 2, fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
              labelStyle={{ color: "#ff6b00" }}
              itemStyle={{ color: "#c4cdd6" }}
            />
            <Area type="monotone" dataKey="anomaly"    stroke="#ff6b00" strokeWidth={1.5} fill="url(#anomalyGrad)" name="Anomaly Thresh" />
            <Area type="monotone" dataKey="continuity" stroke="#00d4ff" strokeWidth={1.5} fill="url(#contGrad)"    name="Continuity Thresh" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 20 }}>
        {[["#ff6b00", "Anomaly Threshold (6.726)"], ["#00d4ff", "Continuity Threshold (5.097)"]].map(([c, l]) => (
          <div key={l} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 16, height: 2, background: c, display: "inline-block" }} />
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74" }}>{l}</span>
          </div>
        ))}
        <span style={{ marginLeft: "auto", fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
          evolves every 6h · floor 3.0 · per-player profiles ≥30 NOMINAL records
        </span>
      </div>
    </Panel>
  );
}

/* ─── SECTION: HARDWARE METRICS ─────────────────────────────────────────── */

function HardwareMetrics() {
  return (
    <Panel>
      <SectionLabel>Live Hardware Measurements — DualShock Edge CFI-ZCP1 USB</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
        {HARDWARE_METRICS.map((m) => (
          <div key={m.metric} style={{
            display: "grid", gridTemplateColumns: "1fr auto",
            gap: 8, alignItems: "center",
            padding: "8px 10px",
            background: "rgba(255,255,255,0.02)",
            border: "1px solid rgba(255,255,255,0.04)",
            borderRadius: 2,
          }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74" }}>{m.metric}</span>
            <span style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700,
              color: m.pass ? "#00ff88" : "#ff2d55",
            }}>{m.value}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ─── SECTION: CONTRACT STACK ────────────────────────────────────────────── */

function ContractStack() {
  return (
    <Panel>
      <SectionLabel>IoTeX Testnet — 13 Contracts Deployed</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {CONTRACT_STACK.map((c) => (
          <div key={c.name} style={{
            display: "grid", gridTemplateColumns: "1fr 60px 60px",
            gap: 12, alignItems: "center",
            padding: "7px 10px",
            background: "rgba(0,212,255,0.03)",
            border: "1px solid rgba(0,212,255,0.08)",
            borderRadius: 2,
          }}>
            <div>
              <span style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 12, fontWeight: 600, color: "#c4cdd6" }}>{c.name}</span>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginLeft: 8 }}>{c.addr}</span>
            </div>
            {c.gas !== "—" && <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74", textAlign: "right" }}>{c.gas} gas</span>}
            {c.gas === "—" && <span />}
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#00ff88", textAlign: "right" }}>● {c.status}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
        P256 precompile at 0x0100 · batch(10) = $0.00024 · Groth16 verifier active @ 0x07D3ca15…
      </div>
    </Panel>
  );
}

/* ─── SECTION: PoAC WIRE FORMAT ─────────────────────────────────────────── */

function PoACRecord() {
  const fields = [
    { offset: "0x00", field: "prev_poac_hash",    size: 32, color: "#ff6b00", desc: "SHA-256 of prev 164B body" },
    { offset: "0x20", field: "sensor_commitment",  size: 32, color: "#ff9500", desc: "H(raw_sensor_buffer)" },
    { offset: "0x40", field: "model_manifest_hash",size: 32, color: "#ffcc00", desc: "H(weights ‖ version ‖ arch_id)" },
    { offset: "0x60", field: "world_model_hash",   size: 32, color: "#00d4ff", desc: "H(W) — state before update" },
    { offset: "0x80", field: "inference_result",   size: 1,  color: "#00ff88", desc: "Encoded classification" },
    { offset: "0x81", field: "action_code",        size: 1,  color: "#00ff88", desc: "Agent action" },
    { offset: "0x82", field: "confidence",         size: 1,  color: "#00ff88", desc: "[0, 255]" },
    { offset: "0x83", field: "battery_pct",        size: 1,  color: "#00ff88", desc: "[0, 100]" },
    { offset: "0x84", field: "monotonic_ctr",      size: 4,  color: "#c4cdd6", desc: "Strictly increasing (BE)" },
    { offset: "0x88", field: "timestamp_ms",       size: 8,  color: "#c4cdd6", desc: "Unix epoch ms" },
    { offset: "0x90", field: "latitude",           size: 8,  color: "#8a9baa", desc: "WGS84" },
    { offset: "0x98", field: "longitude",          size: 8,  color: "#8a9baa", desc: "WGS84" },
    { offset: "0xA0", field: "bounty_id",          size: 4,  color: "#5a6a74", desc: "On-chain bounty reference" },
    { offset: "0xA4", field: "signature",          size: 64, color: "#ff2d55", desc: "ECDSA-P256 r ‖ s" },
  ];
  const total = fields.reduce((s, f) => s + f.size, 0);

  return (
    <Panel>
      <SectionLabel>PoAC Wire Format — 228 Bytes FROZEN</SectionLabel>
      <div style={{ display: "flex", height: 28, borderRadius: 2, overflow: "hidden", marginBottom: 16, border: "1px solid rgba(255,255,255,0.05)" }}>
        {fields.map((f) => (
          <div
            key={f.field}
            title={`${f.field} (${f.size}B)`}
            style={{
              width: `${(f.size / total) * 100}%`,
              background: f.color,
              opacity: 0.7,
              borderRight: "1px solid rgba(0,0,0,0.3)",
              minWidth: f.size >= 8 ? 2 : 1,
              transition: "opacity 0.15s",
              cursor: "default",
            }}
          />
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "55px 1fr auto 200px", gap: "6px 12px", alignItems: "start" }}>
        {fields.slice(0, 8).map((f) => (
          <>
            <span key={f.offset + "o"} style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>{f.offset}</span>
            <span key={f.offset + "f"} style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: f.color, fontWeight: 700 }}>{f.field}</span>
            <span key={f.offset + "s"} style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", textAlign: "right" }}>{f.size}B</span>
            <span key={f.offset + "d"} style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>{f.desc}</span>
          </>
        ))}
      </div>
      <div style={{ marginTop: 10, fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", borderTop: "1px solid rgba(255,107,0,0.1)", paddingTop: 8 }}>
        164B signed body + 64B ECDSA-P256 = 228B total · record_hash = SHA-256(raw[0:164]) · fits single NB-IoT uplink frame ·{" "}
        <span style={{ color: "#ff6b00" }}>Phase 40:</span> rhythm_hash = SHA-256(Cross‖0xFFFF‖L2‖0xFFFF‖R2‖0xFFFF‖Triangle)
      </div>
    </Panel>
  );
}

/* ─── SECTION: ZK PROOF STATUS ───────────────────────────────────────────── */

function ZKProofStatus() {
  const constraints = useCounter(1820, 1200);
  return (
    <Panel>
      <SectionLabel>Groth16 ZK PITL Session Proof — BN254</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {[
              ["Circuit Constraints", `~${constraints.toLocaleString()}`, "#ff6b00"],
              ["Powers-of-Tau",       "2^11",                            "#ff9500"],
              ["Public Inputs",       "5",                               "#c4cdd6"],
              ["Ceremony Type",       "SINGLE-CONTRIB ⚠",                "#ff9500"],
            ].map(([l, v, c]) => (
              <div key={l} style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid rgba(255,255,255,0.04)", paddingBottom: 5 }}>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74" }}>{l}</span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: c, fontWeight: 700 }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74", marginBottom: 8 }}>PUBLIC INPUTS (5)</div>
          {[
            ["featureCommitment", "Poseidon(7)(features[0..6])"],
            ["humanityProbInt",   "prob × 1000 ∈ [0,1000]"],
            ["inferenceResult",   "pub[2]=inferenceCode · C2 circuit bound (Phase 41)"],
            ["nullifierHash",     "Poseidon(deviceIdHash, epoch)"],
            ["epoch",             "block.number / EPOCH_BLOCKS"],
          ].map(([k, v]) => (
            <div key={k} style={{ marginBottom: 5 }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#ff6b00" }}>{k}</span>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginTop: 1 }}>{v}</div>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}

/* ─── SECTION: PHG CREDENTIAL ────────────────────────────────────────────── */

function PHGCredential({ phgScore }) {
  const scoreTarget = (phgScore !== undefined && phgScore !== null) ? Math.round(phgScore) : 847;
  const score = useCounter(scoreTarget, 2000);
  return (
    <Panel>
      <SectionLabel>PHG Humanity Credential — Soulbound ERC-5192</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 20, alignItems: "center" }}>
        <div style={{ textAlign: "center" }}>
          <div style={{
            width: 80, height: 80, borderRadius: "50%",
            border: "2px solid #00ff88",
            boxShadow: "0 0 24px rgba(0,255,136,0.3), inset 0 0 24px rgba(0,255,136,0.05)",
            display: "flex", alignItems: "center", justifyContent: "center",
            flexDirection: "column",
            position: "relative",
          }}>
            <div style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 22, fontWeight: 700, color: "#00ff88", lineHeight: 1 }}>{score}</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 7, color: "#5a6a74", marginTop: 2 }}>PHG SCORE</div>
            <div style={{ position: "absolute", inset: -4, borderRadius: "50%", border: "1px solid rgba(0,255,136,0.15)", animation: "spinSlow 12s linear infinite" }} />
          </div>
          <div style={{ marginTop: 6, fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#00ff88" }}>● STABLE</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74", marginBottom: 4 }}>
            p_human = 0.28·p_L4 + 0.27·p_L5 + 0.20·p_E4 + 0.15·p_L2B + 0.10·p_L2C
          </div>
          {[
            ["L4 Biometric (0.28)",  "0.87", "#ff6b00"],
            ["L5 Temporal (0.27)",   "0.94", "#ff9500"],
            ["Cog Stability (0.20)", "0.91", "#ffcc00"],
            ["IMU-Button (0.15)",    "0.89", "#00d4ff"],
            ["Stick-IMU (0.10)",     "0.82", "#00d4ff"],
          ].map(([label, val, color]) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#5a6a74", width: 140, flexShrink: 0 }}>{label}</span>
              <div style={{ flex: 1, height: 4, background: "rgba(255,255,255,0.05)", borderRadius: 1 }}>
                <div style={{ width: `${parseFloat(val) * 100}%`, height: "100%", background: color, borderRadius: 1, boxShadow: `0 0 6px ${color}60` }} />
              </div>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color, width: 36, textAlign: "right" }}>{val}</span>
            </div>
          ))}
          <div style={{ marginTop: 4, fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060" }}>
            L6 reweighting: 0.23·p_L4+0.22·p_L5+0.15·p_E4+0.15·p_L6+0.15·p_L2B+0.10·p_L2C (when enabled) ·{" "}
            <span style={{ color: "#ff6b00" }}>l5_source</span> ('cross'|'l2_dig'|'r2'|'triangle'|'pooled') stored in PITL metadata
          </div>
        </div>
      </div>
      <div style={{
        marginTop: 14, display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
        gap: 8, borderTop: "1px solid rgba(255,107,0,0.1)", paddingTop: 12,
      }}>
        {[["STABLE", "#00ff88", "Accumulating humanity"], ["SUSPENDED", "#ff2d55", "≥2 consecutive critical"], ["CLEARED", "#00d4ff", "Auto-reinstated"]].map(([state, color, desc]) => (
          <div key={state} style={{ padding: "8px", background: "rgba(255,255,255,0.02)", border: `1px solid ${color}22`, borderRadius: 2 }}>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color, marginBottom: 3 }}>● {state}</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>{desc}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ─── SECTION: OPEN ITEMS ────────────────────────────────────────────────── */

function OpenItems() {
  const items = [
    { id: "P1", label: "L6 Human Response Baseline",         status: "COMPLETE", priority: "—",      detail: "N=43 captures · per-profile onset_ms/settle_ms thresholds wired (PROFILE_VERSION 2) · l6_threshold_calibrator.py --from-db · Phase 43" },
    { id: "P2", label: "Inter-Player Separation Improvement", status: "OPEN",     priority: "HIGH",   detail: "requires post-Phase-17 touchpad recapture (hardware + gameplay) · touch_position_variance structurally zero across all N=74 sessions · separation ratio 0.362 · tremor FFT widened (Phase 49) · touchpad recapture is the remaining blocker" },
    { id: "P3", label: "Full Covariance L4 Fingerprinting",  status: "COMPLETE", priority: "—",      detail: "USE_FULL_COVARIANCE flag · EMA NxN cov matrix · Tikhonov regularization λ=0.01 · synthetic separation ratio 9.85 · Phase 41" },
    { id: "P4", label: "ZK Inference Code Binding",          status: "COMPLETE", priority: "—",      detail: "pub[2]=inferenceCode wired · PITLSessionRegistry.sol::submitPITLProof(inferenceCode) · C2 circuit constraint active · Phase 41" },
    { id: "P5", label: "Pro Bot Adversarial Data",           status: "COMPLETE", priority: "—",      detail: "3 white-box attack classes G/H/I (N=5 each, Phase 48) · H: 100% L4 (grip_asym+stick_autocorr) · G/I: 0% batch, detected live (L4+L2B / L2B) · real hardware bot software (aimbot, ML-driven) still untested" },
    { id: "P6", label: "Multi-Party ZK Ceremony",            status: "PLANNED",  priority: "LOW",    detail: "Hermez Perpetual Powers of Tau MPC · current single-contributor ceremony is dev-only" },
    { id: "P7", label: "Bluetooth Calibration (125–250Hz)",  status: "OPEN",     priority: "LOW",    detail: "All N=74 sessions USB-only · L4/L5 thresholds have no empirical grounding for BT polling rates" },
    { id: "P8", label: "Formal Verification (TLA+)",         status: "FUTURE",   priority: "LOW",    detail: "Chain integrity: linkage, monotonicity, non-repudiation · safety-critical esports deployments" },
    { id: "C1", label: "Multi-Button L5 (Phase 39)",         status: "COMPLETE", priority: "—",      detail: "4-button IBI oracle (Cross>L2_dig>R2>Triangle) + pooled fallback · 8 new tests · 888 bridge tests total (Phase 49)" },
    { id: "C2", label: "rhythm_hash 4-Deque Commit (Ph40)",  status: "COMPLETE", priority: "—",      detail: "SHA-256(Cross‖0xFFFF‖L2‖0xFFFF‖R2‖0xFFFF‖Triangle) · same intervals in different buttons produce distinct hashes" },
    { id: "C3", label: "Spectral Entropy Feature (Phase 46)", status: "COMPLETE", priority: "—",     detail: "accel_magnitude_spectral_entropy index 9 · replaces touchpad_active_fraction (zero-variance) · N=74 recalibration → thresholds 6.726/5.097 · bot-vs-human only, does not improve 0.362 separation ratio" },
    { id: "C4", label: "L2C Phantom Weight (Phase 47)",      status: "COMPLETE", priority: "—",      detail: "l2c_inactive flag in pitl_meta · log.debug per dead-zone cycle · §7.5.4 footnote in whitepaper · HUMANITY tile shows '4-signal (L2C: dead zone)' in orange · PITL layer table live INACTIVE indicator" },
    { id: "C5", label: "Tremor FFT Widening (Phase 49)",     status: "COMPLETE", priority: "—",      detail: "ring buffer 513→1025 positions · 512→1024 velocity samples · 1.95→0.977 Hz/bin · 4 bins across 8–12 Hz band · batch validator 7→9 features · bridge 888" },
    { id: "C6", label: "Agentic Intelligence (Phase 50)",    status: "COMPLETE", priority: "—",      detail: "CalibrationIntelligenceAgent peer (6 tools, 30-min event consumer, min() enforcement) · BridgeAgent +3 tools (get_session_narrative, compare_device_fingerprints, get_calibration_agent_status) · agent_events/threshold_history/calibration_agent_sessions tables · /calibration/agent + /calibration/stream endpoints · bridge 902" },
    { id: "C7", label: "Game-Aware Profiling (Phase 51)",    status: "COMPLETE", priority: "—",      detail: "GameProfile registry (ncaa_cfb_26) · L5 R2-first priority override for football · L6-Passive sprint onset EMA baseline · resistance event flagging (ratio 1.5x) · BridgeAgent get_game_profile tool · bridge 915" },
    { id: "C8", label: "Resilience Hardening (Phase 52+53)", status: "COMPLETE", priority: "—",      detail: "WS broadcast NaN/Inf guard (_safe_val) · DS transport restart wrapper (3x) · controller_registered WS event · CONTROLLER OK badge · ANTHROPIC_API_KEY startup check · CalibrationIntelligenceAgent failure escalation · ProactiveMonitor decoupled · CORS +:5174 · gas dead-letter extended · chain gas error discrimination · _pending_pitl_meta reset · 21 new tests · bridge 936" },
    { id: "C9", label: "Runtime Hardening (Phase 54)",       status: "COMPLETE", priority: "—",      detail: "numpy fallback ImportError fix (NCD build_distance_matrix) · _task_done_handler CRITICAL log on 11 managed tasks · send_raw_transaction nonce reset on send failure · WS receive 60s timeout (ws_records + ws_frames) · store migration log.debug · fetchSnapshot abort dedup · WS reconnect exponential backoff 5→60s · 5 new tests · bridge 941" },
    { id: "D0", label: "ioID Device Identity (Phase 55)",   status: "COMPLETE", priority: "—",      detail: "VAPIioIDRegistry.sol · ioid_devices SQLite table · DID did:io:0x<addr> in PITL metadata + WS stream · ensure_ioid_registered() + ioid_increment_session() chain calls · get_ioid_status BridgeAgent tool #22 · 5 new tests · bridge 946" },
    { id: "D1", label: "ZK Tournament Passport (Phase 56)", status: "COMPLETE", priority: "—",      detail: "TournamentPassport.circom (5 public signals) · PITLTournamentPassport.sol (mock mode, SESSION_COUNT=5) · tournament_passports table · generate_tournament_passport BridgeAgent tool #23 · POST /operator/passport endpoint · 5 new tests · bridge 951" },
    { id: "D2", label: "Jitter Variance Feature (Phase 57)", status: "COMPLETE", priority: "—",     detail: "press_timing_jitter_variance index 11 in BiometricFeatureFrame · _BIO_FEATURE_DIM 11→12 · normalised IBI variance: human 0.001–0.05, bot macro <0.00005 · IBI deques (Cross/L2/R2/Triangle, maxlen=50) · behavioral_archaeologist FEATURE_KEYS updated · threshold_calibrator _extract_jitter_variance() · 5 new tests · bridge 956" },
    { id: "D3", label: "Security Hardening (Phase 58)", status: "COMPLETE", priority: "—",          detail: "Operator endpoint auth (x-api-key → 401/503) · sliding-window per-IP rate limiter (60s window, cfg.rate_limit_per_minute) · operator_audit_log table + log_operator_action/get_operator_audit_log · inference_code column in pitl_session_proofs (ZK binding Phase 58A partial) · BridgeAgent tools #24–27: analyze_threshold_impact, predict_evasion_cost, get_anomaly_trend, generate_incident_report · 16 new tests · bridge 972" },
    { id: "E3", label: "PITLSessionRegistry v2 (Phase 58B)", status: "PLANNED", priority: "HIGH",   detail: "Deploy PITLSessionRegistry v2: nullifierInferenceCodes[nullifier]=inferenceCode on-chain · PITLTournamentPassport rejects CHEAT-coded nullifiers · requires testnet IOTX (wallet funded 2026-03-14) · no new ceremony" },
    { id: "E4", label: "ZK Inference Binding (Phase 59)", status: "FUTURE", priority: "MEDIUM",     detail: "Circuit constraint: inferenceCode cryptographically derived from sensor commitment preimage · new MPC ceremony required · closes biometric transplant gap for on-chain tournament gating" },
    { id: "E5", label: "My Controller 3D Twin (Phase 59)", status: "COMPLETE", priority: "—",
      detail: "Physics-driven DualShock Edge digital twin · /ws/twin/{device_id} fusion stream · IBI Biometric Heartbeat · PoAC DNA Helix chain timeline · L4 aura · L6-Passive R2 stiffness · ZK passport + ioID DID panel · Phase 58 audit log queries · BridgeAgent tool #28 · +15 tests · bridge 987" },
  ];
  const colors  = { OPEN: "#ff9500", PLANNED: "#00d4ff", FUTURE: "#5a6a74", COMPLETE: "#00ff88" };
  const pColors = { HIGH: "#ff2d55", MEDIUM: "#ff9500", LOW: "#5a6a74", "—": "#3d5060" };

  return (
    <Panel>
      <SectionLabel>Open Validation Items — §8.6 / §10.x</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {items.map((item) => (
          <div key={item.id} style={{
            display: "grid", gridTemplateColumns: "30px 1fr 80px 60px",
            gap: 10, alignItems: "start",
            padding: "9px 10px",
            background: item.status === "COMPLETE" ? "rgba(0,255,136,0.03)" : "rgba(255,255,255,0.02)",
            border: `1px solid ${item.status === "COMPLETE" ? "rgba(0,255,136,0.1)" : "rgba(255,255,255,0.04)"}`,
            borderRadius: 2,
          }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", paddingTop: 1 }}>{item.id}</span>
            <div>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 12, fontWeight: 600, color: item.status === "COMPLETE" ? "#00ff88" : "#c4cdd6" }}>{item.label}</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginTop: 2, lineHeight: 1.5 }}>{item.detail}</div>
            </div>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: colors[item.status] || "#5a6a74", textAlign: "right", paddingTop: 1 }}>{item.status}</span>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: pColors[item.priority], textAlign: "right", paddingTop: 1 }}>{item.priority}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ─── SECTION: LIVE RECORD FEED (LIVE mode only) ────────────────────────── */

const _INF_COLORS = {
  NOMINAL:            "#00ff88",
  SKILLED:            "#00ff88",
  DRIVER_INJECT:      "#ff2d55",
  WALLHACK_PREAIM:    "#ff2d55",
  AIMBOT_BEHAVIORAL:  "#ff2d55",
  TEMPORAL_ANOMALY:   "#ff9500",
  BIOMETRIC_ANOMALY:  "#ff9500",
};

function LiveRecordFeed({ records }) {
  return (
    <Panel>
      <SectionLabel>Live PoAC Record Feed — WS /ws/records</SectionLabel>
      {records.length === 0 ? (
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", textAlign: "center", padding: "20px 0" }}>
          Waiting for records…
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {records.map((r, i) => {
            const color = _INF_COLORS[r.inference_name] || "#c4cdd6";
            return (
              <div key={i} style={{
                display: "grid", gridTemplateColumns: "68px 1fr 130px 56px",
                gap: 8, alignItems: "center",
                padding: "7px 10px",
                background: "rgba(255,255,255,0.02)",
                border: `1px solid ${color}22`,
                borderRadius: 2,
                opacity: i === 0 ? 1 : Math.max(0.4, 1 - i * 0.07),
                transition: "opacity 0.3s",
              }}>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>
                  {new Date(r._ts).toTimeString().slice(0, 8)}
                </span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color, fontWeight: i === 0 ? 700 : 400 }}>
                  {r.inference_name}
                </span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#5a6a74", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  #{r.record_hash || "—"}
                </span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", textAlign: "right" }}>
                  {r.confidence !== undefined ? `${Math.round(r.confidence * 100)}%` : "—"}
                </span>
              </div>
            );
          })}
        </div>
      )}
      <div style={{ marginTop: 8, fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>
        last 10 records · green=NOMINAL · orange=ADVISORY · red=HARD CHEAT
      </div>
    </Panel>
  );
}

/* ─── BRIDGEAGENT CHAT PANEL ─────────────────────────────────────────────── */

const AGENT_QUICK_QUERIES = [
  "What is the current PHG score?",
  "Show PITL layer status",
  "Are there any active threats?",
  "What is the calibration health?",
  "Show recent PoAC records",
  "Check credential status",
  "Run startup diagnostics",
];

const AGENT_TOOLS = [
  "get_player_profile",           "get_leaderboard",              "get_leaderboard_rank",
  "run_pitl_calibration",         "get_continuity_chain",         "get_recent_records",
  "get_startup_diagnostics",      "get_phg_checkpoints",          "check_eligibility",
  "get_pitl_proof",               "get_behavioral_report",        "get_network_clusters",
  "get_federation_status",        "query_digest",                 "get_detection_policy",
  "get_credential_status",        "get_calibration_status",
  // Phase 50
  "get_session_narrative",        "compare_device_fingerprints",  "get_calibration_agent_status",
  // Phase 51
  "get_game_profile",
];

const CALIB_AGENT_TOOLS = [
  "get_threshold_history",        "get_feature_variance_report",
  "get_zero_variance_features",   "get_separation_analysis",
  "get_pending_recalibration_flags", "trigger_recalibration",
];

const CALIB_QUICK_QUERIES = [
  "Show threshold history",
  "What features have zero variance?",
  "What is the separation analysis?",
  "Check pending recalibration flags",
  "How has the anomaly threshold evolved?",
  "Run personal recalibration",
];

function AgentPanel({ apiKey, onThinkingChange }) {
  const [messages,        setMessages]        = useState([]);
  const [input,           setInput]           = useState("");
  const [thinking,        setThinking]        = useState(false);
  const [toolCatalogOpen, setToolCatalogOpen] = useState(false);
  const abortRef  = useRef(null);
  const feedRef   = useRef(null);

  function _setThinking(val) {
    setThinking(val);
    if (onThinkingChange) onThinkingChange(val);
  }

  async function sendMessage(text) {
    if (!text.trim() || thinking) return;
    if (abortRef.current) abortRef.current.abort();

    const userMsg = { role: "user", text: text.trim() };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    _setThinking(true);

    const assistantIdx = { val: -1 };
    setMessages(prev => {
      assistantIdx.val = prev.length;
      return [...prev, { role: "assistant", text: "", badges: [] }];
    });

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const key  = apiKey || "";
    const url  = `${BRIDGE_URL}/operator/agent/stream?session_id=dashboard&message=${encodeURIComponent(text.trim())}&api_key=${encodeURIComponent(key)}`;

    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) {
        const errText = res.status === 403 ? "Invalid API key" :
                        res.status === 429 ? "Rate limit exceeded (60 req/min)" :
                        res.status === 503 ? "OPERATOR_API_KEY not configured on bridge" :
                        `HTTP ${res.status}`;
        setMessages(prev => {
          const next = [...prev];
          next[assistantIdx.val] = { role: "assistant", text: `Error: ${errText}`, badges: [] };
          return next;
        });
        _setThinking(false);
        return;
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buf     = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim());
            if (evt.type === "text_delta") {
              setMessages(prev => {
                const next = [...prev];
                const msg  = next[assistantIdx.val];
                next[assistantIdx.val] = { ...msg, text: (msg.text || "") + evt.text };
                return next;
              });
            } else if (evt.type === "tool_start") {
              setMessages(prev => {
                const next = [...prev];
                const msg  = next[assistantIdx.val];
                next[assistantIdx.val] = { ...msg, badges: [...(msg.badges || []), { kind: "start", tool: evt.tool }] };
                return next;
              });
            } else if (evt.type === "tool_result") {
              setMessages(prev => {
                const next = [...prev];
                const msg  = next[assistantIdx.val];
                next[assistantIdx.val] = { ...msg, badges: [...(msg.badges || []), { kind: "result", tool: evt.tool, preview: evt.preview }] };
                return next;
              });
            } else if (evt.type === "done" || evt.type === "error") {
              if (evt.type === "error") {
                setMessages(prev => {
                  const next = [...prev];
                  next[assistantIdx.val] = { ...next[assistantIdx.val], text: (next[assistantIdx.val].text || "") + `\n[Error: ${evt.message}]` };
                  return next;
                });
              }
              _setThinking(false);
            }
          } catch {}
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        setMessages(prev => {
          const next = [...prev];
          next[assistantIdx.val] = { role: "assistant", text: `Connection error: ${err.message}`, badges: [] };
          return next;
        });
      }
    } finally {
      _setThinking(false);
    }
  }

  // Auto-scroll feed
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [messages]);

  // Cleanup on unmount
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  const noKey = !apiKey;

  return (
    <Panel style={{ animation: "fadeIn 0.4s ease both" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <SectionLabel>BridgeAgent Interface — /operator/agent/stream</SectionLabel>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 9,
          color: thinking ? "#ff6b00" : "#00ff88",
          animation: thinking ? "statusPulse 1.2s ease-in-out infinite" : "none",
          letterSpacing: "0.1em",
        }}>
          {thinking ? "◌ AGENT THINKING..." : "● AGENT READY"}
        </span>
      </div>

      {/* Tool catalogue toggle */}
      <div style={{ marginBottom: 12 }}>
        <button
          onClick={() => setToolCatalogOpen(o => !o)}
          style={{
            background: "none", border: "1px solid rgba(255,107,0,0.25)",
            color: "#ff6b00", fontFamily: "'JetBrains Mono', monospace",
            fontSize: 8, letterSpacing: "0.1em", padding: "4px 10px",
            borderRadius: 2, cursor: "pointer",
          }}
        >
          {toolCatalogOpen ? "▲" : "▼"} {AGENT_TOOLS.length} TOOLS AVAILABLE
        </button>
        {toolCatalogOpen && (
          <div style={{
            marginTop: 8,
            display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "4px 12px",
            padding: "10px 12px",
            background: "rgba(255,107,0,0.04)",
            border: "1px solid rgba(255,107,0,0.12)",
            borderRadius: 2,
          }}>
            {AGENT_TOOLS.map(t => (
              <span key={t} style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 8, color: "#5a7080", letterSpacing: "0.05em",
              }}>{t}</span>
            ))}
          </div>
        )}
      </div>

      {/* No API key warning */}
      {noKey && (
        <div style={{
          fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
          color: "#ff9500", padding: "8px 12px", marginBottom: 12,
          background: "rgba(255,149,0,0.06)", border: "1px solid rgba(255,149,0,0.2)",
          borderRadius: 2,
        }}>
          Set VITE_BRIDGE_API_KEY in frontend/.env to enable agent queries.
        </div>
      )}

      {/* Message feed */}
      <div
        ref={feedRef}
        style={{
          minHeight: 120, maxHeight: 320, overflowY: "auto",
          display: "flex", flexDirection: "column", gap: 8,
          marginBottom: 12, paddingRight: 4,
        }}
      >
        {messages.length === 0 && (
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", textAlign: "center", padding: "24px 0" }}>
            Send a query to analyse the live VAPI session.
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{
            padding: "8px 12px",
            background: msg.role === "user" ? "rgba(0,212,255,0.05)" : "rgba(255,107,0,0.04)",
            border: `1px solid ${msg.role === "user" ? "rgba(0,212,255,0.12)" : "rgba(255,107,0,0.12)"}`,
            borderRadius: 2,
          }}>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: msg.role === "user" ? "#00d4ff" : "#ff6b00", marginBottom: 4, letterSpacing: "0.1em" }}>
              {msg.role === "user" ? "USER" : "AGENT"}
            </div>
            {msg.text && (
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#c4cdd6", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                {msg.text}
              </div>
            )}
            {msg.badges && msg.badges.length > 0 && (
              <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                {msg.badges.map((b, j) => (
                  <span key={j} style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 8, letterSpacing: "0.05em",
                    padding: "2px 7px",
                    background: b.kind === "result" ? "rgba(0,255,136,0.08)" : "rgba(255,107,0,0.08)",
                    border: `1px solid ${b.kind === "result" ? "rgba(0,255,136,0.25)" : "rgba(255,107,0,0.25)"}`,
                    color: b.kind === "result" ? "#00ff88" : "#ff9500",
                    borderRadius: 2,
                  }}>
                    {b.kind === "result" ? `✓ ${b.tool}${b.preview ? `: ${b.preview}` : ""}` : `⚙ ${b.tool}`}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Quick queries */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 10 }}>
        {AGENT_QUICK_QUERIES.map(q => (
          <button
            key={q}
            onClick={() => sendMessage(q)}
            disabled={thinking || noKey}
            style={{
              background: "none",
              border: "1px solid rgba(255,107,0,0.2)",
              color: (thinking || noKey) ? "#3d5060" : "#c4cdd6",
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 8, letterSpacing: "0.05em",
              padding: "4px 9px", borderRadius: 2,
              cursor: (thinking || noKey) ? "default" : "pointer",
              transition: "border-color 0.15s, color 0.15s",
            }}
          >
            {q}
          </button>
        ))}
      </div>

      {/* Text input + send */}
      <div style={{ display: "flex", gap: 8 }}>
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") sendMessage(input); }}
          disabled={thinking || noKey}
          placeholder="Enter a query for the BridgeAgent…"
          style={{
            flex: 1,
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,107,0,0.2)",
            color: "#c4cdd6",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10, padding: "8px 12px", borderRadius: 2,
            outline: "none",
          }}
        />
        <button
          onClick={() => sendMessage(input)}
          disabled={thinking || noKey || !input.trim()}
          style={{
            background: (thinking || noKey || !input.trim()) ? "rgba(255,107,0,0.06)" : "rgba(255,107,0,0.15)",
            border: "1px solid rgba(255,107,0,0.3)",
            color: (thinking || noKey || !input.trim()) ? "#3d5060" : "#ff6b00",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 9, letterSpacing: "0.15em",
            padding: "8px 18px", borderRadius: 2,
            cursor: (thinking || noKey || !input.trim()) ? "default" : "pointer",
            transition: "background 0.15s, color 0.15s",
          }}
        >
          SEND
        </button>
      </div>
    </Panel>
  );
}

/* ─── CALIBRATION INTELLIGENCE AGENT PANEL (Phase 50) ───────────────────── */

function CalibAgentPanel({ apiKey, phase50Stats }) {
  const [messages,  setMessages]  = useState([]);
  const [input,     setInput]     = useState("");
  const [thinking,  setThinking]  = useState(false);
  const abortRef = useRef(null);
  const feedRef  = useRef(null);

  const pendingFlags = phase50Stats?.calib_agent_events_pending ?? 0;
  const lastUpdate   = phase50Stats?.last_threshold_update_ts   ?? null;
  const histCount    = phase50Stats?.threshold_history_count    ?? 0;

  async function sendMessage(text) {
    if (!text.trim() || thinking) return;
    if (abortRef.current) abortRef.current.abort();

    setMessages(prev => [...prev, { role: "user", text: text.trim() }]);
    setInput("");
    setThinking(true);

    const assistantIdx = { val: -1 };
    setMessages(prev => {
      assistantIdx.val = prev.length;
      return [...prev, { role: "assistant", text: "", badges: [] }];
    });

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const key = apiKey || "";
    const url = `${BRIDGE_URL}/operator/calibration/stream?session_id=calib_dashboard&message=${encodeURIComponent(text.trim())}&api_key=${encodeURIComponent(key)}`;

    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) {
        const errText = res.status === 403 ? "Invalid API key" :
                        res.status === 429 ? "Rate limit exceeded" :
                        res.status === 503 ? "OPERATOR_API_KEY not configured on bridge" :
                        `HTTP ${res.status}`;
        setMessages(prev => {
          const next = [...prev];
          next[assistantIdx.val] = { role: "assistant", text: `Error: ${errText}`, badges: [] };
          return next;
        });
        setThinking(false);
        return;
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buf     = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim());
            if (evt.type === "text_delta") {
              setMessages(prev => {
                const next = [...prev];
                const msg  = next[assistantIdx.val];
                next[assistantIdx.val] = { ...msg, text: (msg.text || "") + evt.text };
                return next;
              });
            } else if (evt.type === "tool_start") {
              setMessages(prev => {
                const next = [...prev];
                const msg  = next[assistantIdx.val];
                next[assistantIdx.val] = { ...msg, badges: [...(msg.badges || []), { kind: "start", tool: evt.tool }] };
                return next;
              });
            } else if (evt.type === "tool_result") {
              setMessages(prev => {
                const next = [...prev];
                const msg  = next[assistantIdx.val];
                next[assistantIdx.val] = { ...msg, badges: [...(msg.badges || []), { kind: "result", tool: evt.tool, preview: evt.preview }] };
                return next;
              });
            } else if (evt.type === "done" || evt.type === "error") {
              if (evt.type === "error") {
                setMessages(prev => {
                  const next = [...prev];
                  next[assistantIdx.val] = { ...next[assistantIdx.val], text: (next[assistantIdx.val].text || "") + `\n[Error: ${evt.message}]` };
                  return next;
                });
              }
              setThinking(false);
            }
          } catch {}
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        setMessages(prev => {
          const next = [...prev];
          next[assistantIdx.val] = { role: "assistant", text: `Connection error: ${err.message}`, badges: [] };
          return next;
        });
      }
    } finally {
      setThinking(false);
    }
  }

  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [messages]);
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  const noKey = !apiKey;

  return (
    <Panel style={{ animation: "fadeIn 0.4s ease both", borderColor: "rgba(0,212,255,0.18)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <SectionLabel>CalibrationIntelligenceAgent — /operator/calibration/stream · Phase 50</SectionLabel>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {pendingFlags > 0 && (
            <span style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
              color: "#ff9500", padding: "3px 8px",
              background: "rgba(255,149,0,0.1)", border: "1px solid rgba(255,149,0,0.3)",
              borderRadius: 2,
            }}>
              {pendingFlags} PENDING FLAG{pendingFlags !== 1 ? "S" : ""}
            </span>
          )}
          {histCount > 0 && (
            <span style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
              color: "#00d4ff", padding: "3px 8px",
              background: "rgba(0,212,255,0.06)", border: "1px solid rgba(0,212,255,0.2)",
              borderRadius: 2,
            }}>
              {histCount} CYCLES LOGGED
            </span>
          )}
          <span style={{
            fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
            color: thinking ? "#00d4ff" : "#00d4ff",
            animation: thinking ? "statusPulse 1.2s ease-in-out infinite" : "none",
            letterSpacing: "0.1em",
          }}>
            {thinking ? "◌ CALIBRATING..." : "● CALIB READY"}
          </span>
        </div>
      </div>

      {lastUpdate && (
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginBottom: 10 }}>
          last threshold update: {lastUpdate}
        </div>
      )}

      <div style={{ marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 4 }}>
        {CALIB_AGENT_TOOLS.map(t => (
          <span key={t} style={{
            fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d6070",
            padding: "2px 7px", background: "rgba(0,212,255,0.04)",
            border: "1px solid rgba(0,212,255,0.12)", borderRadius: 2,
          }}>{t}</span>
        ))}
      </div>

      {noKey && (
        <div style={{
          fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#ff9500",
          padding: "8px 12px", marginBottom: 12,
          background: "rgba(255,149,0,0.06)", border: "1px solid rgba(255,149,0,0.2)",
          borderRadius: 2,
        }}>
          Set VITE_BRIDGE_API_KEY in frontend/.env to enable calibration agent queries.
        </div>
      )}

      <div ref={feedRef} style={{
        minHeight: 80, maxHeight: 240, overflowY: "auto",
        display: "flex", flexDirection: "column", gap: 8,
        marginBottom: 12, paddingRight: 4,
      }}>
        {messages.length === 0 && (
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", textAlign: "center", padding: "20px 0" }}>
            Query the calibration intelligence agent. · 6 tools · 30-min event consumer · min() enforcement
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{
            padding: "8px 12px",
            background: msg.role === "user" ? "rgba(0,212,255,0.05)" : "rgba(0,212,255,0.03)",
            border: `1px solid ${msg.role === "user" ? "rgba(0,212,255,0.12)" : "rgba(0,212,255,0.08)"}`,
            borderRadius: 2,
          }}>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#00d4ff", marginBottom: 4, letterSpacing: "0.1em" }}>
              {msg.role === "user" ? "USER" : "CALIB-AGENT"}
            </div>
            {msg.text && (
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#c4cdd6", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                {msg.text}
              </div>
            )}
            {msg.badges && msg.badges.length > 0 && (
              <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                {msg.badges.map((b, j) => (
                  <span key={j} style={{
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 8, letterSpacing: "0.05em",
                    padding: "2px 7px",
                    background: b.kind === "result" ? "rgba(0,212,255,0.08)" : "rgba(0,212,255,0.05)",
                    border: `1px solid ${b.kind === "result" ? "rgba(0,212,255,0.3)" : "rgba(0,212,255,0.15)"}`,
                    color: "#00d4ff", borderRadius: 2,
                  }}>
                    {b.kind === "result" ? `✓ ${b.tool}${b.preview ? `: ${b.preview}` : ""}` : `⚙ ${b.tool}`}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 10 }}>
        {CALIB_QUICK_QUERIES.map(q => (
          <button key={q} onClick={() => sendMessage(q)} disabled={thinking || noKey} style={{
            background: "none", border: "1px solid rgba(0,212,255,0.2)",
            color: (thinking || noKey) ? "#3d5060" : "#c4cdd6",
            fontFamily: "'JetBrains Mono', monospace", fontSize: 8, letterSpacing: "0.05em",
            padding: "4px 9px", borderRadius: 2,
            cursor: (thinking || noKey) ? "default" : "pointer",
          }}>{q}</button>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <input
          type="text" value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") sendMessage(input); }}
          disabled={thinking || noKey}
          placeholder="Query the CalibrationIntelligenceAgent…"
          style={{
            flex: 1, background: "rgba(0,212,255,0.03)", border: "1px solid rgba(0,212,255,0.2)",
            color: "#c4cdd6", fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10, padding: "8px 12px", borderRadius: 2, outline: "none",
          }}
        />
        <button onClick={() => sendMessage(input)} disabled={thinking || noKey || !input.trim()} style={{
          background: (thinking || noKey || !input.trim()) ? "rgba(0,212,255,0.03)" : "rgba(0,212,255,0.12)",
          border: "1px solid rgba(0,212,255,0.3)",
          color: (thinking || noKey || !input.trim()) ? "#3d5060" : "#00d4ff",
          fontFamily: "'JetBrains Mono', monospace", fontSize: 9, letterSpacing: "0.15em",
          padding: "8px 18px", borderRadius: 2,
          cursor: (thinking || noKey || !input.trim()) ? "default" : "pointer",
        }}>
          SEND
        </button>
      </div>
    </Panel>
  );
}

/* ─── SECTION: CAPTURE MONITOR (Phase 44) ───────────────────────────────── */

function StickPad({ x = 128, y = 128, label }) {
  const px = ((x - 128) / 128) * 45;
  const py = ((y - 128) / 128) * 45;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <div style={{
        width: 64, height: 64, border: "1px solid rgba(0,212,255,0.2)",
        borderRadius: 2, background: "rgba(0,212,255,0.03)", position: "relative", overflow: "hidden",
      }}>
        <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "rgba(0,212,255,0.08)" }} />
        <div style={{ position: "absolute", top: "50%", left: 0, right: 0, height: 1, background: "rgba(0,212,255,0.08)" }} />
        <div style={{
          position: "absolute", width: 8, height: 8, borderRadius: "50%",
          background: "#00d4ff", boxShadow: "0 0 6px #00d4ff",
          left: `calc(50% + ${px}% - 4px)`, top: `calc(50% + ${py}% - 4px)`,
          transition: "left 0.06s, top 0.06s",
        }} />
      </div>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>{label}</span>
    </div>
  );
}

function TriggerBar({ value = 0, label, color = "#ff9500" }) {
  const pct = Math.round((value / 255) * 100);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <div style={{
        width: 20, height: 64, border: "1px solid rgba(255,107,0,0.2)",
        borderRadius: 2, background: "rgba(255,107,0,0.04)", position: "relative", overflow: "hidden",
      }}>
        <div style={{
          position: "absolute", bottom: 0, left: 0, right: 0,
          height: `${pct}%`, background: color, opacity: 0.8,
          transition: "height 0.06s",
        }} />
      </div>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>{label}</span>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color, fontWeight: 700 }}>{pct}%</span>
    </div>
  );
}

function CaptureMonitor({ latestRecord, accelHistory, latestFrame }) {
  const [l6Summary, setL6Summary] = useState(null);
  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const res = await fetch(`${BRIDGE_URL}/l6/captures/summary`,
          { signal: AbortSignal.timeout(3000) });
        if (res.ok && active) setL6Summary(await res.json());
      } catch {}
    }
    poll();
    const t = setInterval(poll, 10000);
    return () => { active = false; clearInterval(t); };
  }, []);

  const frame = latestFrame ?? {};
  const rec   = latestRecord ?? {};
  const l6Total = l6Summary
    ? Object.values(l6Summary.by_profile ?? {}).reduce((s, v) => s + v, 0)
    : null;
  const l6Pct = l6Total !== null ? Math.min(100, Math.round((l6Total / 50) * 100)) : 0;

  const humanityPct   = rec.humanity_prob != null ? Math.round(rec.humanity_prob * 100) : null;
  const humanityColor = humanityPct == null ? "#5a6a74"
    : humanityPct >= 70 ? "#00ff88" : humanityPct >= 40 ? "#ff9500" : "#ff2d55";
  const l4Color = rec.pitl_l4_distance == null ? "#5a6a74"
    : rec.pitl_l4_distance > 6.726 ? "#ff2d55"
    : rec.pitl_l4_distance > 5.097 ? "#ff9500" : "#00ff88";
  const l5Color = rec.pitl_l5_cv == null ? "#5a6a74"
    : rec.pitl_l5_cv < 0.08 ? "#ff2d55" : "#00ff88";

  // l2c_inactive: true when right stick is in dead zone (e.g. NCAA Football 26, 68/69 sessions).
  // In that state p_L2C=0.5 neutral → formula is effectively 4-signal, not 5.
  const l2cInactive = rec.l2c_inactive === true;
  const humanitySub = l2cInactive ? "4-signal (L2C: dead zone)" : "5-signal";
  const humanitySubColor = l2cInactive ? "#ff9500" : "#2a3840";

  const scores = [
    { label: "L4 DIST",  value: rec.pitl_l4_distance != null ? rec.pitl_l4_distance.toFixed(2) : "—",          color: l4Color,       sub: "thresh 6.73",  subColor: "#2a3840" },
    { label: "L5 CV",    value: rec.pitl_l5_cv != null ? rec.pitl_l5_cv.toFixed(3) : "—",                      color: l5Color,       sub: "thresh 0.08",  subColor: "#2a3840" },
    { label: "HUMANITY", value: humanityPct != null ? `${humanityPct}%` : "—",                                  color: humanityColor, sub: humanitySub,    subColor: humanitySubColor },
    { label: "L2B FRAC", value: rec.l2b_coupled_fraction != null ? rec.l2b_coupled_fraction.toFixed(2) : "—",   color: "#ff9500",     sub: "thresh 0.55",  subColor: "#2a3840" },
    { label: "L5 SRC",   value: rec.l5_source ?? "—",                                                           color: "#00d4ff",     sub: "rhythm btn",   subColor: "#2a3840" },
  ];

  return (
    <Panel>
      <SectionLabel>Capture Monitor — Live Controller Feed · /ws/frames 20Hz · Phase 46</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 16 }}>

        {/* Left: accel waveform + PITL scores */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginBottom: 4 }}>
              ACCEL MAGNITUDE (g) · 3s ROLLING WINDOW
            </div>
            <div style={{ height: 80 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={accelHistory} margin={{ top: 2, right: 2, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="accelGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#00d4ff" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#00d4ff" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <Area type="monotone" dataKey="v" stroke="#00d4ff" strokeWidth={1.5}
                        fill="url(#accelGrad)" dot={false} isAnimationActive={false} />
                  <YAxis domain={[0, "auto"]} hide />
                  <XAxis dataKey="t" hide />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 6 }}>
            {scores.map(({ label, value, color, sub, subColor }) => (
              <div key={label} style={{
                padding: "8px 10px", background: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.05)", borderRadius: 2,
              }}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 14, fontWeight: 700, color }}>{value}</div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginTop: 3 }}>{label}</div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 7, color: subColor ?? "#2a3840", marginTop: 1 }}>{sub}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Right: sticks + triggers + L6 progress */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, alignItems: "center" }}>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
            <StickPad x={frame.left_stick_x}  y={frame.left_stick_y}  label="L-STICK" />
            <TriggerBar value={frame.l2_trigger} label="L2" color="#ff6b00" />
            <TriggerBar value={frame.r2_trigger} label="R2" color="#ff9500" />
            <StickPad x={frame.right_stick_x} y={frame.right_stick_y} label="R-STICK" />
          </div>
          <div style={{
            width: "100%", padding: "10px 12px",
            background: "rgba(255,107,0,0.04)", border: "1px solid rgba(255,107,0,0.15)", borderRadius: 2,
          }}>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginBottom: 6 }}>
              L6 CAPTURE · TARGET N≥50
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ flex: 1, height: 6, background: "rgba(255,255,255,0.05)", borderRadius: 1, overflow: "hidden" }}>
                <div style={{
                  height: "100%", width: `${l6Pct}%`,
                  background: l6Pct >= 100 ? "#00ff88" : "#ff9500",
                  transition: "width 0.4s",
                }} />
              </div>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 700,
                             color: l6Pct >= 100 ? "#00ff88" : "#ff9500", minWidth: 36 }}>
                {l6Total !== null ? `${l6Total}/50` : "—"}
              </span>
            </div>
          </div>
        </div>

      </div>
    </Panel>
  );
}

/* ─── MAIN APP ────────────────────────────────────────────────────────────── */

export default function VAPIDashboard() {
  const pulse                    = usePulse(3000);
  const { snapshot, mode, records } = useBridgeData();
  const { accelHistory, latestFrame } = useFrameData(mode === "LIVE");
  const latestDeviceId = records[0]?.device_id || '';
  const [agentThinking, setAgentThinking] = useState(false);

  const sessionTarget  = snapshot?.session?.total_sessions  ?? 74;
  const testTarget     = snapshot?.session?.total_tests     ?? 972;
  const contractTarget = snapshot?.session?.contracts_live  ?? 15;
  const phase50Stats   = snapshot?.phase50 ?? null;

  const sessionCount  = useCounter(sessionTarget,  1500);
  const testCount     = useCounter(testTarget,     1800);
  const contractCount = useCounter(contractTarget, 1200);

  const l6Status      = snapshot ? (snapshot.l6?.enabled ? "ACTIVE" : "DISABLED") : undefined;
  // L2C dead-zone: derive from most recent WS record (true in NCAA CFB 26, 68/69 sessions).
  const l2cInactive   = records.length > 0 ? records[0]?.l2c_inactive === true : undefined;
  const calibData = snapshot?.calibration?.threshold_history ?? [];
  const phgScore  = snapshot?.phg?.score;

  return (
    <>
      {/* Google Fonts */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #030507; }

        @keyframes statusPulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 8px #00ff88; }
          50% { opacity: 0.4; box-shadow: 0 0 2px #00ff88; }
        }
        @keyframes spinSlow {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes headerGlow {
          0%, 100% { text-shadow: 0 0 20px rgba(255,107,0,0.4), 0 0 60px rgba(255,107,0,0.1); }
          50% { text-shadow: 0 0 30px rgba(255,107,0,0.6), 0 0 80px rgba(255,107,0,0.2); }
        }
        .panel-fade { animation: fadeIn 0.4s ease both; }
      `}</style>

      <ScanLines />
      <GridNoise />

      <div style={{
        minHeight: "100vh",
        background: "radial-gradient(ellipse 80% 60% at 50% -10%, rgba(255,107,0,0.06) 0%, transparent 60%), #030507",
        color: "#c4cdd6",
        fontFamily: "'Rajdhani', sans-serif",
        position: "relative",
        zIndex: 1,
      }}>

        {/* ── HEADER ─────────────────────────────────────────────────────── */}
        <div style={{
          borderBottom: "1px solid rgba(255,107,0,0.2)",
          padding: "20px 32px 16px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          background: "rgba(3,5,7,0.9)",
          position: "sticky", top: 0, zIndex: 100,
          backdropFilter: "blur(12px)",
        }}>
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
              <h1 style={{
                fontFamily: "'Rajdhani', sans-serif",
                fontSize: 28, fontWeight: 700, letterSpacing: "0.15em",
                color: "#ff6b00",
                animation: "headerGlow 4s ease-in-out infinite",
              }}>
                VAPI
              </h1>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: "#3d5060", letterSpacing: "0.1em" }}>
                VERIFIED AUTONOMOUS PHYSICAL INTELLIGENCE
              </span>
            </div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#3d5060", marginTop: 3, letterSpacing: "0.1em" }}>
              Cryptographic Anti-Cheat Protocol · DualShock Edge CFI-ZCP1 · IoTeX L1 · Whitepaper v3 (Phase 58)
            </div>
          </div>
          <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
            {[
              ["SESSIONS",  sessionCount,  "#ff6b00"],
              ["TESTS",     testCount,     "#00d4ff"],
              ["CONTRACTS", contractCount, "#00ff88"],
            ].map(([label, val, color]) => (
              <div key={label} style={{ textAlign: "center" }}>
                <div style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 22, fontWeight: 700, color, lineHeight: 1 }}>{val.toLocaleString()}</div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", letterSpacing: "0.15em", marginTop: 2 }}>{label}</div>
              </div>
            ))}
            {mode === "LIVE" ? (
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "6px 12px",
                border: "1px solid rgba(0,255,136,0.3)",
                borderRadius: 2,
                background: "rgba(0,255,136,0.06)",
              }}>
                <StatusDot status="ACTIVE" />
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#00ff88" }}>LIVE — BRIDGE CONNECTED</span>
              </div>
            ) : (
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "6px 12px",
                border: "1px solid rgba(255,149,0,0.3)",
                borderRadius: 2,
                background: "rgba(255,149,0,0.06)",
              }}>
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#ff9500", flexShrink: 0 }} />
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#ff9500" }}>DEMO — BRIDGE OFFLINE</span>
              </div>
            )}
            {mode === "LIVE" && (
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "6px 12px",
                border: snapshot?.hardware?.controller_connected
                  ? "1px solid rgba(0,212,255,0.3)"
                  : "1px solid rgba(255,59,48,0.3)",
                borderRadius: 2,
                background: snapshot?.hardware?.controller_connected
                  ? "rgba(0,212,255,0.05)"
                  : "rgba(255,59,48,0.05)",
              }}>
                <span style={{
                  width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                  background: snapshot?.hardware?.controller_connected ? "#00d4ff" : "#ff3b30",
                }} />
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
                  color: snapshot?.hardware?.controller_connected ? "#00d4ff" : "#ff3b30" }}>
                  {snapshot?.hardware?.controller_connected ? "CONTROLLER OK" : "NO CONTROLLER"}
                </span>
              </div>
            )}
            {mode === "LIVE" && (
              <div style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "6px 12px",
                border: "1px solid rgba(255,107,0,0.2)",
                borderRadius: 2,
                background: "rgba(255,107,0,0.04)",
              }}>
                <span style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 9,
                  color: agentThinking ? "#ff6b00" : "#4caf50",
                  animation: agentThinking ? "statusPulse 1.2s ease-in-out infinite" : "none",
                  letterSpacing: "0.08em",
                }}>
                  {agentThinking ? "◌ AGENT THINKING..." : "● AGENT READY"}
                </span>
              </div>
            )}
            {mode === "LIVE" && snapshot?.game_profile?.active && (
              <div style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "6px 12px",
                border: "1px solid rgba(0,212,255,0.3)",
                borderRadius: 2,
                background: "rgba(0,212,255,0.05)",
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: "50%",
                  background: "#00d4ff", flexShrink: 0,
                }} />
                <span style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 9, color: "#00d4ff", letterSpacing: "0.08em",
                  textTransform: "uppercase",
                }}>
                  {snapshot.game_profile.display_name}
                </span>
                <span style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 8, color: "#3d5060", letterSpacing: "0.06em",
                }}>
                  L5: {(snapshot.game_profile.l5_priority ?? []).slice(0, 2).join(">").toUpperCase()}
                </span>
              </div>
            )}
            {mode === "LIVE" && snapshot?.hardware?.controller_connected && latestDeviceId && (
              <a href={`/controller-twin.html?device=${latestDeviceId}`}
                 target="_blank" rel="noopener noreferrer"
                 style={{ display: "inline-flex", alignItems: "center", gap: 4,
                          padding: "3px 10px", border: `1px solid rgba(255,107,0,0.4)`,
                          borderRadius: 2, background: `rgba(255,107,0,0.07)`, cursor: "pointer",
                          fontFamily: "'JetBrains Mono', monospace", fontSize: 8,
                          color: "#ff6b00", textDecoration: "none", letterSpacing: "0.1em" }}>
                MY CONTROLLER ↗
              </a>
            )}
          </div>
        </div>

        {/* ── STAT ROW ────────────────────────────────────────────────────── */}
        <div style={{ padding: "16px 32px 0", display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 8 }}>
          <StatBox label="Injection Det. Margin" value="14,000×"  accent="#ff6b00" mono />
          <StatBox label="False Positive Rate"   value="2.9%"     accent="#00ff88" mono />
          <StatBox label="Calibration Sessions"  value="N=74"     accent="#00d4ff" mono />
          <StatBox label="Distinct Players"      value="3"        accent="#ff9500" mono />
          <StatBox label="Separation Ratio"      value="0.362"    accent="#ff9500" mono sub="⚠ inter-player" />
          <StatBox label="L5 Cross Coverage"     value="83.8%"    accent="#00ff88" mono sub="62/74 sessions" />
        </div>

        {/* ── MAIN GRID ───────────────────────────────────────────────────── */}
        <div style={{ padding: "16px 32px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {/* Left column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="panel-fade" style={{ animationDelay: "0.05s" }}><PITLStack l6Status={l6Status} l2cInactive={l2cInactive} /></div>
            <div className="panel-fade" style={{ animationDelay: "0.15s" }}><AdversarialMatrix /></div>
            <div className="panel-fade" style={{ animationDelay: "0.25s" }}><HardwareMetrics /></div>
            {mode === "LIVE" && (
              <div className="panel-fade" style={{ animationDelay: "0.32s" }}><LiveRecordFeed records={records} /></div>
            )}
          </div>
          {/* Right column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="panel-fade" style={{ animationDelay: "0.1s" }}><PoACRecord /></div>
            <div className="panel-fade" style={{ animationDelay: "0.2s" }}><BiometricRadar /></div>
            <div className="panel-fade" style={{ animationDelay: "0.3s" }}><LivingCalibration chartData={calibData} /></div>
          </div>
        </div>

        {/* ── L5 COVERAGE ROW (Phase 39/40) ───────────────────────────────── */}
        <div style={{ padding: "0 32px 16px" }}>
          <div className="panel-fade" style={{ animationDelay: "0.33s" }}><L5ButtonCoverage /></div>
        </div>

        {/* ── CAPTURE MONITOR (Phase 44, LIVE only) ───────────────────────── */}
        {mode === "LIVE" && (
          <div style={{ padding: "0 32px 16px" }}>
            <div className="panel-fade" style={{ animationDelay: "0.36s" }}>
              <CaptureMonitor
                latestRecord={records[0] ?? null}
                accelHistory={accelHistory}
                latestFrame={latestFrame}
              />
            </div>
          </div>
        )}

        {/* ── FULL-WIDTH ROW ───────────────────────────────────────────────── */}
        <div style={{ padding: "0 32px", display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          <div className="panel-fade" style={{ animationDelay: "0.35s" }}><PHGCredential phgScore={phgScore} /></div>
          <div className="panel-fade" style={{ animationDelay: "0.4s"  }}><ZKProofStatus /></div>
          <div className="panel-fade" style={{ animationDelay: "0.45s" }}><ContractStack /></div>
        </div>

        {/* ── OPEN ITEMS ───────────────────────────────────────────────────── */}
        <div style={{ padding: "16px 32px 0" }}>
          <div className="panel-fade" style={{ animationDelay: "0.5s" }}><OpenItems /></div>
        </div>

        {/* ── BRIDGEAGENT CHAT PANEL (LIVE mode only) ───────────────────── */}
        {mode === "LIVE" && (
          <div style={{ padding: "16px 32px 0" }}>
            <div className="panel-fade" style={{ animationDelay: "0.55s" }}>
              <AgentPanel
                apiKey={import.meta.env.VITE_BRIDGE_API_KEY}
                onThinkingChange={setAgentThinking}
              />
            </div>
          </div>
        )}

        {/* ── CALIBRATION INTELLIGENCE AGENT PANEL (Phase 50, LIVE only) ── */}
        {mode === "LIVE" && (
          <div style={{ padding: "16px 32px 32px" }}>
            <div className="panel-fade" style={{ animationDelay: "0.60s" }}>
              <CalibAgentPanel
                apiKey={import.meta.env.VITE_BRIDGE_API_KEY}
                phase50Stats={phase50Stats}
              />
            </div>
          </div>
        )}

        {/* ── FOOTER ──────────────────────────────────────────────────────── */}
        <div style={{
          borderTop: "1px solid rgba(255,107,0,0.1)",
          padding: "12px 32px",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>
            VAPI Protocol Dashboard · Whitepaper v3 · Phase 58 · IoTeX Testnet
          </span>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>
            228B PoAC wire format FROZEN · 9-layer PITL · Groth16 BN254 · ~1,397 tests
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{
              display: "inline-block", width: 5, height: 5, borderRadius: "50%",
              background: pulse ? "#ff6b00" : "transparent",
              border: "1px solid rgba(255,107,0,0.4)",
              transition: "background 0.1s",
            }} />
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>MODE 6 CALIBRATION ACTIVE</span>
          </div>
        </div>

      </div>
    </>
  );
}
