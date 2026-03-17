import { useRef, useState, useEffect, useCallback, useMemo, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { Canvas, useFrame } from '@react-three/fiber'
import { Physics } from '@react-three/rapier'
import { OrbitControls, Sparkles } from '@react-three/drei'
import * as THREE from 'three'
import QRCode from 'qrcode'

const VOID_BG = '#030507'
const ORANGE  = '#ff6b00'
const CYAN    = '#00d4ff'
const GREEN   = '#00ff88'
const RED     = '#ff2d55'
const DIM     = '#3d5060'

// 12-feature names (indices 0 and 10 are structurally zero — excluded from L4)
const BIO_FEATURES = [
  'Trigger Rate',   // 0 — structurally zero
  'L2 Onset Vel',   // 1
  'R2 Onset Vel',   // 2
  'Micro Tremor',   // 3
  'Grip Asymm',     // 4
  'Autocorr L1',    // 5
  'Autocorr L5',    // 6
  'Tremor Hz',      // 7
  'Tremor Power',   // 8
  'Spectral Ent',   // 9
  'Touch Var',      // 10 — structurally zero (pending recapture)
  'IBI Jitter',     // 11 — Phase 57
]

// Per-feature max expected human value (for radar normalization)
const BIO_NORM = [1, 5000, 5000, 600000, 1, 1, 1, 50, 1, 9, 1, 0.06]
const FEATURE_ZERO_IDX = new Set([0, 10])

const params     = new URLSearchParams(window.location.search)
const DEVICE_ID  = params.get('device') || ''
const BRIDGE_URL = params.get('bridge') || 'localhost:8080'

// ---------------------------------------------------------------------------
// useAutoDiscover — resolves device_id from URL param or bridge /api/v1/devices
// ---------------------------------------------------------------------------
function useAutoDiscover(initial) {
  const [deviceId, setDeviceId] = useState(initial)
  useEffect(() => {
    if (deviceId) return
    const poll = () => {
      fetch(`http://${BRIDGE_URL}/api/v1/devices`)
        .then(r => r.json())
        .then(devs => {
          const first = devs?.[0]?.device_id
          if (!first) return
          setDeviceId(first)
          const u = new URL(window.location)
          u.searchParams.set('device', first)
          window.history.replaceState({}, '', u)
        })
        .catch(() => {})
    }
    poll()
    const t = setInterval(poll, 3000)
    return () => clearInterval(t)
  }, [deviceId])
  return deviceId
}

// ---------------------------------------------------------------------------
// useTwinStream — live /ws/twin/{device_id} fusion WebSocket
// ---------------------------------------------------------------------------
function useTwinStream(deviceId) {
  const [frame,  setFrame]  = useState(null)
  const [record, setRecord] = useState(null)
  const [mode,   setMode]   = useState('OFFLINE')

  useEffect(() => {
    if (!deviceId) return
    let ws, timer
    const delay = { v: 3000 }
    function connect() {
      ws = new WebSocket(`ws://${BRIDGE_URL}/ws/twin/${deviceId}`)
      ws.onopen  = () => { delay.v = 3000; setMode('LIVE') }
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'frame')  setFrame(msg.data)
          if (msg.type === 'record') setRecord(msg.data)
        } catch {}
      }
      ws.onclose = () => {
        setMode('OFFLINE')
        delay.v = Math.min(delay.v * 2, 30000)
        timer = setTimeout(connect, delay.v)
      }
      ws.onerror = () => {}
    }
    connect()
    return () => { ws?.close(); clearTimeout(timer) }
  }, [deviceId])

  return { frame, record, mode }
}

// ---------------------------------------------------------------------------
// useTwinSnapshot — REST snapshot + chain lock points
// ---------------------------------------------------------------------------
function useTwinSnapshot(deviceId) {
  const [snap,  setSnap]  = useState(null)
  const [chain, setChain] = useState([])

  useEffect(() => {
    if (!deviceId) return
    fetch(`http://${BRIDGE_URL}/controller/twin/${deviceId}`)
      .then(r => r.json()).then(setSnap).catch(() => {})
    fetch(`http://${BRIDGE_URL}/controller/twin/${deviceId}/chain?limit=50`)
      .then(r => r.json()).then(setChain).catch(() => {})
  }, [deviceId])

  return { snap, chain }
}

// ---------------------------------------------------------------------------
// useReplayMode — Phase 61: session replay hook
// ---------------------------------------------------------------------------
function useReplayMode(deviceId) {
  const [replayFrames,  setReplayFrames]  = useState([])
  const [replayIdx,     setReplayIdx]     = useState(0)
  const [replayActive,  setReplayActive]  = useState(false)
  const [checkpointSet, setCheckpointSet] = useState(new Set())
  const intervalRef = useRef(null)

  // Load checkpoint set on mount
  useEffect(() => {
    if (!deviceId) return
    fetch(`http://${BRIDGE_URL}/controller/twin/${deviceId}/checkpoints?limit=200`)
      .then(r => r.json())
      .then(d => setCheckpointSet(new Set(d.checkpoints || [])))
      .catch(() => {})
  }, [deviceId])

  const startReplay = useCallback((recordHash) => {
    fetch(`http://${BRIDGE_URL}/controller/twin/${deviceId}/replay?record_hash=${recordHash}`)
      .then(r => r.json())
      .then(d => {
        if (!d.frames?.length) return
        setReplayFrames(d.frames)
        setReplayIdx(0)
        setReplayActive(true)
      }).catch(() => {})
  }, [deviceId])

  const stopReplay = useCallback(() => {
    setReplayActive(false)
    setReplayFrames([])
    setReplayIdx(0)
    clearInterval(intervalRef.current)
  }, [])

  // Advance playback at 20 Hz
  useEffect(() => {
    if (!replayActive || !replayFrames.length) return
    intervalRef.current = setInterval(() => {
      setReplayIdx(i => {
        if (i + 1 >= replayFrames.length) {
          setReplayActive(false)
          return 0
        }
        return i + 1
      })
    }, 50)
    return () => clearInterval(intervalRef.current)
  }, [replayActive, replayFrames.length])

  const currentReplayFrame = replayActive ? replayFrames[replayIdx] : null
  const replayProgress = replayFrames.length ? replayIdx / replayFrames.length : 0

  return {
    currentReplayFrame, replayActive, startReplay, stopReplay,
    replayProgress, replayIdx, replayTotal: replayFrames.length, checkpointSet,
  }
}

// ---------------------------------------------------------------------------
// useFeatureHistory — Phase 61: device feature vector history for scatter
// ---------------------------------------------------------------------------
function useFeatureHistory(deviceId) {
  const [history, setHistory] = useState([])
  useEffect(() => {
    if (!deviceId) return
    fetch(`http://${BRIDGE_URL}/controller/twin/${deviceId}/features?limit=50`)
      .then(r => r.json()).then(setHistory).catch(() => {})
  }, [deviceId])
  return history
}

// ---------------------------------------------------------------------------
// BiometricGlobe — glowing energy orb shaped by 12 L4 biometric features.
// Bot = near-perfect sphere. Human = unique lumpy plasma blob.
// Vertex displacement per azimuthal sector driven by feature[i] / BIO_NORM[i].
// ---------------------------------------------------------------------------
function BiometricGlobe({ frame, record, snap }) {
  const groupRef  = useRef()
  const coreRef   = useRef()
  const shellRef  = useRef()
  const midRef    = useRef()
  const outerRef  = useRef()
  const lightRef  = useRef()

  // History ring buffers for orbiting energy arc trails (50 frames each)
  const histRef = useRef({
    l2:  new Array(50).fill(0),
    r2:  new Array(50).fill(0),
    acc: new Array(50).fill(0),
  })

  // Biometric mean vector — parsed once per snap change, not per frame
  const mvRef = useRef(null)
  useEffect(() => {
    const mj = snap?.biometric_fingerprint?.mean_json
    try { mvRef.current = mj ? JSON.parse(mj) : null } catch { mvRef.current = null }
  }, [snap?.biometric_fingerprint?.mean_json])

  // Pre-allocated color objects — avoid GC pressure in 60 Hz loop
  const _redC   = useMemo(() => new THREE.Color(RED),   [])
  const _greenC = useMemo(() => new THREE.Color(GREEN), [])
  const _hc     = useMemo(() => new THREE.Color(),      [])

  // ── Geometries (created once) ──────────────────────────────────────────────
  const globe = useMemo(() => {
    const g = new THREE.SphereGeometry(1.1, 52, 52)
    g.userData.orig = new Float32Array(g.attributes.position.array)
    return g
  }, [])
  const coreGeo  = useMemo(() => new THREE.SphereGeometry(0.62, 24, 18),  [])
  const midGeo   = useMemo(() => new THREE.SphereGeometry(1.45, 18, 12),  [])
  const outerGeo = useMemo(() => new THREE.SphereGeometry(1.95, 16, 10),  [])
  const l2Geo    = useMemo(() => new THREE.BufferGeometry(), [])
  const r2Geo    = useMemo(() => new THREE.BufferGeometry(), [])
  const accGeo   = useMemo(() => new THREE.BufferGeometry(), [])

  useFrame((state, delta) => {
    if (!shellRef.current) return
    const t = state.clock.elapsedTime

    const l2Raw    = (frame?.l2_trigger   ?? 0) / 255
    const r2Raw    = (frame?.r2_trigger   ?? 0) / 255
    const accelMag = Math.hypot(frame?.accel_x ?? 0, frame?.accel_y ?? 0) / 32767
    const gyroX    = frame?.gyro_x ?? 0
    const gyroY    = frame?.gyro_y ?? 0
    const humanity = record?.humanity_prob ?? 0.5

    // Roll history buffers
    const H = histRef.current
    H.l2.push(l2Raw);     H.l2.shift()
    H.r2.push(r2Raw);     H.r2.shift()
    H.acc.push(accelMag); H.acc.shift()

    // Human color: RED (bot) → GREEN (human)
    const humanColor = _hc.lerpColors(_redC, _greenC, humanity)

    // ── Vertex displacement ────────────────────────────────────────────────
    const mv   = mvRef.current
    const pos  = globe.attributes.position
    const orig = globe.userData.orig
    for (let i = 0, n = pos.count; i < n; i++) {
      const ox = orig[i*3], oy = orig[i*3+1], oz = orig[i*3+2]
      // Map azimuthal angle to one of 12 feature sectors
      const fIdx = Math.floor(((Math.atan2(oz, ox) + Math.PI) / (2 * Math.PI)) * 12) % 12
      const feat = FEATURE_ZERO_IDX.has(fIdx) ? 0
        : mv ? Math.min(Math.abs(mv[fIdx]) / BIO_NORM[fIdx], 1) : 0
      const r = 1.1
        + feat  * 0.44                                   // biometric signature
        + 0.055 * Math.sin(t * 2.1 + fIdx * 0.72)       // living wobble
        + (l2Raw + r2Raw) * 0.06                         // trigger pulse
      const len = Math.sqrt(ox*ox + oy*oy + oz*oz) || 1
      pos.setXYZ(i, ox/len * r, oy/len * r, oz/len * r)
    }
    pos.needsUpdate = true
    globe.computeVertexNormals()

    // ── Globe rotation — slow self-spin + physical gyro feedback ──────────
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * (0.22 + gyroX * 0.08)
      groupRef.current.rotation.x += delta * gyroY * 0.04
    }

    // ── Material updates ───────────────────────────────────────────────────
    const pulse = 0.5 + 0.4 * Math.sin(t * 2.8)   // breathing pulse

    if (coreRef.current?.material) {
      coreRef.current.material.color.copy(humanColor)
      coreRef.current.material.emissive.copy(humanColor)
      coreRef.current.material.emissiveIntensity = 1.4 + pulse * 0.8 + (l2Raw + r2Raw) * 1.5
      coreRef.current.material.opacity = 0.55 + pulse * 0.15
    }
    if (shellRef.current?.material) {
      shellRef.current.material.emissive.copy(humanColor)
      shellRef.current.material.emissiveIntensity = 0.55 + r2Raw * 0.9 + pulse * 0.2
    }
    if (midRef.current?.material) {
      midRef.current.material.color.copy(humanColor)
      midRef.current.material.emissive.copy(humanColor)
      midRef.current.material.emissiveIntensity = 0.28 + pulse * 0.12
      midRef.current.material.opacity = 0.06 + pulse * 0.025
    }
    if (outerRef.current?.material) {
      outerRef.current.material.color.copy(humanColor)
      outerRef.current.material.emissive.copy(humanColor)
      outerRef.current.material.emissiveIntensity = 0.12 + pulse * 0.06
      outerRef.current.material.opacity = 0.028 + pulse * 0.01
    }
    if (lightRef.current) {
      lightRef.current.color.copy(humanColor)
      lightRef.current.intensity = 2.5 + pulse * 2.0 + (l2Raw + r2Raw) * 4.0
    }

    // ── Energy arc ribbon trails ───────────────────────────────────────────
    const arc = (hist, yBias, angSpeed, phase, amp) =>
      hist.map((v, k) => {
        const a = (k / hist.length) * Math.PI * 2 + angSpeed * t + phase
        const rr = 1.52 + v * amp
        return new THREE.Vector3(Math.cos(a) * rr, yBias + v * 0.26, Math.sin(a) * rr)
      })
    if (H.l2.length  > 1) l2Geo.setFromPoints(arc(H.l2,  0.32,  0.55, 0,            0.32))
    if (H.r2.length  > 1) r2Geo.setFromPoints(arc(H.r2, -0.32, -0.45, 0,            0.32))
    if (H.acc.length > 1) accGeo.setFromPoints(arc(H.acc, 0.00,  0.38, Math.PI*0.7, 0.22))
  })

  const humanity0   = record?.humanity_prob ?? 0.5
  const humanColor0 = new THREE.Color().lerpColors(new THREE.Color(RED), new THREE.Color(GREEN), humanity0)
  const l6pFlag     = record?.l6p_flag ?? false
  const anomalyThr  = snap?.calibration?.anomaly_threshold ?? 6.726

  return (
    <group ref={groupRef}>

      {/* Dynamic point light at globe centre — drives scene lighting */}
      <pointLight ref={lightRef} intensity={2.5} color={humanColor0} distance={8} decay={2} />

      {/* Innermost core — bright glowing nucleus */}
      <mesh ref={coreRef} geometry={coreGeo}>
        <meshStandardMaterial
          color={humanColor0} emissive={humanColor0} emissiveIntensity={1.4}
          transparent opacity={0.55} depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Biometric shell — vertex displaced by L4 feature magnitudes */}
      <mesh ref={shellRef} geometry={globe} castShadow>
        <meshStandardMaterial
          color="#0d1f30" roughness={0.28} metalness={0.72}
          emissive={humanColor0} emissiveIntensity={0.55}
          transparent opacity={0.82}
        />
      </mesh>

      {/* Mid glow layer — additive outer haze */}
      <mesh ref={midRef} geometry={midGeo}>
        <meshStandardMaterial
          color={humanColor0} emissive={humanColor0} emissiveIntensity={0.28}
          transparent opacity={0.06} depthWrite={false}
          side={THREE.BackSide} blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Outer corona — large diffuse glow envelope */}
      <mesh ref={outerRef} geometry={outerGeo}>
        <meshStandardMaterial
          color={humanColor0} emissive={humanColor0} emissiveIntensity={0.12}
          transparent opacity={0.028} depthWrite={false}
          side={THREE.BackSide} blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* L2 trigger energy arc (ORANGE, upper orbital plane) */}
      <line geometry={l2Geo}>
        <lineBasicMaterial color={ORANGE} transparent opacity={0.75}
          blending={THREE.AdditiveBlending} depthWrite={false} />
      </line>

      {/* R2 trigger energy arc (CYAN, lower orbital plane) */}
      <line geometry={r2Geo}>
        <lineBasicMaterial color={CYAN} transparent opacity={0.75}
          blending={THREE.AdditiveBlending} depthWrite={false} />
      </line>

      {/* Accel magnitude arc (GREEN, equatorial) */}
      <line geometry={accGeo}>
        <lineBasicMaterial color={GREEN} transparent opacity={0.50}
          blending={THREE.AdditiveBlending} depthWrite={false} />
      </line>

      {/* L6 passive challenge — pulsing equatorial ring */}
      {l6pFlag && (
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[1.72, 0.022, 8, 72]} />
          <meshStandardMaterial color={CYAN} emissive={CYAN}
            emissiveIntensity={2.5} transparent opacity={0.8}
            blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>
      )}

      {/* Anomaly sparkles */}
      <Sparkles
        count={record?.pitl_l4_distance > anomalyThr ? 60 : 18}
        scale={[3.8, 3.8, 3.8]} size={0.7}
        color={record?.inference === 0x30 ? RED : humanColor0}
        speed={0.35} opacity={0.55}
      />
    </group>
  )
}

// ---------------------------------------------------------------------------
// IBIHeartbeat — canvas showing organic IBI rhythm vs bot mechanical grid
// ---------------------------------------------------------------------------
function IBIHeartbeat({ ibiSnapshot }) {
  const canvasRef = useRef()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !ibiSnapshot) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width, H = canvas.height
    ctx.clearRect(0, 0, W, H)

    ctx.strokeStyle = 'rgba(255,45,85,0.25)'
    ctx.setLineDash([3, 6])
    ctx.lineWidth = 0.8
    const gridStep = W / 8
    for (let x = gridStep; x < W; x += gridStep) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke()
    }
    ctx.setLineDash([])

    const buttonOrder = ['r2', 'cross', 'l2', 'triangle']
    const colors = [ORANGE, CYAN, '#00ff88', '#ff9500']
    const rowH = H / 4 - 4

    buttonOrder.forEach((btn, bi) => {
      const ibis = ibiSnapshot[btn] || []
      if (!ibis.length) return
      const maxIBI = Math.max(...ibis, 500)
      const y0 = bi * (rowH + 4) + 2
      ibis.forEach((ibi, i) => {
        const x = (i / ibis.length) * W
        const h = (ibi / maxIBI) * rowH
        ctx.fillStyle = colors[bi] + 'cc'
        ctx.fillRect(x, y0 + rowH - h, W / ibis.length - 1, h)
      })
    })

    ctx.fillStyle = '#5a6a74'
    ctx.font = '8px JetBrains Mono, monospace'
    ctx.fillText('IBI BIOMETRIC HEARTBEAT', 4, H - 4)
  }, [ibiSnapshot])

  return (
    <canvas ref={canvasRef} width={300} height={120}
      style={{ width: '100%', display: 'block', borderTop: `1px solid rgba(255,107,0,0.2)` }} />
  )
}

// ---------------------------------------------------------------------------
// BiometricRadar — Phase 60A: 12-spoke fingerprint radar chart
// ---------------------------------------------------------------------------
function BiometricRadar({ meanJson }) {
  const canvasRef = useRef()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width, H = canvas.height
    const cx = W / 2, cy = H / 2 + 4
    const R = Math.min(cx, cy) - 28
    const N = 12
    ctx.clearRect(0, 0, W, H)

    // Reference rings
    for (let ri = 1; ri <= 4; ri++) {
      ctx.beginPath()
      for (let i = 0; i < N; i++) {
        const angle = (i / N) * Math.PI * 2 - Math.PI / 2
        const x = cx + Math.cos(angle) * R * (ri / 4)
        const y = cy + Math.sin(angle) * R * (ri / 4)
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.strokeStyle = `rgba(0,212,255,${0.06 + ri * 0.03})`
      ctx.lineWidth = 0.5
      ctx.stroke()
    }

    // Spokes and labels
    for (let i = 0; i < N; i++) {
      const angle = (i / N) * Math.PI * 2 - Math.PI / 2
      const isZero = FEATURE_ZERO_IDX.has(i)
      const x1 = cx + Math.cos(angle) * R
      const y1 = cy + Math.sin(angle) * R
      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(x1, y1)
      ctx.strokeStyle = isZero ? 'rgba(61,80,96,0.3)' : 'rgba(0,212,255,0.12)'
      ctx.lineWidth = 0.8
      ctx.stroke()
      const lx = cx + Math.cos(angle) * (R + 10)
      const ly = cy + Math.sin(angle) * (R + 10)
      ctx.fillStyle = isZero ? '#2a3540' : '#4a5a64'
      ctx.font = '6px JetBrains Mono, monospace'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(BIO_FEATURES[i].slice(0, 9), lx, ly)
    }

    // Player fingerprint polygon
    const vals = meanJson ? JSON.parse(meanJson) : null
    if (vals && vals.length >= 12) {
      ctx.beginPath()
      let started = false
      for (let i = 0; i < N; i++) {
        const angle = (i / N) * Math.PI * 2 - Math.PI / 2
        const norm = FEATURE_ZERO_IDX.has(i) ? 0 : Math.min(Math.abs(vals[i]) / BIO_NORM[i], 1)
        const x = cx + Math.cos(angle) * R * norm
        const y = cy + Math.sin(angle) * R * norm
        if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.fillStyle = 'rgba(255,107,0,0.12)'
      ctx.fill()
      ctx.strokeStyle = ORANGE
      ctx.lineWidth = 1.5
      ctx.stroke()

      // Dot at each active spoke tip
      for (let i = 0; i < N; i++) {
        if (FEATURE_ZERO_IDX.has(i)) continue
        const angle = (i / N) * Math.PI * 2 - Math.PI / 2
        const norm = Math.min(Math.abs(vals[i]) / BIO_NORM[i], 1)
        const x = cx + Math.cos(angle) * R * norm
        const y = cy + Math.sin(angle) * R * norm
        ctx.beginPath()
        ctx.arc(x, y, 2, 0, Math.PI * 2)
        ctx.fillStyle = ORANGE
        ctx.fill()
      }
    } else {
      // No data placeholder
      ctx.fillStyle = DIM
      ctx.font = '8px JetBrains Mono, monospace'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('NO FINGERPRINT DATA', cx, cy)
      ctx.fillStyle = '#2a3540'
      ctx.font = '7px JetBrains Mono, monospace'
      ctx.fillText('(requires ≥5 calibrated sessions)', cx, cy + 14)
    }

    ctx.fillStyle = '#3d5060'
    ctx.font = '7px JetBrains Mono, monospace'
    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    ctx.fillText('BIOMETRIC FINGERPRINT · 12 FEATURES · 10 ACTIVE', 4, 2)
  }, [meanJson])

  return (
    <canvas ref={canvasRef} width={300} height={260}
      style={{ width: '100%', display: 'block' }} />
  )
}

// ---------------------------------------------------------------------------
// L5RhythmOverlay — Phase 60A: temporal rhythm oracle visualization
// ---------------------------------------------------------------------------
function L5RhythmOverlay({ record }) {
  const l5Cv      = record?.pitl_l5_cv
  const l5Entropy = record?.pitl_l5_entropy
  const l5Quant   = record?.pitl_l5_quant
  const humanity  = record?.l5_rhythm_humanity

  // L5 thresholds (N=74 calibration)
  const CV_THRESH      = 0.08
  const ENTROPY_THRESH = 1.0

  const buttons    = ['r2', 'cross', 'l2', 'triangle']
  const btnColors  = [ORANGE, CYAN, GREEN, '#ff9500']
  const btnLabels  = ['R2 (SPRINT)', 'CROSS', 'L2', 'TRIANGLE']

  const isCvDict = l5Cv && typeof l5Cv === 'object' && !Array.isArray(l5Cv)

  return (
    <div style={{ padding: '8px 10px', fontFamily: 'JetBrains Mono, monospace' }}>
      <div style={{ fontSize: 8, color: CYAN, marginBottom: 8, letterSpacing: '0.1em' }}>
        L5 TEMPORAL RHYTHM ORACLE
      </div>

      {/* Entropy gauge */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
          <span style={{ fontSize: 7, color: DIM }}>IPI ENTROPY</span>
          <span style={{ fontSize: 7, color: l5Entropy != null && l5Entropy < ENTROPY_THRESH ? RED : '#c4cdd6' }}>
            {l5Entropy != null ? l5Entropy.toFixed(3) + ' bits' : '—'}
            {l5Entropy != null && l5Entropy < ENTROPY_THRESH && <span style={{ color: RED }}> ▼BOT</span>}
          </span>
        </div>
        <div style={{ background: '#0d1a24', height: 7, borderRadius: 2, overflow: 'hidden', position: 'relative' }}>
          <div style={{
            width: `${Math.min((l5Entropy ?? 0) / 3 * 100, 100)}%`,
            height: '100%',
            background: l5Entropy != null && l5Entropy < ENTROPY_THRESH ? RED : GREEN,
            transition: 'width 0.6s ease',
          }} />
          {/* Threshold marker */}
          <div style={{
            position: 'absolute', top: 0, bottom: 0,
            left: `${ENTROPY_THRESH / 3 * 100}%`,
            width: 1, background: ORANGE, opacity: 0.6,
          }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 6, color: '#2a3540', marginTop: 1 }}>
          <span>0</span><span style={{ color: '#3d5060' }}>▲{ENTROPY_THRESH}</span><span>3 bits</span>
        </div>
      </div>

      {/* Per-button CV bars */}
      <div style={{ fontSize: 7, color: DIM, marginBottom: 4 }}>INTER-PRESS INTERVAL CV</div>
      {buttons.map((btn, bi) => {
        const cv = isCvDict ? l5Cv[btn] : (bi === 0 && l5Cv != null ? Number(l5Cv) : null)
        if (cv == null) return (
          <div key={btn} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: 6, color: btnColors[bi], width: 52, flexShrink: 0 }}>{btnLabels[bi]}</span>
            <span style={{ fontSize: 6, color: '#2a3540' }}>—</span>
          </div>
        )
        const anomaly = cv < CV_THRESH
        return (
          <div key={btn} style={{ marginBottom: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 6, color: btnColors[bi], width: 52, flexShrink: 0 }}>{btnLabels[bi]}</span>
              <div style={{ flex: 1, background: '#0d1a24', height: 5, borderRadius: 2, overflow: 'hidden', position: 'relative' }}>
                <div style={{
                  width: `${Math.min(cv / 2 * 100, 100)}%`,
                  height: '100%',
                  background: anomaly ? RED : btnColors[bi],
                  transition: 'width 0.6s ease',
                }} />
                <div style={{
                  position: 'absolute', top: 0, bottom: 0,
                  left: `${CV_THRESH / 2 * 100}%`,
                  width: 1, background: '#5a6a74', opacity: 0.5,
                }} />
              </div>
              <span style={{ fontSize: 6, color: anomaly ? RED : '#c4cdd6', width: 32, textAlign: 'right', flexShrink: 0 }}>
                {cv.toFixed(3)}
              </span>
            </div>
          </div>
        )
      })}

      {/* Status flags */}
      <div style={{ marginTop: 6, borderTop: `1px solid ${DIM}22`, paddingTop: 6 }}>
        <div style={{ display: 'flex', gap: 12 }}>
          <div style={{ fontSize: 7, color: l5Quant ? RED : DIM }}>
            {l5Quant ? '● QUANT DETECT' : '○ QUANT CLEAN'}
          </div>
          {humanity != null && (
            <div style={{ fontSize: 7, color: '#c4cdd6' }}>
              L5 HUMAN: <span style={{ color: humanity > 0.5 ? GREEN : RED }}>
                {(humanity * 100).toFixed(0)}%
              </span>
            </div>
          )}
        </div>
        <div style={{ fontSize: 7, color: DIM, marginTop: 3 }}>
          PRIORITY: R2 &gt; CROSS &gt; L2 &gt; TRIANGLE (ncaa_cfb_26)
        </div>
        <div style={{ fontSize: 7, color: DIM }}>
          THRESHOLD: CV &lt; {CV_THRESH} | ENTROPY &lt; {ENTROPY_THRESH} bits
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BiometricScatter — Phase 60A/61: 2D feature cross-section + history dots
// ---------------------------------------------------------------------------
function BiometricScatter({ snap, history = [] }) {
  const canvasRef  = useRef()
  const meanJson   = snap?.biometric_fingerprint?.mean_json
  const nSessions  = snap?.biometric_fingerprint?.n_sessions ?? 0

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width, H = canvas.height
    const MARGIN = 28
    const W_ = W - MARGIN * 2
    const H_ = H - MARGIN * 2

    // Feature axes: X = micro_tremor_accel_variance (idx 3), Y = IBI jitter (idx 11)
    // Calibration stats from N=74 hardware sessions:
    //   micro_tremor mean ~278k LSB², human range 50k–600k
    //   IBI jitter human 0.001–0.05 s²
    const X_MAX = 650000
    const Y_MAX = 0.06
    const toX = v => MARGIN + Math.min(Math.max(v / X_MAX, 0), 1) * W_
    const toY = v => H - MARGIN - Math.min(Math.max(v / Y_MAX, 0), 1) * H_

    ctx.clearRect(0, 0, W, H)

    // Grid lines
    ctx.strokeStyle = 'rgba(0,212,255,0.07)'
    ctx.lineWidth = 0.5
    for (let i = 1; i <= 4; i++) {
      ctx.beginPath(); ctx.moveTo(toX(X_MAX * i / 4), MARGIN); ctx.lineTo(toX(X_MAX * i / 4), H - MARGIN); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(MARGIN, toY(Y_MAX * i / 4)); ctx.lineTo(W - MARGIN, toY(Y_MAX * i / 4)); ctx.stroke()
    }

    // Axes
    ctx.strokeStyle = 'rgba(61,80,96,0.5)'
    ctx.lineWidth = 0.8
    ctx.beginPath(); ctx.moveTo(MARGIN, H - MARGIN); ctx.lineTo(W - MARGIN, H - MARGIN); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(MARGIN, MARGIN); ctx.lineTo(MARGIN, H - MARGIN); ctx.stroke()

    // Bot zone (near-zero both axes: macro-timed bot has no tremor and no IBI jitter)
    const botZoneR = W_ * 0.09
    ctx.fillStyle = 'rgba(255,45,85,0.07)'
    ctx.beginPath(); ctx.arc(toX(X_MAX * 0.04), toY(Y_MAX * 0.02), botZoneR, 0, Math.PI * 2); ctx.fill()
    ctx.strokeStyle = 'rgba(255,45,85,0.3)'
    ctx.lineWidth = 0.8
    ctx.setLineDash([2, 4])
    ctx.beginPath(); ctx.arc(toX(X_MAX * 0.04), toY(Y_MAX * 0.02), botZoneR, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = '#5a2030'
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.fillText('BOT', toX(X_MAX * 0.04), toY(Y_MAX * 0.02) + botZoneR + 2)

    // Human calibration ellipse (N=74, centroid approximated from hardware session stats)
    // L4 dist_mean=2.083, dist_std=1.642 — ellipse represents ~2σ human zone
    const calCx = X_MAX * 0.43, calCy = Y_MAX * 0.38
    const eW = W_ * 0.40, eH = H_ * 0.44
    ctx.strokeStyle = 'rgba(0,255,136,0.3)'
    ctx.lineWidth = 1
    ctx.setLineDash([3, 5])
    ctx.beginPath(); ctx.ellipse(toX(calCx), toY(calCy), eW / 2, eH / 2, 0, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = 'rgba(0,255,136,0.05)'
    ctx.beginPath(); ctx.ellipse(toX(calCx), toY(calCy), eW / 2, eH / 2, 0, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = 'rgba(0,255,136,0.45)'
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'bottom'
    ctx.fillText('HUMAN 2σ  (N=74)', toX(calCx), toY(calCy) - eH / 2 - 2)

    // Player fingerprint dot
    if (meanJson) {
      const vals = JSON.parse(meanJson)
      if (vals.length >= 12) {
        const px = vals[3]   // micro_tremor_accel_variance
        const py = vals[11]  // press_timing_jitter_variance
        const dotX = toX(px), dotY = toY(py)
        // Glow ring
        const grad = ctx.createRadialGradient(dotX, dotY, 0, dotX, dotY, 14)
        grad.addColorStop(0, ORANGE + 'aa')
        grad.addColorStop(1, ORANGE + '00')
        ctx.fillStyle = grad
        ctx.beginPath(); ctx.arc(dotX, dotY, 14, 0, Math.PI * 2); ctx.fill()
        // Dot
        ctx.fillStyle = ORANGE
        ctx.beginPath(); ctx.arc(dotX, dotY, 4.5, 0, Math.PI * 2); ctx.fill()
        // Label
        ctx.fillStyle = ORANGE
        ctx.font = '7px JetBrains Mono, monospace'
        ctx.textAlign = 'left'
        ctx.textBaseline = 'middle'
        ctx.fillText(`PLAYER (${nSessions}s)`, dotX + 7, dotY)
      }
    } else {
      ctx.fillStyle = DIM
      ctx.font = '8px JetBrains Mono, monospace'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('NO FINGERPRINT — NEEDS SESSIONS', W / 2, H / 2)
    }

    // Phase 61: history dots — actual device DB records (cyan, semi-transparent)
    if (history.length) {
      history.forEach((rec) => {
        if (!rec.features || rec.features.length < 12) return
        const hx = toX(rec.features[3])   // micro_tremor_accel_variance
        const hy = toY(rec.features[11])  // press_timing_jitter_variance
        ctx.fillStyle = CYAN + '66'
        ctx.beginPath(); ctx.arc(hx, hy, 2.5, 0, Math.PI * 2); ctx.fill()
      })
      ctx.fillStyle = CYAN + '88'
      ctx.font = '6px JetBrains Mono, monospace'
      ctx.textAlign = 'right'
      ctx.textBaseline = 'top'
      ctx.fillText(`\u25cf ${history.length} DEVICE RECORDS`, W - MARGIN, MARGIN + 8)
    }

    // Axis labels
    ctx.fillStyle = '#3d5060'
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'bottom'
    ctx.fillText('MICRO TREMOR VARIANCE (idx 3) →', W / 2, H - 1)
    ctx.save()
    ctx.translate(8, H / 2)
    ctx.rotate(-Math.PI / 2)
    ctx.textBaseline = 'top'
    ctx.fillText('IBI JITTER VAR (idx 11) →', 0, 0)
    ctx.restore()

    // Corner note
    ctx.fillStyle = '#2a3540'
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.textAlign = 'right'
    ctx.textBaseline = 'top'
    ctx.fillText('sep ratio 0.362 — intra-player only', W - 2, 2)
  }, [meanJson, nSessions, history])

  return (
    <canvas ref={canvasRef} width={300} height={220}
      style={{ width: '100%', display: 'block' }} />
  )
}

// ---------------------------------------------------------------------------
// ProofShareQR — Phase 60A: QR code modal for sharing proof deeplink
// ---------------------------------------------------------------------------
function ProofShareQR({ record, deviceId: propDeviceId }) {
  const [showQR,  setShowQR]  = useState(false)
  const [qrUrl,   setQrUrl]   = useState(null)
  const [copied,  setCopied]  = useState(false)

  const txHash      = record?.tx_hash
  const explorerUrl = txHash
    ? `https://testnet.iotexscan.io/action/${txHash}`
    : null
  const twinUrl = `${window.location.origin}${window.location.pathname}?device=${propDeviceId || DEVICE_ID}`

  useEffect(() => {
    if (!showQR) return
    const target = explorerUrl || twinUrl
    QRCode.toDataURL(target, {
      width: 160, margin: 1,
      color: { dark: '#ff6b00', light: '#030507' },
    }).then(setQrUrl).catch(() => setQrUrl(null))
  }, [showQR, explorerUrl, twinUrl])

  const handleCopy = () => {
    navigator.clipboard.writeText(twinUrl).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <>
      <button onClick={() => setShowQR(true)}
        style={{ width: '100%', fontSize: 8, background: 'none',
                 border: `1px solid ${ORANGE}44`, borderRadius: 2,
                 color: ORANGE, padding: '4px 0', cursor: 'pointer',
                 fontFamily: 'JetBrains Mono, monospace',
                 letterSpacing: '0.1em', marginTop: 8 }}>
        SHARE PROOF ↗
      </button>

      {showQR && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(3,5,7,0.93)',
                      zIndex: 200, display: 'flex', alignItems: 'center',
                      justifyContent: 'center' }}
          onClick={() => setShowQR(false)}>
          <div style={{ background: '#080f14', border: `1px solid ${ORANGE}44`,
                        borderRadius: 6, padding: 20, width: 300,
                        fontFamily: 'JetBrains Mono, monospace' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ color: ORANGE, fontSize: 11, letterSpacing: '0.15em', marginBottom: 12 }}>
              VAPI PROOF SHARE
            </div>

            {/* QR Code */}
            {qrUrl ? (
              <img src={qrUrl} style={{ width: 160, height: 160, display: 'block',
                                        margin: '0 auto 12px', imageRendering: 'pixelated' }} alt="QR" />
            ) : (
              <div style={{ width: 160, height: 160, margin: '0 auto 12px', background: '#0d1a24',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: 8, color: DIM }}>
                GENERATING…
              </div>
            )}

            {/* IoTeX chain link */}
            {explorerUrl && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 7, color: CYAN, marginBottom: 3 }}>IoTeX CHAIN RECORD</div>
                <a href={explorerUrl} target="_blank" rel="noopener noreferrer"
                  style={{ fontSize: 7, color: ORANGE, wordBreak: 'break-all', textDecoration: 'none' }}>
                  {explorerUrl}
                </a>
              </div>
            )}

            {/* Record hash */}
            {record?.record_hash && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 7, color: CYAN, marginBottom: 2 }}>SHA-256 RECORD HASH</div>
                <div style={{ fontSize: 7, color: '#c4cdd6', wordBreak: 'break-all', lineHeight: 1.4 }}>
                  {record.record_hash}
                </div>
              </div>
            )}

            {/* Humanity */}
            {record && (
              <div style={{ fontSize: 8, color: '#c4cdd6', marginBottom: 12 }}>
                HUMANITY: <span style={{ color: (record.humanity_prob ?? 0) > 0.5 ? GREEN : RED }}>
                  {((record.humanity_prob ?? 0) * 100).toFixed(1)}%
                </span>
                {' · '}L4: {record.pitl_l4_distance?.toFixed(3) ?? '—'}
              </div>
            )}

            <button onClick={handleCopy}
              style={{ width: '100%', fontSize: 8, background: 'none',
                       border: `1px solid ${ORANGE}44`, borderRadius: 2,
                       color: copied ? GREEN : ORANGE, padding: '5px 0',
                       cursor: 'pointer', fontFamily: 'inherit', marginBottom: 6 }}>
              {copied ? 'COPIED ✓' : 'COPY TWIN PAGE URL'}
            </button>
            <button onClick={() => setShowQR(false)}
              style={{ width: '100%', fontSize: 8, background: 'none',
                       border: `1px solid ${DIM}`, borderRadius: 2,
                       color: DIM, padding: '5px 0', cursor: 'pointer',
                       fontFamily: 'inherit' }}>
              CLOSE
            </button>
          </div>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// PoACHelix — DNA helix from chain records
// ---------------------------------------------------------------------------
function PoACHelix({ chain }) {
  const points = chain.slice(0, 30).map((r, i) => {
    const t = i / 30
    const angle = t * Math.PI * 4
    return new THREE.Vector3(
      Math.cos(angle) * (1.8 + 0.1 * i),
      t * 3 - 1.5,
      Math.sin(angle) * (1.8 + 0.1 * i),
    )
  })
  if (!points.length) return null
  return (
    <group>
      {chain.slice(0, 29).map((r, i) => {
        const color = r.inference === 0x20 ? GREEN : r.inference === 0x30 ? '#ff9500' : RED
        return (
          <mesh key={r.record_hash || i} position={points[i]}>
            <sphereGeometry args={[0.04, 8, 8]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.8} />
          </mesh>
        )
      })}
    </group>
  )
}

// ---------------------------------------------------------------------------
// ProofAnchorPanel — top-right HTML overlay (with SHARE PROOF button)
// ---------------------------------------------------------------------------
function ProofAnchorPanel({ snap, record, mode, deviceId }) {
  if (!snap) return null
  const { ioid, passport, audit_log, anomaly_trend, calibration } = snap
  const trendColor = { IMPROVING: GREEN, STABLE: CYAN, DEGRADING: RED }[anomaly_trend] || DIM

  return (
    <div style={{ position: 'absolute', right: 12, top: 52, width: 240,
                  background: 'rgba(8,15,20,0.92)', border: `1px solid ${ORANGE}22`,
                  borderRadius: 4, padding: 12, fontFamily: 'JetBrains Mono, monospace' }}>
      <div style={{ color: CYAN, fontSize: 8, marginBottom: 6 }}>DEVICE IDENTITY</div>
      <div style={{ color: '#c4cdd6', fontSize: 9, wordBreak: 'break-all', marginBottom: 8 }}>
        {ioid?.did || '— not registered'}
      </div>

      <div style={{ color: CYAN, fontSize: 8, marginBottom: 4 }}>ZK PASSPORT</div>
      {passport?.issued ? (
        <div style={{ fontSize: 8, color: GREEN, marginBottom: 8 }}>
          ISSUED {passport.on_chain ? '· ON-CHAIN' : '· LOCAL'}
          <div style={{ color: DIM, wordBreak: 'break-all' }}>
            {passport.passport_hash?.slice(0, 24)}…
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 8, color: DIM, marginBottom: 8 }}>PENDING</div>
      )}

      <div style={{ color: CYAN, fontSize: 8, marginBottom: 4 }}>L4 PROFILE</div>
      <div style={{ fontSize: 8, color: '#c4cdd6', marginBottom: 8 }}>
        THRESHOLD: {calibration?.anomaly_threshold?.toFixed(3) ?? '—'}<br />
        SESSIONS:  {calibration?.session_count ?? 0}<br />
        TREND: <span style={{ color: trendColor }}>{anomaly_trend}</span>
      </div>

      {record && (
        <>
          <div style={{ color: CYAN, fontSize: 8, marginBottom: 4 }}>LIVE RECORD</div>
          <div style={{ fontSize: 8, color: '#c4cdd6' }}>
            L4: {record.pitl_l4_distance?.toFixed(3) ?? '—'}<br />
            HUMANITY: {((record.humanity_prob ?? 0) * 100).toFixed(1)}%<br />
            HASH: {record.record_hash?.slice(0, 12)}…
          </div>
        </>
      )}

      {audit_log?.length > 0 && (
        <>
          <div style={{ color: CYAN, fontSize: 8, marginTop: 8, marginBottom: 4 }}>
            PROOF QUERIES ({audit_log.length})
          </div>
          {audit_log.slice(0, 3).map((e, i) => (
            <div key={i} style={{ fontSize: 7, color: DIM, marginBottom: 2 }}>
              {e.outcome.toUpperCase()} · {e.endpoint.replace('/operator/passport', '/passport')}
            </div>
          ))}
        </>
      )}

      <div style={{ fontSize: 7, color: '#3d5060', marginTop: 8, lineHeight: 1.4 }}>
        L4 is intra-player anomaly detection only.<br />
        Separation ratio 0.362 — biometric transplant attack not blocked.
      </div>

      <div style={{ marginTop: 8, fontSize: 8, color: mode === 'LIVE' ? GREEN : RED }}>
        {mode === 'LIVE' ? '● LIVE' : '○ OFFLINE'}
      </div>

      {/* Phase 60A: QR share button */}
      <ProofShareQR record={record} deviceId={deviceId} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// ControllerTwinPage — main component
// ---------------------------------------------------------------------------
function ControllerTwinPage() {
  const deviceId = useAutoDiscover(DEVICE_ID)

  const { frame, record, mode } = useTwinStream(deviceId)
  const { snap, chain }         = useTwinSnapshot(deviceId)
  const [chainIdx, setChainIdx] = useState(0)
  const [leftTab, setLeftTab]   = useState('HEARTBEAT')   // Phase 60A tab switcher

  // Phase 61: session replay + feature history
  const {
    currentReplayFrame, replayActive, startReplay, stopReplay,
    replayProgress, replayIdx, replayTotal, checkpointSet,
  } = useReplayMode(deviceId)
  const featureHistory = useFeatureHistory(deviceId)

  const lockedRecord = chain[chainIdx] || record
  const activeFrame  = currentReplayFrame || frame   // replay overrides live

  const TABS = ['HEARTBEAT', 'RADAR', 'L5', 'SCATTER']
  const TAB_LABELS = { HEARTBEAT: 'HEARTBEAT', RADAR: 'RADAR', L5: 'L5 RHYTHM', SCATTER: 'BIOM MAP' }

  return (
    <div style={{ width: '100vw', height: '100vh', background: VOID_BG,
                  fontFamily: 'Rajdhani, sans-serif', overflow: 'hidden' }}>

      {/* Header */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10,
                    padding: '10px 16px', background: 'rgba(3,5,7,0.9)',
                    borderBottom: `1px solid ${ORANGE}22`,
                    display: 'flex', alignItems: 'center', gap: 16 }}>
        <span style={{ color: ORANGE, fontWeight: 700, fontSize: 14, letterSpacing: '0.15em' }}>
          VAPI · MY CONTROLLER
        </span>
        <span style={{ color: DIM, fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>
          DualShock Edge CFI-ZCP1 · {deviceId ? deviceId.slice(0, 16) + '…' : 'DISCOVERING…'}
        </span>
        <span style={{ color: '#1e3448', fontSize: 8, fontFamily: 'JetBrains Mono, monospace' }}>
          BIOMETRIC FINGERPRINT GLOBE · vertex = L4 feature / max
        </span>
        <a href="/" style={{ marginLeft: 'auto', color: DIM, fontSize: 9,
                             fontFamily: 'JetBrains Mono, monospace', textDecoration: 'none' }}>
          ← DASHBOARD
        </a>
      </div>

      {/* Three.js Canvas */}
      <Canvas shadows camera={{ position: [0, 0.4, 3.6], fov: 48 }}
        gl={{ antialias: true }}
        style={{ position: 'absolute', inset: 0 }}>
        <color attach="background" args={[VOID_BG]} />
        <ambientLight intensity={0.06} />
        <pointLight position={[4, 4, 4]} intensity={0.4} color="#ffffff" />
        <OrbitControls enablePan={false} minDistance={2.2} maxDistance={7} target={[0, 0, 0]} />
        <Physics gravity={[0, 0, 0]}>
          <Suspense fallback={null}>
            <BiometricGlobe frame={activeFrame} record={lockedRecord} snap={snap} />
            <PoACHelix chain={chain} />
          </Suspense>
        </Physics>
      </Canvas>

      {/* Proof Anchor Panel (top-right) */}
      <ProofAnchorPanel snap={snap} record={lockedRecord} mode={mode} deviceId={deviceId} />

      {/* Left panel — Phase 60A tabbed viz */}
      <div style={{ position: 'absolute', bottom: 56, left: 12, width: 320,
                    background: 'rgba(8,15,20,0.90)', border: `1px solid ${ORANGE}22`,
                    borderRadius: 4, overflow: 'hidden' }}>

        {/* Tab bar */}
        <div style={{ display: 'flex', borderBottom: `1px solid ${ORANGE}18` }}>
          {TABS.map(tab => (
            <button key={tab} onClick={() => setLeftTab(tab)}
              style={{ flex: 1, padding: '5px 2px', fontSize: 7, background: 'none',
                       border: 'none', cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace',
                       color: leftTab === tab ? ORANGE : DIM,
                       borderBottom: leftTab === tab ? `2px solid ${ORANGE}` : '2px solid transparent',
                       letterSpacing: '0.05em' }}>
              {TAB_LABELS[tab]}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {leftTab === 'HEARTBEAT' && (
          <>
            <div style={{ padding: '5px 8px 2px', fontSize: 8, color: CYAN,
                          fontFamily: 'JetBrains Mono, monospace' }}>
              IBI BIOMETRIC HEARTBEAT · R2 &gt; CROSS &gt; L2 &gt; TRIANGLE
            </div>
            <IBIHeartbeat ibiSnapshot={record?.ibi_snapshot} />
          </>
        )}

        {leftTab === 'RADAR' && (
          <BiometricRadar meanJson={snap?.biometric_fingerprint?.mean_json} />
        )}

        {leftTab === 'L5' && (
          <L5RhythmOverlay record={lockedRecord} />
        )}

        {leftTab === 'SCATTER' && (
          <>
            <div style={{ padding: '5px 8px 2px', fontSize: 8, color: CYAN,
                          fontFamily: 'JetBrains Mono, monospace' }}>
              FEATURE SPACE: TREMOR × IBI JITTER
            </div>
            <BiometricScatter snap={snap} history={featureHistory} />
          </>
        )}
      </div>

      {/* Phase 61: Replay status bar */}
      {replayActive && (
        <div style={{ position: 'absolute', bottom: 56, right: 12,
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
                      color: CYAN, background: 'rgba(8,15,20,0.9)',
                      border: `1px solid ${CYAN}44`, padding: '4px 8px', borderRadius: 3 }}>
          &#9654; REPLAY {replayIdx}/{replayTotal}
          <button onClick={stopReplay}
            style={{ marginLeft: 8, color: RED, background: 'none',
                     border: 'none', cursor: 'pointer', fontSize: 8 }}>
            &#9632; STOP
          </button>
          <div style={{ height: 2, background: '#0d1a24', marginTop: 3 }}>
            <div style={{ width: `${replayProgress * 100}%`, height: '100%', background: CYAN }} />
          </div>
        </div>
      )}

      {/* Chain Timeline Scrubber */}
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0,
                    background: 'rgba(8,15,20,0.95)', borderTop: `1px solid ${ORANGE}22`,
                    padding: '6px 12px' }}>
        <div style={{ fontSize: 7, color: DIM, fontFamily: 'JetBrains Mono, monospace',
                      marginBottom: 3 }}>
          PoAC CHAIN — {chain.length} LOCK POINTS · CLICK TO INSPECT
          {checkpointSet.size > 0 && (
            <span style={{ color: CYAN, marginLeft: 8 }}>
              &#9654; {checkpointSet.size} REPLAYABLE
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 2, overflowX: 'auto' }}>
          {chain.map((r, i) => {
            const color = r.inference === 0x20 ? GREEN : r.inference === 0x30 ? '#ff9500' : RED
            const hasCheckpoint = checkpointSet.has(r.record_hash)
            return (
              <div key={r.record_hash || i}
                onClick={() => {
                  setChainIdx(i)
                  if (hasCheckpoint) startReplay(r.record_hash)
                }}
                title={`${r.record_hash?.slice(0, 16)}… | L4: ${r.pitl_l4_distance?.toFixed(2)}${hasCheckpoint ? ' | REPLAYABLE' : ''}`}
                style={{ width: 10, height: 18, background: color,
                         opacity: chainIdx === i ? 1 : 0.4, cursor: 'pointer',
                         flexShrink: 0, borderRadius: 1,
                         border: chainIdx === i
                           ? `1px solid ${ORANGE}`
                           : hasCheckpoint ? `1px solid ${CYAN}88` : 'none' }} />
            )
          })}
        </div>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<ControllerTwinPage />)
