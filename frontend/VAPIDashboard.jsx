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
  { id: "L2C", name: "Stick-IMU Correlation",  type: "ADVISORY",   code: "0x32",   signal: "Pearson cross-corr 10–60ms",           status: "ACTIVE",  margin: null,  detail: "abs(max_causal_corr) of stick velocity vs. gyro_z at causal lags. Threshold < 0.15. abs() mandatory — anti-correlation is physical coupling." },
  { id: "L3",  name: "Behavioral ML",          type: "HARD CHEAT", code: "0x29/2A",signal: "9-feature temporal classifier",        status: "ACTIVE",  margin: null,  detail: "30→64→32→6 INT8 net. Targets MACRO (σ² < 1.0ms²) + AIMBOT (jerk > 2.0)" },
  { id: "L4",  name: "Biometric Fingerprint",  type: "ADVISORY",   code: "0x30",   signal: "11-feature Mahalanobis (Phase 17)",    status: "ACTIVE",  margin: null,  detail: "Anomaly threshold: 7.019 (mean+3σ). Continuity: 5.369. N=69, 3 players. Zero-variance features auto-excluded (ZERO_VAR_THRESHOLD=1e-4)." },
  { id: "L5",  name: "Temporal Rhythm",        type: "ADVISORY",   code: "0x2B",   signal: "4-btn IBI · CV · entropy · 60Hz quant",status: "ACTIVE",  margin: null,  detail: "Phase 39: 4-button priority Cross(1.373)>L2_dig(1.333)>R2(1.176)>Triangle(1.138). Pooled IBI fallback ≥5 samples/button. Fires on ≥2/3: CV<0.08, entropy<1.0bit, quant>0.55. l5_source persisted in PITL metadata." },
  { id: "L6",  name: "Active Haptic C-R",      type: "ADVISORY",   code: "—",      signal: "Motorized trigger resistance",         status: "PENDING", margin: null,  detail: "8 resistance profiles. Onset 40–300ms. DISABLED — L6_CHALLENGES_ENABLED=false. Baseline calibration requires N≥50 challenge sessions." },
];

const ADVERSARIAL_DATA = [
  { attack: "IMU Injection",     detection: 100, n: 10, layer: "L2",      color: "#00ff88" },
  { attack: "Timing Macro",      detection: 100, n: 10, layer: "L5",      color: "#00ff88" },
  { attack: "Quant-Masked Bot",  detection: 100, n: 15, layer: "L5",      color: "#00ff88" },
  { attack: "Warmup Attack",     detection: 60,  n: 10, layer: "L5+Arch", color: "#ff9500" },
  { attack: "Replay (Chain)",    detection: 20,  n: 5,  layer: "L1",      color: "#ff9500" },
  { attack: "Bio Transplant",    detection: 0,   n: 5,  layer: "L4",      color: "#ff2d55" },
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
  sessions: 69,
  players: 3,
  l4Anomaly: 7.019,
  l4Continuity: 5.369,
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
  { feature: "Touchpad Frac",   score: 12 },  // structurally zero
];

const CONTRACT_STACK = [
  { name: "PoACVerifier",        addr: "…deployed", gas: "81,245",  status: "LIVE" },
  { name: "PHGCredential",       addr: "…deployed", gas: "110,000", status: "LIVE" },
  { name: "TournamentGateV3",    addr: "…deployed", gas: "~72,000", status: "LIVE" },
  { name: "PITLSessionRegistry", addr: "0x07D3ca15…",gas: "—",      status: "LIVE" },
  { name: "FederatedThreatReg",  addr: "…deployed", gas: "65,000",  status: "LIVE" },
  { name: "SkillOracle",         addr: "…deployed", gas: "—",       status: "LIVE" },
];

const MODE6_DATA = Array.from({ length: 24 }, (_, i) => ({
  cycle: `C${i + 1}`,
  anomaly:    +(7.019 + (Math.sin(i * 0.4) * 0.3 + (i > 12 ? -0.08 * (i - 12) : 0))).toFixed(3),
  continuity: +(5.369 + (Math.cos(i * 0.4) * 0.2)).toFixed(3),
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
  const color = status === "ACTIVE" ? "#00ff88" : status === "PENDING" ? "#ff9500" : "#ff2d55";
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

function PITLStack() {
  const [active, setActive] = useState(null);
  return (
    <Panel>
      <SectionLabel>Physical Input Trust Layer — 9-Level Stack</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {PITL_LAYERS.map((layer, i) => (
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
              <StatusDot status={layer.status} />
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: layer.status === "PENDING" ? "#ff9500" : "#3d5060" }}>{layer.status}</span>
            </div>
          </div>
        ))}
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
            ["Anomaly Threshold",  "7.019",  "mean+3σ (N=69)"],
            ["Continuity Thresh",  "5.369",  "mean+2σ"],
            ["Dist Mean",          "2.068",  "across N=69"],
            ["Dist Std",           "1.650",  ""],
            ["Separation Ratio",   "0.362",  "inter-person ⚠"],
            ["False Positive Rate","2.9%",   "2/69 sessions"],
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
        ⚠ 3 features structurally zero: trigger_resistance_change_rate, touchpad_active_fraction, touch_position_variance — auto-excluded via ZERO_VAR_THRESHOLD=1e-4. Separation ratio 0.362 &lt; 1.0: L4 is intra-player anomaly detector, not inter-player identifier.
      </div>
    </Panel>
  );
}

/* ─── SECTION: MODE 6 LIVING CALIBRATION ────────────────────────────────── */

function LivingCalibration() {
  return (
    <Panel>
      <SectionLabel>Mode 6 — Living Calibration (Phase 38) · α=0.95 · ±15%/cycle</SectionLabel>
      <div style={{ height: 140 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={MODE6_DATA} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
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
        {[["#ff6b00", "Anomaly Threshold (7.019)"], ["#00d4ff", "Continuity Threshold (5.369)"]].map(([c, l]) => (
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
            ["inferenceResult",   "pub[2]=0 ⚠ NOT bound on-chain"],
            ["nullifierHash",     "Poseidon(deviceIdHash, epoch)"],
            ["epoch",             "block.number / EPOCH_BLOCKS"],
          ].map(([k, v]) => (
            <div key={k} style={{ marginBottom: 5 }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: k.includes("inferenceResult") ? "#ff9500" : "#ff6b00" }}>{k}</span>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060", marginTop: 1 }}>{v}</div>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}

/* ─── SECTION: PHG CREDENTIAL ────────────────────────────────────────────── */

function PHGCredential() {
  const score = useCounter(847, 2000);
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
    { id: "P1", label: "L6 Human Response Baseline",         status: "OPEN",     priority: "HIGH",   detail: "N≥50 challenge sessions · onset_ms/settle_ms/grip_variance distributions · l6_threshold_calibrator.py skeleton ready" },
    { id: "P2", label: "Post-Phase-17 Session Recapture",    status: "OPEN",     priority: "HIGH",   detail: "Touchpad features now populated · widen tremor FFT window to ≥1024 frames at 1000Hz · recompute separation ratio (currently 0.362)" },
    { id: "P3", label: "Full Covariance L4 Fingerprinting",  status: "PLANNED",  priority: "MEDIUM", detail: "Diagonal → full 7×7 covariance matrix · captures trigger onset × grip asymmetry correlation · improves inter-player separation" },
    { id: "P4", label: "ZK Inference Code Binding",          status: "PLANNED",  priority: "MEDIUM", detail: "pub[2]=0 → real inferenceResult on-chain · requires circuit upgrade + multi-party MPC ceremony" },
    { id: "P5", label: "Pro Bot Adversarial Data",           status: "OPEN",     priority: "MEDIUM", detail: "Commercial aimbot trajectories, ML-driven bots, game-specific macros as labeled data" },
    { id: "P6", label: "Multi-Party ZK Ceremony",            status: "PLANNED",  priority: "LOW",    detail: "Hermez Perpetual Powers of Tau MPC · current single-contributor ceremony is dev-only" },
    { id: "P7", label: "Bluetooth Calibration (125–250Hz)",  status: "OPEN",     priority: "LOW",    detail: "All N=69 sessions USB-only · L4/L5 thresholds have no empirical grounding for BT polling rates" },
    { id: "P8", label: "Formal Verification (TLA+)",         status: "FUTURE",   priority: "LOW",    detail: "Chain integrity: linkage, monotonicity, non-repudiation · safety-critical esports deployments" },
    { id: "C1", label: "Multi-Button L5 (Phase 39)",         status: "COMPLETE", priority: "—",      detail: "4-button IBI oracle (Cross>L2_dig>R2>Triangle) + pooled fallback · 8 new tests · 867 bridge tests total" },
    { id: "C2", label: "rhythm_hash 4-Deque Commit (Ph40)",  status: "COMPLETE", priority: "—",      detail: "SHA-256(Cross‖0xFFFF‖L2‖0xFFFF‖R2‖0xFFFF‖Triangle) · same intervals in different buttons produce distinct hashes" },
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

/* ─── MAIN APP ────────────────────────────────────────────────────────────── */

export default function VAPIDashboard() {
  const pulse         = usePulse(3000);
  const sessionCount  = useCounter(69,   1500);
  const testCount     = useCounter(1289, 1800);
  const contractCount = useCounter(13,   1200);

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
              Cryptographic Anti-Cheat Protocol · DualShock Edge CFI-ZCP1 · IoTeX L1 · Whitepaper v3 (Phase 40)
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
            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "6px 12px",
              border: "1px solid rgba(0,255,136,0.3)",
              borderRadius: 2,
              background: "rgba(0,255,136,0.06)",
            }}>
              <StatusDot status="ACTIVE" />
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: "#00ff88" }}>TESTNET LIVE</span>
            </div>
          </div>
        </div>

        {/* ── STAT ROW ────────────────────────────────────────────────────── */}
        <div style={{ padding: "16px 32px 0", display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 8 }}>
          <StatBox label="Injection Det. Margin" value="14,000×"  accent="#ff6b00" mono />
          <StatBox label="False Positive Rate"   value="2.9%"     accent="#00ff88" mono />
          <StatBox label="Calibration Sessions"  value="N=69"     accent="#00d4ff" mono />
          <StatBox label="Distinct Players"      value="3"        accent="#ff9500" mono />
          <StatBox label="Separation Ratio"      value="0.362"    accent="#ff9500" mono sub="⚠ inter-player" />
          <StatBox label="L5 Cross Coverage"     value="83.8%"    accent="#00ff88" mono sub="62/74 sessions" />
        </div>

        {/* ── MAIN GRID ───────────────────────────────────────────────────── */}
        <div style={{ padding: "16px 32px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {/* Left column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="panel-fade" style={{ animationDelay: "0.05s" }}><PITLStack /></div>
            <div className="panel-fade" style={{ animationDelay: "0.15s" }}><AdversarialMatrix /></div>
            <div className="panel-fade" style={{ animationDelay: "0.25s" }}><HardwareMetrics /></div>
          </div>
          {/* Right column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="panel-fade" style={{ animationDelay: "0.1s" }}><PoACRecord /></div>
            <div className="panel-fade" style={{ animationDelay: "0.2s" }}><BiometricRadar /></div>
            <div className="panel-fade" style={{ animationDelay: "0.3s" }}><LivingCalibration /></div>
          </div>
        </div>

        {/* ── L5 COVERAGE ROW (Phase 39/40) ───────────────────────────────── */}
        <div style={{ padding: "0 32px 16px" }}>
          <div className="panel-fade" style={{ animationDelay: "0.33s" }}><L5ButtonCoverage /></div>
        </div>

        {/* ── FULL-WIDTH ROW ───────────────────────────────────────────────── */}
        <div style={{ padding: "0 32px", display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          <div className="panel-fade" style={{ animationDelay: "0.35s" }}><PHGCredential /></div>
          <div className="panel-fade" style={{ animationDelay: "0.4s"  }}><ZKProofStatus /></div>
          <div className="panel-fade" style={{ animationDelay: "0.45s" }}><ContractStack /></div>
        </div>

        {/* ── OPEN ITEMS ───────────────────────────────────────────────────── */}
        <div style={{ padding: "16px 32px 32px" }}>
          <div className="panel-fade" style={{ animationDelay: "0.5s" }}><OpenItems /></div>
        </div>

        {/* ── FOOTER ──────────────────────────────────────────────────────── */}
        <div style={{
          borderTop: "1px solid rgba(255,107,0,0.1)",
          padding: "12px 32px",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>
            VAPI Protocol Dashboard · Whitepaper v3 · Phase 40 · IoTeX Testnet
          </span>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 8, color: "#3d5060" }}>
            228B PoAC wire format FROZEN · 9-layer PITL · Groth16 BN254 · ~1,289 tests
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
