"""
HTTP Transport + PoHG Pulse Dashboard — FastAPI-based webhook receiver and monitoring UI.

Endpoints:
  POST /api/v1/records          — Submit a single 228-byte PoAC record
  POST /api/v1/records/batch    — Submit multiple records (multipart binary)
  GET  /api/v1/devices          — List all known devices
  GET  /api/v1/devices/{id}     — Get device details
  GET  /api/v1/stats            — Bridge statistics
  GET  /api/v1/records/recent   — Recent records feed (optional ?device_id=)
  WS   /ws/records              — Real-time record stream (WebSocket)
  GET  /                        — Operator dashboard (PoHG Pulse Observatory)
  GET  /player/{device_id}      — Player dashboard (Trust Ledger + Identity Glyph)
"""

import json
import logging
import time

from fastapi import FastAPI, Request, Response, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..codec import POAC_RECORD_SIZE
from ..config import Config
from ..store import Store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebSocket broadcaster — module-level singleton
# ---------------------------------------------------------------------------

_ws_clients: set[WebSocket] = set()


async def ws_broadcast(message: str):
    """Broadcast a JSON string to all connected WebSocket clients. Dead clients are removed."""
    dead: set[WebSocket] = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# Inference name map for WebSocket messages (gaming codes)
_GAMING_INF_NAMES = {
    0x20: "NOMINAL",
    0x21: "SKILLED",
    0x28: "DRIVER_INJECT",
    0x29: "WALLHACK_PREAIM",
    0x2A: "AIMBOT_BEHAVIORAL",
    0x2B: "TEMPORAL_ANOMALY",
    0x30: "BIOMETRIC_ANOMALY",
}


def _record_to_ws_msg(record) -> str:
    """Serialize a PoACRecord to a WebSocket broadcast JSON string."""
    return json.dumps({
        "record_hash":    record.record_hash.hex()[:16],
        "inference":      record.inference_result,
        "inference_name": _GAMING_INF_NAMES.get(record.inference_result,
                                                  f"0x{record.inference_result:02X}"),
        "confidence":     record.confidence,
        "chain_ok":       True,
        "pitl_l4_distance": record.pitl_l4_distance,
        "pitl_l5_cv":       record.pitl_l5_cv,
        "pitl_l5_entropy":  record.pitl_l5_entropy_bits,
        "pitl_l5_quant":    record.pitl_l5_quant_score,
        "ts_ms":           record.timestamp_ms,
        "device_id":       record.device_id.hex()[:16] if record.device_id else "",
    })


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(cfg: Config, store: Store, on_record) -> FastAPI:
    """Create the FastAPI application with all routes."""

    app = FastAPI(title="VAPI Bridge", version="0.2.0-rc1")

    # --- WebSocket ---

    @app.websocket("/ws/records")
    async def ws_records(ws: WebSocket):
        await ws.accept()
        _ws_clients.add(ws)
        try:
            while True:
                # Keep-alive: client sends ping text, we ignore it
                await ws.receive_text()
        except (WebSocketDisconnect, Exception):
            _ws_clients.discard(ws)

    # --- Webhook API ---

    @app.post("/api/v1/records")
    async def submit_record(request: Request):
        body = await request.body()
        if len(body) != POAC_RECORD_SIZE:
            raise HTTPException(
                400, f"Expected {POAC_RECORD_SIZE} bytes, got {len(body)}"
            )
        source = f"http:{request.client.host}"
        try:
            await on_record(body, source)
            return {"status": "accepted"}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/v1/records/batch")
    async def submit_batch(request: Request):
        body = await request.body()
        if len(body) % POAC_RECORD_SIZE != 0:
            raise HTTPException(
                400,
                f"Body size {len(body)} is not a multiple of {POAC_RECORD_SIZE}",
            )
        count = len(body) // POAC_RECORD_SIZE
        accepted = 0
        errors = []
        for i in range(count):
            chunk = body[i * POAC_RECORD_SIZE : (i + 1) * POAC_RECORD_SIZE]
            source = f"http-batch:{request.client.host}"
            try:
                await on_record(chunk, source)
                accepted += 1
            except ValueError as e:
                errors.append({"index": i, "error": str(e)})
        return {"accepted": accepted, "errors": errors}

    # --- Read API ---

    @app.get("/api/v1/stats")
    async def get_stats():
        return store.get_stats()

    @app.get("/api/v1/devices")
    async def list_devices():
        return store.list_devices()

    @app.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str):
        device = store.get_device(device_id)
        if not device:
            raise HTTPException(404, "Device not found")
        return device

    @app.get("/api/v1/records/recent")
    async def recent_records(limit: int = 50, device_id: str | None = None):
        return store.get_recent_records(min(limit, 200), device_id=device_id)

    # --- Dashboards ---

    @app.get("/", response_class=HTMLResponse)
    async def operator_dashboard():
        return OPERATOR_HTML

    @app.get("/player/{device_id}", response_class=HTMLResponse)
    async def player_dashboard(device_id: str):
        return PLAYER_DASHBOARD_HTML.replace("__DEVICE_ID__", device_id)

    return app


# ---------------------------------------------------------------------------
# Operator Dashboard — PoHG Pulse Observatory
# ---------------------------------------------------------------------------

OPERATOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PoHG Pulse — Operator</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        [x-cloak]{display:none!important}
        .pulse{animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
        .heartbeat{animation:hb 1.5s ease-in-out}
        @keyframes hb{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
        #chain-ribbon{display:flex;align-items:flex-end;gap:2px;overflow:hidden;flex-direction:row}
        .chain-link{flex-shrink:0;width:18px;border-radius:2px;cursor:pointer;transition:opacity .2s}
        .chain-link:hover{opacity:.7}
        .tooltip{position:fixed;background:#1e293b;border:1px solid #334155;padding:6px 10px;
                 border-radius:6px;font-size:11px;pointer-events:none;z-index:999;
                 color:#e2e8f0;display:none}
    </style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen" x-data="operator()" x-init="init()">
<div id="tip" class="tooltip"></div>

<!-- Top Bar -->
<div class="border-b border-gray-800 px-6 py-3 flex items-center justify-between bg-gray-900">
    <div class="flex items-center gap-3">
        <div class="w-2 h-2 rounded-full bg-emerald-400 pulse"></div>
        <span class="font-bold text-blue-400 text-lg">PoHG Pulse</span>
        <span class="text-gray-500 text-sm">Proof of Human Gaming Observatory</span>
    </div>
    <div class="flex items-center gap-6 text-sm">
        <span class="text-gray-400">Devices: <span class="text-white font-mono" x-text="stats.devices_active ?? 0"></span></span>
        <span class="text-gray-400">Records/min: <span class="text-emerald-400 font-mono" x-text="recPerMin"></span></span>
        <span class="text-gray-400">WS: <span :class="wsConnected ? 'text-emerald-400' : 'text-red-400'"
              x-text="wsConnected ? 'live' : 'offline'"></span></span>
    </div>
</div>

<div class="max-w-screen-2xl mx-auto px-4 py-4 space-y-4">

    <!-- Panel 1: Chain Ribbon -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <div class="flex items-center justify-between mb-3">
            <h2 class="font-semibold text-gray-200">Chain Ribbon
                <span class="text-xs text-gray-500 ml-2">newest →</span></h2>
            <div class="flex gap-3 text-xs">
                <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-sm bg-emerald-500"></span>NOMINAL</span>
                <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-sm bg-amber-500"></span>Advisory</span>
                <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-sm bg-red-500"></span>Cheat</span>
            </div>
        </div>
        <div id="chain-ribbon" class="h-16 bg-gray-950 rounded p-1"></div>
    </div>

    <!-- Row: Panel 2 + Panel 3 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">

        <!-- Panel 2: Human Signal Waveform -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
            <h2 class="font-semibold text-gray-200 mb-3">Human Signal Waveform
                <span class="text-xs text-gray-500 ml-2">last 60 records</span></h2>
            <canvas id="waveChart" height="160"></canvas>
        </div>

        <!-- Panel 3: SDK Attestation Heartbeat -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
            <h2 class="font-semibold text-gray-200 mb-3">SDK Attestation Heartbeat</h2>
            <div class="space-y-2" x-show="attestation" x-cloak>
                <template x-for="layer in layers" :key="layer.id">
                    <div class="flex items-center gap-3 text-sm">
                        <span :class="layer.active ? 'text-emerald-400' : 'text-red-400'" class="text-lg">●</span>
                        <span class="w-32 text-gray-300" x-text="layer.id"></span>
                        <div class="flex-1 bg-gray-800 rounded-full h-2">
                            <div class="h-2 rounded-full transition-all duration-500"
                                 :class="layer.active ? 'bg-emerald-500' : 'bg-red-700'"
                                 :style="`width:${(layer.score * 100).toFixed(0)}%`"></div>
                        </div>
                        <span class="text-gray-400 text-xs w-12" x-text="`${(layer.score * 100).toFixed(0)}%`"></span>
                        <span :class="layer.active ? 'text-emerald-300 text-xs' : 'text-red-300 text-xs'"
                              x-text="layer.active ? 'ACTIVE' : 'OFFLINE'"></span>
                    </div>
                </template>
                <div class="pt-2 border-t border-gray-800 mt-2">
                    <div class="flex items-center justify-between text-xs text-gray-500">
                        <span>Attestation hash:
                            <span class="font-mono text-blue-400"
                                  x-text="(attestation.attestation_hash||'').slice(0,16) + '...'"></span></span>
                        <span x-text="attestation.sdk_version"></span>
                    </div>
                </div>
            </div>
            <div class="text-gray-500 text-sm" x-show="!attestation">Loading attestation...</div>
        </div>
    </div>

    <!-- Row: Panel 4 + Panel 5 -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">

        <!-- Panel 4: Adversarial Pressure Map -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4 lg:col-span-1">
            <h2 class="font-semibold text-gray-200 mb-3">Adversarial Pressure
                <span class="text-xs text-gray-500 ml-2">last 10 min</span></h2>
            <canvas id="pressureChart" height="180"></canvas>
        </div>

        <!-- Panel 5: Active Devices -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4 lg:col-span-2">
            <h2 class="font-semibold text-gray-200 mb-3">Active Devices</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-xs">
                    <thead>
                        <tr class="text-gray-500 text-left">
                            <th class="pb-2">Device</th>
                            <th class="pb-2">Counter</th>
                            <th class="pb-2">Battery</th>
                            <th class="pb-2">Records</th>
                            <th class="pb-2">Verified</th>
                            <th class="pb-2">Last Seen</th>
                            <th class="pb-2">Profile</th>
                        </tr>
                    </thead>
                    <tbody>
                        <template x-for="d in devices" :key="d.device_id">
                            <tr class="border-t border-gray-800">
                                <td class="py-1.5 pr-2">
                                    <a :href="'/player/' + d.device_id" class="font-mono text-blue-400 hover:underline"
                                       x-text="d.device_id.slice(0,12) + '...'"></a>
                                </td>
                                <td class="py-1.5 pr-2" x-text="d.last_counter"></td>
                                <td class="py-1.5 pr-2">
                                    <span :class="d.last_battery < 20 ? 'text-red-400' : 'text-green-400'"
                                          x-text="d.last_battery + '%'"></span>
                                </td>
                                <td class="py-1.5 pr-2" x-text="d.records_total"></td>
                                <td class="py-1.5 pr-2 text-emerald-400" x-text="d.records_verified"></td>
                                <td class="py-1.5 pr-2 text-gray-500" x-text="timeAgo(d.last_seen)"></td>
                                <td class="py-1.5">
                                    <span class="px-2 py-0.5 rounded text-xs bg-blue-900 text-blue-300">DualShock</span>
                                </td>
                            </tr>
                        </template>
                        <tr x-show="devices.length === 0">
                            <td colspan="7" class="py-4 text-center text-gray-600">No devices connected</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
const INF_COLOR = {
    0x20: '#10b981', 0x21: '#34d399',  // NOMINAL/SKILLED: emerald
    0x2B: '#f59e0b', 0x30: '#f59e0b',  // Advisory: amber
    0x28: '#ef4444', 0x29: '#ef4444', 0x2A: '#ef4444', // Hard cheat: crimson
};
const INF_NAMES = {
    32:'NOMINAL', 33:'SKILLED', 40:'DRIVER_INJECT', 41:'WALLHACK',
    42:'AIMBOT', 43:'TEMPORAL_ANOMALY', 48:'BIOMETRIC_ANOMALY'
};

function hexColor(inf) {
    return INF_COLOR[inf] || '#475569';
}

function operator() {
    return {
        stats: {}, devices: [], attestation: null,
        wsConnected: false, recPerMin: 0,
        layers: [],
        _recTimes: [],
        _ribbonLinks: [],
        _waveData: { l2:[], l3:[], l4:[], l5:[] },
        _waveChart: null, _pressureChart: null,

        async init() {
            this.initCharts();
            this.connectWS();
            await this.refresh();
            setInterval(() => this.refresh(), 5000);
            setInterval(() => this.refreshPressure(), 15000);
            setInterval(() => this.refreshAttestation(), 60000);
            this.refreshAttestation();
        },

        initCharts() {
            // Human Signal Waveform
            const wCtx = document.getElementById('waveChart').getContext('2d');
            this._waveChart = new Chart(wCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label:'L2 HID-XInput', data:[], borderColor:'#38bdf8', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                        { label:'L3 Behavioral', data:[], borderColor:'#a78bfa', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                        { label:'L4 Biometric dist', data:[], borderColor:'#f59e0b', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                        { label:'L5 Temporal CV×100', data:[], borderColor:'#10b981', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                    ]
                },
                options: {
                    responsive:true, maintainAspectRatio:false, animation:false,
                    scales: {
                        x:{ display:false },
                        y:{ grid:{ color:'#1f2937' }, ticks:{ color:'#6b7280', font:{size:10} },
                            min:0, max:260 }
                    },
                    plugins: {
                        legend:{ labels:{ color:'#9ca3af', boxWidth:12, font:{size:10} } },
                        annotation: {
                            annotations: {
                                l4thresh:{ type:'line', yMin:3, yMax:3, borderColor:'#ef444455', borderWidth:1, borderDash:[4,4] },
                            }
                        }
                    }
                }
            });

            // Adversarial Pressure Map
            const pCtx = document.getElementById('pressureChart').getContext('2d');
            this._pressureChart = new Chart(pCtx, {
                type: 'bar',
                data: {
                    labels: [],
                    datasets: [
                        { label:'Hard Cheat (L2/L3)', data:[], backgroundColor:'#ef4444' },
                        { label:'Temporal (L5)',       data:[], backgroundColor:'#f59e0b' },
                        { label:'Biometric (L4)',      data:[], backgroundColor:'#0d9488' },
                    ]
                },
                options: {
                    responsive:true, maintainAspectRatio:false, animation:false,
                    plugins:{ legend:{ labels:{ color:'#9ca3af', boxWidth:10, font:{size:9} } } },
                    scales: {
                        x:{ stacked:true, grid:{color:'#1f2937'}, ticks:{color:'#6b7280',font:{size:9}} },
                        y:{ stacked:true, grid:{color:'#1f2937'}, ticks:{color:'#6b7280',font:{size:9}}, min:0 }
                    }
                }
            });
        },

        connectWS() {
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const ws = new WebSocket(`${proto}://${location.host}/ws/records`);
            ws.onopen  = () => { this.wsConnected = true; };
            ws.onclose = () => {
                this.wsConnected = false;
                setTimeout(() => this.connectWS(), 3000);
            };
            ws.onmessage = (e) => {
                try { this.onRecord(JSON.parse(e.data)); } catch(err) {}
            };
            // Keep-alive ping every 20s
            setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
        },

        onRecord(r) {
            // Track records/min
            const now = Date.now();
            this._recTimes.push(now);
            this._recTimes = this._recTimes.filter(t => now - t < 60000);
            this.recPerMin = this._recTimes.length;

            // Chain Ribbon
            this.addRibbonLink(r);

            // Waveform datasets (rolling 60)
            const push = (arr, v) => { arr.push(v); if(arr.length > 60) arr.shift(); };
            const inf = r.inference;
            push(this._waveData.l2, (inf === 0x28) ? r.confidence : 0);
            push(this._waveData.l3, (inf === 0x29 || inf === 0x2A) ? r.confidence : 0);
            push(this._waveData.l4, r.pitl_l4_distance != null ? Math.min(r.pitl_l4_distance, 6.0) * 40 : 0);
            push(this._waveData.l5, r.pitl_l5_cv != null ? Math.min(r.pitl_l5_cv * 100, 260) : 0);

            const labels = Array.from({length: this._waveData.l2.length}, (_, i) => i);
            this._waveChart.data.labels = labels;
            this._waveChart.data.datasets[0].data = [...this._waveData.l2];
            this._waveChart.data.datasets[1].data = [...this._waveData.l3];
            this._waveChart.data.datasets[2].data = [...this._waveData.l4];
            this._waveChart.data.datasets[3].data = [...this._waveData.l5];
            this._waveChart.update('none');
        },

        addRibbonLink(r) {
            const ribbon = document.getElementById('chain-ribbon');
            const inf = r.inference;
            const color = hexColor(inf);
            const height = Math.round(16 + (r.confidence / 255) * 44);

            const div = document.createElement('div');
            div.className = 'chain-link';
            div.style.background = color;
            div.style.height = height + 'px';
            div.title = `${INF_NAMES[inf] || '0x' + inf.toString(16)} | conf=${r.confidence} | ${r.record_hash}...`;

            div.addEventListener('mouseenter', (e) => {
                const tip = document.getElementById('tip');
                tip.innerText = div.title;
                tip.style.display = 'block';
                tip.style.left = (e.clientX + 12) + 'px';
                tip.style.top  = (e.clientY - 28) + 'px';
            });
            div.addEventListener('mouseleave', () => {
                document.getElementById('tip').style.display = 'none';
            });

            ribbon.appendChild(div);
            this._ribbonLinks.push(div);
            if (this._ribbonLinks.length > 200) {
                const old = this._ribbonLinks.shift();
                if (old.parentNode) old.parentNode.removeChild(old);
            }
            ribbon.scrollLeft = ribbon.scrollWidth;
        },

        async refresh() {
            try {
                const [s, d, r] = await Promise.all([
                    fetch('/api/v1/stats').then(r => r.json()),
                    fetch('/api/v1/devices').then(r => r.json()),
                    fetch('/api/v1/records/recent?limit=80').then(r => r.json()),
                ]);
                this.stats = s;
                this.devices = d;
                // Bootstrap ribbon from recent records (only on first load)
                if (this._ribbonLinks.length === 0) {
                    [...r].reverse().forEach(rec => {
                        this.addRibbonLink({
                            inference: rec.inference,
                            confidence: rec.confidence,
                            record_hash: rec.record_hash,
                            pitl_l4_distance: rec.pitl_l4_distance,
                            pitl_l5_cv: rec.pitl_l5_cv,
                        });
                    });
                }
            } catch(e) { console.error('Refresh failed:', e); }
        },

        async refreshPressure() {
            try {
                const data = await fetch('/dash/api/v1/pitl/timeline?minutes=10').then(r => r.json());
                const buckets = {};
                const HARD = new Set([0x28, 0x29, 0x2A]);
                data.forEach(row => {
                    const label = new Date(row.bucket * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
                    if (!buckets[label]) buckets[label] = {hard:0, temporal:0, biometric:0};
                    if (HARD.has(row.inference)) buckets[label].hard += row.cnt;
                    else if (row.inference === 0x2B) buckets[label].temporal += row.cnt;
                    else if (row.inference === 0x30) buckets[label].biometric += row.cnt;
                });
                const labels = Object.keys(buckets);
                this._pressureChart.data.labels = labels;
                this._pressureChart.data.datasets[0].data = labels.map(l => buckets[l].hard);
                this._pressureChart.data.datasets[1].data = labels.map(l => buckets[l].temporal);
                this._pressureChart.data.datasets[2].data = labels.map(l => buckets[l].biometric);
                this._pressureChart.update('none');
            } catch(e) {}
        },

        async refreshAttestation() {
            try {
                const att = await fetch('/dash/api/v1/sdk/attestation').then(r => r.json());
                this.attestation = att;
                const scores = att.pitl_scores || {};
                const active = att.layers_active || {};
                this.layers = [
                    { id:'L2_hid_xinput', active: active.L2_hid_xinput||false, score: scores.L2_hid_xinput||0 },
                    { id:'L3_behavioral', active: active.L3_behavioral||false, score: scores.L3_behavioral||0 },
                    { id:'L4_biometric',  active: active.L4_biometric||false,  score: scores.L4_biometric||0  },
                    { id:'L5_temporal',   active: active.L5_temporal||false,   score: scores.L5_temporal||0   },
                ];
            } catch(e) {}
        },

        timeAgo(ts) {
            const s = Math.floor(Date.now() / 1000 - ts);
            if (s < 60) return s + 's';
            if (s < 3600) return Math.floor(s/60) + 'm';
            if (s < 86400) return Math.floor(s/3600) + 'h';
            return Math.floor(s/86400) + 'd';
        }
    };
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Player Dashboard — Trust Ledger + Identity Glyph + Chain Ribbon + Credential
# ---------------------------------------------------------------------------

PLAYER_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PoHG Pulse — Player</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <style>
        [x-cloak]{display:none!important}
        #player-ribbon{display:flex;align-items:flex-end;gap:2px;overflow:hidden}
        .chain-link{flex-shrink:0;width:18px;border-radius:2px}
        #qr-container img,.qrcode img{display:block}
    </style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen" x-data="player('__DEVICE_ID__')" x-init="init()">

<!-- Header -->
<div class="border-b border-gray-800 px-6 py-3 flex items-center justify-between bg-gray-900">
    <div class="flex items-center gap-3">
        <a href="/" class="text-gray-500 hover:text-gray-300 text-sm">← Operator</a>
        <span class="text-gray-700">|</span>
        <span class="font-bold text-blue-400">PoHG Pulse</span>
        <span class="text-gray-500 text-sm">Player Profile</span>
    </div>
    <div class="flex items-center gap-3">
        <span class="font-mono text-xs text-gray-500" x-text="deviceId.slice(0,24) + '...'"></span>
        <span x-show="rank" x-cloak
              class="px-2 py-0.5 rounded-full text-xs font-bold bg-yellow-900 text-yellow-300"
              x-text="rank ? '#' + rank.rank + ' of ' + rank.total : ''"></span>
    </div>
</div>

<div class="max-w-5xl mx-auto px-4 py-6 space-y-4">

    <!-- Panel A: Trust Ledger -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6">
        <div class="flex items-start justify-between mb-4">
            <div>
                <div class="text-xs text-gray-500 uppercase tracking-wider mb-1">PHG Trust Score</div>
                <div class="text-5xl font-bold text-emerald-400" x-text="profile ? profile.phg_score.toLocaleString() : '—'"></div>
                <div class="text-gray-500 text-sm mt-1">Proof of Human Gaming — cryptographic accumulation</div>
            </div>
            <div class="text-right">
                <div class="px-3 py-1 rounded-full text-sm font-medium bg-blue-900 text-blue-300">
                    DualShock Edge
                </div>
                <div class="text-xs text-gray-500 mt-1">PHCI CERTIFIED</div>
            </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4" x-show="profile" x-cloak>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Verified Records</div>
                <div class="text-xl font-bold" x-text="(profile.nominal_records || 0).toLocaleString()"></div>
            </div>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Confidence Mean</div>
                <div class="text-xl font-bold" x-text="(profile.confidence_mean || 0) + ' / 255'"></div>
            </div>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Total Records</div>
                <div class="text-xl font-bold" x-text="(profile.total_records || 0).toLocaleString()"></div>
            </div>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Chain Active Since</div>
                <div class="text-sm font-bold" x-text="profile.first_record_at ? new Date(profile.first_record_at * 1000).toLocaleDateString() : '—'"></div>
            </div>
        </div>
        <div x-show="!profile" class="text-gray-600 text-sm mt-2">No data found for this device.</div>
    </div>

    <!-- Panel B: Identity Glyph (Biometric Fingerprint) -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6" x-show="profile && profile.fingerprint_available" x-cloak>
        <h2 class="font-semibold text-gray-200 mb-1">Identity Glyph
            <span class="text-xs text-gray-500 ml-2">Biometric Fingerprint</span></h2>
        <p class="text-xs text-gray-600 mb-4">
            The shape of this radar is your unique kinematic signature — averaged over recent authenticated records.
            It stabilizes as the EWC biometric model converges.
        </p>
        <div class="max-w-xs mx-auto">
            <canvas id="glyphChart"></canvas>
        </div>
    </div>
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6"
         x-show="profile && !profile.fingerprint_available" x-cloak>
        <h2 class="font-semibold text-gray-200 mb-1">Identity Glyph</h2>
        <p class="text-gray-600 text-sm">
            Biometric fingerprint unavailable — requires at least 5 authenticated sessions
            with L4 PITL active (DualShock Edge CERTIFIED tier).
        </p>
    </div>

    <!-- Panel C: Chain Ribbon (player-specific) -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h2 class="font-semibold text-gray-200 mb-3">Chain Ribbon
            <span class="text-xs text-gray-500 ml-2">this device · newest →</span></h2>
        <div id="player-ribbon" class="h-16 bg-gray-950 rounded p-1"></div>
    </div>

    <!-- Panel D: PHG Credential + QR Code -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6">
        <div class="flex items-start justify-between mb-4">
            <div>
                <h2 class="font-semibold text-gray-200 mb-1">PHG Credential
                    <span class="text-xs text-gray-500 ml-2">Soulbound On-Chain Identity</span></h2>
                <p class="text-xs text-gray-600">ERC-5192 soulbound — non-transferable, permanently locked.</p>
            </div>
            <div x-show="credential" x-cloak
                 class="px-3 py-1 rounded-full text-sm font-bold bg-emerald-900 text-emerald-300">
                MINTED ✓
            </div>
            <div x-show="!credential" x-cloak
                 class="px-3 py-1 rounded-full text-sm font-medium bg-gray-800 text-gray-400">
                NOT MINTED
            </div>
        </div>

        <!-- Minted: show credential details + QR -->
        <div x-show="credential" x-cloak class="grid grid-cols-1 md:grid-cols-2 gap-6 mt-2">
            <div class="space-y-3">
                <div class="bg-gray-800 rounded p-3">
                    <div class="text-xs text-gray-500">Credential ID</div>
                    <div class="text-xl font-bold text-emerald-400" x-text="'#' + (credential && credential.credential_id)"></div>
                </div>
                <div class="bg-gray-800 rounded p-3">
                    <div class="text-xs text-gray-500">Minted</div>
                    <div class="text-sm font-bold" x-text="credential ? new Date(credential.minted_at * 1000).toLocaleString() : '—'"></div>
                </div>
                <div class="bg-gray-800 rounded p-3">
                    <div class="text-xs text-gray-500">Tx Hash</div>
                    <div class="font-mono text-xs text-gray-400 truncate" x-text="credential ? (credential.tx_hash || 'local-only') : '—'"></div>
                </div>
                <button @click="copyProofUrl()"
                    class="w-full mt-2 px-4 py-2 bg-blue-700 hover:bg-blue-600 text-white text-sm rounded font-medium transition-colors">
                    Share Proof URL
                </button>
                <div x-show="copied" x-cloak class="text-xs text-emerald-400 text-center">Copied to clipboard!</div>
            </div>
            <div class="flex flex-col items-center gap-3">
                <div class="text-xs text-gray-500 uppercase tracking-wider">Scan to verify</div>
                <div id="qr-container" class="bg-gray-800 rounded-lg p-3 inline-block"></div>
                <div class="text-xs text-gray-600 text-center">Links to your shareable proof page</div>
            </div>
        </div>

        <!-- Not minted: onboarding wizard -->
        <div x-show="!credential" x-cloak class="mt-4 space-y-3">
            <p class="text-sm text-gray-400">Complete these steps to mint your soulbound credential:</p>
            <div class="space-y-2">
                <div class="flex items-center gap-3 p-3 rounded"
                     :class="(profile && profile.total_records > 0) ? 'bg-emerald-900/30 border border-emerald-800' : 'bg-gray-800'">
                    <span class="text-lg" x-text="(profile && profile.total_records > 0) ? '✓' : '○'"></span>
                    <div>
                        <div class="text-sm font-medium">Step 1 — Controller Connected</div>
                        <div class="text-xs text-gray-500">Play at least one authenticated session</div>
                    </div>
                </div>
                <div class="flex items-center gap-3 p-3 rounded"
                     :class="(profile && profile.phg_score > 0) ? 'bg-emerald-900/30 border border-emerald-800' : 'bg-gray-800'">
                    <span class="text-lg" x-text="(profile && profile.phg_score > 0) ? '✓' : '○'"></span>
                    <div>
                        <div class="text-sm font-medium">Step 2 — PHG Score Accumulating</div>
                        <div class="text-xs text-gray-500">Bridge accumulates confirmed PHG checkpoints on-chain</div>
                    </div>
                </div>
                <div class="flex items-center gap-3 p-3 rounded bg-gray-800">
                    <span class="text-lg">○</span>
                    <div>
                        <div class="text-sm font-medium">Step 3 — Mint Credential</div>
                        <div class="text-xs text-gray-500">Bridge mints automatically when PITL ZK proof is generated at session end</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

</div>

<script>
const INF_COLOR = {
    32: '#10b981', 33: '#34d399',
    43: '#f59e0b', 48: '#f59e0b',
    40: '#ef4444', 41: '#ef4444', 42: '#ef4444',
};

function player(deviceId) {
    return {
        deviceId,
        profile: null,
        credential: null,
        rank: null,
        copied: false,
        _glyphChart: null,
        _qrGenerated: false,

        async init() {
            await this.loadProfile();
            await this.loadRibbon();
            this.fetchCredential();
            this.fetchRank();
        },

        async loadProfile() {
            try {
                const p = await fetch(`/dash/api/v1/player/${this.deviceId}/profile`).then(r => {
                    if (!r.ok) throw new Error(r.status);
                    return r.json();
                });
                this.profile = p;
                if (p.fingerprint_available && p.biometric_fingerprint) {
                    this.$nextTick(() => this.renderGlyph(p.biometric_fingerprint));
                }
            } catch(e) {
                this.profile = null;
            }
        },

        async fetchCredential() {
            try {
                const r = await fetch(`/dash/api/v1/player/${this.deviceId}/credential`);
                this.credential = r.ok ? await r.json() : null;
                if (this.credential && !this._qrGenerated) {
                    this.$nextTick(() => this.renderQR());
                }
            } catch(e) { this.credential = null; }
        },

        async fetchRank() {
            try {
                const r = await fetch('/dash/api/v1/leaderboard?limit=10000');
                if (!r.ok) return;
                const board = await r.json();
                const idx = board.findIndex(e => e.device_id === this.deviceId);
                this.rank = idx >= 0 ? { rank: idx + 1, total: board.length } : null;
            } catch(e) { this.rank = null; }
        },

        renderQR() {
            const container = document.getElementById('qr-container');
            if (!container || this._qrGenerated) return;
            this._qrGenerated = true;
            const proofUrl = window.location.origin + '/proof/' + this.deviceId;
            try {
                new QRCode(container, {
                    text: proofUrl,
                    width: 180, height: 180,
                    colorDark: '#10b981',
                    colorLight: '#1f2937',
                    correctLevel: QRCode.CorrectLevel.M,
                });
            } catch(e) {
                container.innerHTML = '<div class="text-xs text-gray-500 p-4">QR unavailable</div>';
            }
        },

        copyProofUrl() {
            const proofUrl = window.location.origin + '/proof/' + this.deviceId;
            if (navigator.clipboard) {
                navigator.clipboard.writeText(proofUrl).then(() => {
                    this.copied = true;
                    setTimeout(() => { this.copied = false; }, 2000);
                });
            }
        },

        renderGlyph(fp) {
            const labels = [
                'Trigger L2 vel', 'Trigger R2 vel', 'Micro-tremor',
                'Grip asymmetry', 'Stick corr lag1', 'Stick corr lag5'
            ];
            const keys = [
                'trigger_onset_velocity_l2', 'trigger_onset_velocity_r2',
                'micro_tremor_variance', 'grip_asymmetry',
                'stick_autocorr_lag1', 'stick_autocorr_lag5'
            ];
            const vals = keys.map(k => Math.min(Math.abs(fp[k] || 0) * 10, 1.0));

            const ctx = document.getElementById('glyphChart').getContext('2d');
            this._glyphChart = new Chart(ctx, {
                type: 'radar',
                data: {
                    labels,
                    datasets: [{
                        label: 'Biometric fingerprint',
                        data: vals,
                        borderColor: '#38bdf8',
                        backgroundColor: '#38bdf820',
                        pointBackgroundColor: '#38bdf8',
                        borderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    scales: {
                        r: {
                            min: 0, max: 1,
                            grid:     { color: '#374151' },
                            angleLines:{ color: '#374151' },
                            ticks:    { display: false },
                            pointLabels:{ color: '#9ca3af', font: { size: 10 } }
                        }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        },

        async loadRibbon() {
            try {
                const records = await fetch(`/api/v1/records/recent?limit=100&device_id=${this.deviceId}`)
                    .then(r => r.json());
                const ribbon = document.getElementById('player-ribbon');
                [...records].reverse().forEach(rec => {
                    const color = INF_COLOR[rec.inference] || '#475569';
                    const height = Math.round(16 + (rec.confidence / 255) * 44);
                    const div = document.createElement('div');
                    div.className = 'chain-link';
                    div.style.background = color;
                    div.style.height = height + 'px';
                    div.title = `0x${rec.inference.toString(16)} conf=${rec.confidence}`;
                    ribbon.appendChild(div);
                });
            } catch(e) {}
        }
    };
}
</script>
</body>
</html>"""
