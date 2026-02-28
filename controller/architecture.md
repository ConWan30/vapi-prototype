# VAPI DualShock — Technical Architecture Document

**Version:** 1.0.0 | **Date:** 2026-02-16

---

## Slide 1: System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     VAPI DualShock Ecosystem                         │
│                                                                      │
│  ┌─────────────┐     BLE 5.0     ┌──────────────┐                  │
│  │  DualShock   │◄──────────────►│  Companion    │                  │
│  │  Controller  │    228B PoAC    │  App (PC/     │                  │
│  │  + ESP32-S3  │    at 2-10 Hz   │  Mobile)      │                  │
│  │              │                 │               │                  │
│  │  VAPI Agent  │                 │ - Dashboard   │                  │
│  │  TinyML      │                 │ - Dev Tools   │                  │
│  │  PoAC Engine │                 │ - Bounties    │                  │
│  └──────────────┘                 └───────┬───────┘                  │
│                                           │                          │
│                                    MQTT / HTTP                       │
│                                           │                          │
│                                   ┌───────▼───────┐                  │
│                                   │ VAPI Bridge    │                  │
│                                   │ Service        │                  │
│                                   └───────┬────────┘                  │
│                                           │                          │
│                                      Web3 / RPC                      │
│                                           │                          │
│                                   ┌───────▼────────┐                 │
│                                   │  IoTeX L1      │                 │
│                                   │                │                 │
│                                   │ DeviceRegistry │                 │
│                                   │ PoACVerifier   │                 │
│                                   │ BountyMarket   │                 │
│                                   └────────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

**Data Flow**: Controller → (BLE) → Companion App → (MQTT) → Bridge → (RPC) → IoTeX

---

## Slide 2: ESP32-S3 Hardware Integration

```
                    DualSense Edge Controller PCB
    ┌──────────────────────────────────────────────────┐
    │                                                   │
    │   ┌─────────┐         ┌────────────────────┐     │
    │   │ Stock   │   SPI   │   ESP32-S3 Module  │     │
    │   │ Sony    │◄───────►│                    │     │
    │   │ MCU     │  MISO   │  ┌──────────────┐ │     │
    │   │         │  MOSI   │  │  VAPI Agent   │ │     │
    │   │ ADC     │  SCLK   │  │  Stack        │ │     │
    │   │ Button  │  CS     │  ├──────────────┤ │     │
    │   │ Decode  │         │  │  TinyML       │ │     │
    │   │ IMU     │  GPIO   │  │  Anti-Cheat   │ │     │
    │   │ Touch   │◄───────►│  ├──────────────┤ │     │
    │   │ Haptic  │  IRQ    │  │  PoAC Engine  │ │     │
    │   │ LED     │         │  │  (mbedTLS)    │ │     │
    │   └─────────┘         │  ├──────────────┤ │     │
    │       │               │  │  BLE 5.0     │ │     │
    │       │  USB/BT       │  │  (NimBLE)    │ │     │
    │       │  to Host      │  └──────────────┘ │     │
    │       │  (passthru)   └────────────────────┘     │
    │       │                        │  BLE antenna    │
    │       ▼                        ▼                 │
    └──────────────────────────────────────────────────┘

    Memory Map (ESP32-S3):
    ┌────────────────────┬──────────┐
    │ Component          │ Size     │
    ├────────────────────┼──────────┤
    │ VAPI Agent Code    │ ~120 KB  │
    │ TinyML Model       │ ~60 KB   │
    │ PoAC Engine        │ ~40 KB   │
    │ BLE Stack (NimBLE) │ ~80 KB   │
    │ FreeRTOS + HAL     │ ~60 KB   │
    │ Input Ring Buffer  │ ~50 KB   │
    │ PoAC Record Queue  │ ~8 KB    │
    │ World Model        │ ~12 KB   │
    │ Crypto Workspace   │ ~8 KB    │
    │ Stack (4 threads)  │ ~32 KB   │
    ├────────────────────┼──────────┤
    │ TOTAL SRAM         │ ~470 KB  │
    │ Available SRAM     │ 512 KB   │
    │ Headroom           │ ~42 KB   │
    ├────────────────────┼──────────┤
    │ PSRAM (overflow)   │ 8 MB     │
    │ Flash (firmware)   │ ~400 KB  │
    │ Flash (available)  │ 16 MB    │
    └────────────────────┴──────────┘
```

---

## Slide 3: Three-Layer Agent Architecture (Gaming Adaptation)

```
    Priority ◄──── Higher                          Lower ────►

    ┌─────────────────────────────────────────────────────────┐
    │                                                          │
    │  Layer 1: GAMING REFLEXIVE          Period: 1ms poll     │
    │  ────────────────────────           PoAC: 2-10 Hz       │
    │  FreeRTOS Priority: 5               Stack: 8 KB          │
    │  Thread: vapi_L1_gaming                                  │
    │                                                          │
    │  Pipeline:                                               │
    │  ┌────────┐ ┌─────────┐ ┌──────┐ ┌──────┐ ┌──────────┐│
    │  │ Poll   │►│ Ring    │►│Feat. │►│TinyML│►│  PoAC    ││
    │  │ Inputs │ │ Buffer  │ │Extr. │ │Infer │ │ Generate ││
    │  │ (1kHz) │ │ (256f)  │ │(20Hz)│ │(10Hz)│ │ (2-10Hz) ││
    │  └────────┘ └─────────┘ └──────┘ └──────┘ └──────────┘│
    │                                                          │
    ├──────────────────────────────────────────────────────────┤
    │                                                          │
    │  Layer 2: ANTI-CHEAT DELIBERATIVE   Period: 5 seconds    │
    │  ────────────────────────────────                        │
    │  FreeRTOS Priority: 8               Stack: 6 KB          │
    │  Thread: vapi_L2_anticheat                               │
    │                                                          │
    │  Functions:                                              │
    │  ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │
    │  │ Skill Profile  │ │ Trend        │ │ Bounty        │  │
    │  │ Update (EMA)   │ │ Analysis     │ │ Evaluation    │  │
    │  │                │ │ (cheat over  │ │ (knapsack)    │  │
    │  │ reaction_ms    │ │  time?)      │ │               │  │
    │  │ precision      │ │              │ │ accept/       │  │
    │  │ consistency    │ │ Pattern      │ │ decline/      │  │
    │  │ imu_corr       │ │ memory       │ │ preempt       │  │
    │  └────────────────┘ └──────────────┘ └───────────────┘  │
    │                                                          │
    ├──────────────────────────────────────────────────────────┤
    │                                                          │
    │  Layer 3: ECONOMIC STRATEGIC        Period: 60 seconds   │
    │  ────────────────────────                                │
    │  FreeRTOS Priority: 11              Stack: 6 KB          │
    │  Thread: vapi_L3_economic                                │
    │                                                          │
    │  Functions:                                              │
    │  ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │
    │  │ BLE Sync       │ │ Chain Drain  │ │ Autonomy      │  │
    │  │ (to companion  │ │ (queue PoAC  │ │ Guard         │  │
    │  │  app)          │ │  to app)     │ │ (reject bad   │  │
    │  │                │ │              │ │  commands)    │  │
    │  │ World model    │ │ Up to 32     │ │               │  │
    │  │ + stats        │ │ records/     │ │ Log rejected  │  │
    │  │                │ │ batch        │ │ suggestions   │  │
    │  └────────────────┘ └──────────────┘ └───────────────┘  │
    │                                                          │
    └──────────────────────────────────────────────────────────┘
```

---

## Slide 4: Anti-Cheat PoAC Flow

```
    TIME ──────────────────────────────────────────────────►

    Input Poll (1 kHz):
    ╔═══╗╔═══╗╔═══╗╔═══╗╔═══╗╔═══╗╔═══╗╔═══╗╔═══╗╔═══╗
    ║ 1 ║║ 2 ║║ 3 ║║...║║100║║101║║...║║200║║201║║...║
    ╚═══╝╚═══╝╚═══╝╚═══╝╚═══╝╚═══╝╚═══╝╚═══╝╚═══╝╚═══╝
    ──────────────────┬───────────────────┬───────────────
                      │                   │
    Feature Extract (20 Hz):              │
                 ┌────▼────┐         ┌────▼────┐
                 │ Window  │         │ Window  │
                 │ 50 frms │         │ 50 frms │
                 │         │         │         │
                 │ Extract:│         │ Extract:│
                 │ -react  │         │ -react  │
                 │ -σ(dt)  │         │ -σ(dt)  │
                 │ -v(stick│         │ -v(stick│
                 │ -corr   │         │ -corr   │
                 └────┬────┘         └────┬────┘
                      │                   │
    TinyML Infer (10 Hz):                 │
                 ┌────▼────────────┐      │
                 │  100-frame      │      │
                 │  window         ├──────┘
                 │                 │
                 │  CNN Classify:  │
                 │  [nominal: 0.92│
                 │   skilled: 0.05│
                 │   cheat:   0.03│
                 │  ]             │
                 │  latency: 4ms  │
                 └────┬───────────┘
                      │
    PoAC Generate (2 Hz normal / 10 Hz tournament):
                 ┌────▼──────────────────────────────┐
                 │  PoAC Record #N                    │
                 │                                    │
                 │  prev_hash ← H(Record #N-1)       │
                 │  sensor    ← H(50-byte snapshot)   │
                 │  model     ← H(anticheat_v1.tflite)│
                 │  world     ← H(player_profile)     │
                 │  inference ← PLAY_NOMINAL (0x20)   │
                 │  action    ← GAME_SESSION (0x10)   │
                 │  confidence← 235/255 (92%)         │
                 │  battery   ← 78%                   │
                 │  counter   ← N                     │
                 │  timestamp ← 1739721600000         │
                 │                                    │
                 │  ══════════════════════════════     │
                 │  signature ← ECDSA-P256(body)      │
                 │  ══════════════════════════════     │
                 └────┬──────────────────────────────┘
                      │
                      ▼  BLE Notify (0xVA01)
                 ┌────────────────┐
                 │ Companion App  │──► Bridge ──► IoTeX
                 └────────────────┘
```

---

## Slide 5: Anti-Cheat TinyML Model Architecture

```
    Input: 100 frames × 30 features = 3000 values (INT8 quantized)

    Features per frame:
    ┌──────────────────────────────────────────────────┐
    │ stick_lx, stick_ly, stick_rx, stick_ry          │  4
    │ stick_l_velocity, stick_r_velocity              │  2
    │ stick_l_accel, stick_r_accel                    │  2
    │ trigger_l2, trigger_r2                          │  2
    │ button_state (18 bits → 3 bytes)                │  3
    │ inter_press_interval (ms)                       │  1
    │ inter_press_variance                            │  1
    │ gyro_x, gyro_y, gyro_z                         │  3
    │ accel_x, accel_y, accel_z                       │  3
    │ accel_magnitude                                 │  1
    │ gyro_magnitude                                  │  1
    │ imu_stick_correlation                           │  1
    │ touch_x, touch_y, touch_active                  │  3
    │ frame_dt_us                                     │  1
    │ button_press_count                              │  1
    │ stick_direction_change_count                    │  1
    │                                          TOTAL: │ 30
    └──────────────────────────────────────────────────┘

    Model Architecture:
    ┌──────────────────────────────────────────────────┐
    │  Conv1D(filters=16, kernel=5, stride=2)          │
    │  BatchNorm → ReLU                                │
    │  Conv1D(filters=32, kernel=3, stride=2)          │
    │  BatchNorm → ReLU                                │
    │  Conv1D(filters=32, kernel=3, stride=2)          │
    │  BatchNorm → ReLU                                │
    │  GlobalAveragePooling1D                          │
    │  Dense(64) → ReLU → Dropout(0.3)                │
    │  Dense(8) → Softmax                              │
    └──────────────────────────────────────────────────┘

    Output: 8 classes
    ┌─────────────────────────┐
    │ [0] play_nominal   0.92 │  ← Normal human play
    │ [1] play_skilled   0.05 │  ← High-skill human
    │ [2] cheat_reaction 0.01 │  ← Impossible reaction
    │ [3] cheat_macro    0.00 │  ← Macro/turbo
    │ [4] cheat_aimbot   0.01 │  ← Aimbot pattern
    │ [5] cheat_recoil   0.00 │  ← Recoil script
    │ [6] cheat_imu_mis  0.01 │  ← No IMU correlation
    │ [7] cheat_inject   0.00 │  ← Fabricated input
    └─────────────────────────┘

    Model Size: ~55 KB (INT8 quantized)
    Inference: ~4 ms on ESP32-S3 @ 240 MHz
    RAM: ~22 KB inference arena
```

---

## Slide 6: Companion App Architecture

```
    ┌────────────────────────────────────────────────────────┐
    │                    Companion App                        │
    │                                                        │
    │  ┌──────────────────────────────────────────────────┐  │
    │  │                  UI Layer                         │  │
    │  │                                                   │  │
    │  │  ┌────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐ │  │
    │  │  │ Live   │ │ Chain    │ │ Bounty │ │ Dev     │ │  │
    │  │  │Session │ │Explorer  │ │Market  │ │Console  │ │  │
    │  │  │        │ │          │ │        │ │         │ │  │
    │  │  │-Status │ │-Search   │ │-Browse │ │-Flash   │ │  │
    │  │  │-Graphs │ │-Filter   │ │-Accept │ │-Train   │ │  │
    │  │  │-Alerts │ │-Verify   │ │-Track  │ │-Tune    │ │  │
    │  │  │-Score  │ │-Export   │ │-Claim  │ │-Debug   │ │  │
    │  │  └────┬───┘ └────┬─────┘ └───┬────┘ └────┬────┘ │  │
    │  └───────┼──────────┼───────────┼───────────┼───────┘  │
    │          │          │           │           │           │
    │  ┌───────▼──────────▼───────────▼───────────▼───────┐  │
    │  │              WebSocket Hub (real-time)             │  │
    │  └──────────────────────┬────────────────────────────┘  │
    │                         │                               │
    │  ┌──────────────────────▼────────────────────────────┐  │
    │  │              FastAPI Backend                       │  │
    │  │                                                    │  │
    │  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│  │
    │  │  │BLE Mgr   │  │PoAC      │  │Bridge Relay      ││  │
    │  │  │(bleak)   │  │Validator │  │(MQTT/HTTP)       ││  │
    │  │  │          │  │          │  │                   ││  │
    │  │  │pair()    │  │verify()  │  │relay_to_iotex()  ││  │
    │  │  │stream()  │  │chain()   │  │submit_evidence() ││  │
    │  │  │ota()     │  │export()  │  │check_bounties()  ││  │
    │  │  └──────────┘  └──────────┘  └──────────────────┘│  │
    │  │                                                    │  │
    │  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│  │
    │  │  │SQLite    │  │Firmware  │  │TinyML Trainer    ││  │
    │  │  │Store     │  │Flasher   │  │(Edge Impulse     ││  │
    │  │  │          │  │(esptool) │  │ API wrapper)     ││  │
    │  │  │records   │  │          │  │                   ││  │
    │  │  │devices   │  │flash()   │  │collect_data()    ││  │
    │  │  │sessions  │  │verify()  │  │train_model()     ││  │
    │  │  │bounties  │  │rollback()│  │deploy_model()    ││  │
    │  │  └──────────┘  └──────────┘  └──────────────────┘│  │
    │  └───────────────────────────────────────────────────┘  │
    └────────────────────────────────────────────────────────┘
```

---

## Slide 7: BLE Communication Protocol

```
    Controller (ESP32-S3)              Companion App (PC/Mobile)
    ═══════════════════                ═════════════════════════

    GATT Server                        GATT Client
    ┌─────────────────┐                ┌─────────────────┐
    │ Service: VAPI   │                │                  │
    │ UUID: 0xVAPI    │                │                  │
    │                 │   ◄─ SCAN ──   │  discover()      │
    │                 │   ── ADV ──►   │                  │
    │                 │   ◄─ CONN ──   │  connect()       │
    │                 │                │                  │
    │ Char: PoAC      │   ── NOTIFY►   │  on_poac()       │
    │   0xVA01        │   228B @ 2Hz   │  → validate      │
    │   (Notify)      │                │  → store         │
    │                 │                │  → display       │
    │ Char: Input     │   ── NOTIFY►   │  on_input()      │
    │   0xVA02        │   50B @ opt    │  → oscilloscope  │
    │   (Notify)      │                │                  │
    │ Char: Status    │   ── NOTIFY►   │  on_status()     │
    │   0xVA03        │   2B changes   │  → alert UI      │
    │   (Read/Notify) │                │                  │
    │ Char: State     │   ◄─ READ ──   │  get_state()     │
    │   0xVA04        │   ── RESP ──►  │                  │
    │   (Read)        │                │                  │
    │ Char: Command   │   ◄─ WRITE ─   │  send_cmd()      │
    │   0xVA05        │   bounty/cfg   │  → bounty inject │
    │   (Write)       │                │  → config update │
    │ Char: Config    │   ◄─ R/W ───   │  get/set_cfg()   │
    │   0xVA06        │                │                  │
    │   (Read/Write)  │                │                  │
    │ Char: OTA       │   ◄─ WRITE ─   │  flash_fw()      │
    │   0xVA07        │   ── INDIC ►   │  → progress      │
    │   (Write/Ind)   │                │                  │
    │ Char: WorldModel│   ◄─ READ ──   │  get_wm()        │
    │   0xVA08        │   ── RESP ──►  │  → skill display │
    │   (Read)        │                │                  │
    └─────────────────┘                └─────────────────┘

    Throughput Budget:
    ┌──────────────────────────────────┬──────────┐
    │ Channel                          │ Bytes/s  │
    ├──────────────────────────────────┼──────────┤
    │ PoAC Notify (2 Hz × 228 B)      │ 456      │
    │ Input Notify (opt, 10 Hz × 50B) │ 500      │
    │ Status Notify (on change)        │ ~10      │
    │ Config/Command (sporadic)        │ ~20      │
    ├──────────────────────────────────┼──────────┤
    │ Total (normal mode)              │ ~486     │
    │ Total (dev mode + input stream)  │ ~986     │
    │ BLE 5.0 2M PHY capacity         │ ~250,000 │
    │ Utilization                      │ <0.4%    │
    └──────────────────────────────────┴──────────┘
```

---

## Slide 8: Bounty Marketplace Integration

```
    ┌──────────────────────────────────────────────────────┐
    │                  Bounty Lifecycle                      │
    │                                                       │
    │   IoTeX Blockchain            Controller    App       │
    │   ═════════════               ══════════    ═══       │
    │                                                       │
    │   BountyMarket                                        │
    │   .postBounty() ─────────────────────────► Browse     │
    │        │                                    │         │
    │        │  Event: BountyPosted               │         │
    │        │                                    ▼         │
    │        │                              ┌──────────┐   │
    │        │              BLE cmd         │ Select   │   │
    │        │         ◄───────────────────│ Bounty   │   │
    │        │                              └──────────┘   │
    │        │                    │                         │
    │        │              ┌─────▼──────┐                  │
    │        │              │ L2 Evaluate│                  │
    │        │              │ - battery  │                  │
    │        │              │ - skill    │                  │
    │        │              │ - utility  │                  │
    │        │              └─────┬──────┘                  │
    │        │                    │                         │
    │        │              ┌─────▼──────┐                  │
    │   .acceptBounty() ◄──│ PoAC:      │──► Display      │
    │        │              │ BOUNTY_    │   "Accepted"    │
    │        │              │ ACCEPT     │                  │
    │        │              └─────┬──────┘                  │
    │        │                    │                         │
    │        │              ┌─────▼──────┐                  │
    │        │              │ L1 Gameplay│                  │
    │        │              │ PoAC with  │──► Live chain   │
    │        │              │ bounty_id  │   view          │
    │        │              │ (N records)│                  │
    │        │              └─────┬──────┘                  │
    │        │                    │                         │
    │   .submitEvidence() ◄───── │ ────────► Progress      │
    │   (per verified PoAC)      │           bar            │
    │        │                    │                         │
    │        │              ┌─────▼──────┐                  │
    │   .claimReward() ◄───│ min_samples│──► "Reward       │
    │        │              │ reached!   │    claimed!"    │
    │        │              └────────────┘                  │
    │        │                                              │
    │        ▼                                              │
    │   IOTX transferred                                   │
    │   to device owner                                    │
    │                                                       │
    └──────────────────────────────────────────────────────┘
```

---

## Slide 9: Anti-Cheat Detection Matrix

```
    ┌──────────────────────────────────────────────────────────────┐
    │              Anti-Cheat Feature Extraction                    │
    │                                                              │
    │  RAW INPUTS                FEATURES              DETECTION   │
    │  ══════════                ════════               ═════════  │
    │                                                              │
    │  Button timing ──►  σ(inter-press dt)  ──► MACRO            │
    │       │              │                     (σ < 1ms)        │
    │       │              ├── FFT periodicity ─► TURBO            │
    │       │              │                     (sharp spike)    │
    │       │              └── press rate ──────► RAPID-FIRE       │
    │       │                                    (>15 Hz sustain) │
    │       │                                                      │
    │  Stick position ──► velocity profile ────► AIMBOT            │
    │       │              │                     (ballistic snap)  │
    │       │              ├── jerk analysis ───► RECOIL SCRIPT    │
    │       │              │                     (perfect invert)  │
    │       │              └── deadzone usage ──► XIM/ADAPTER      │
    │       │                                    (no deadzone)    │
    │       │                                                      │
    │  IMU (gyro+acc) ──► noise floor ─────────► DESK MODE        │
    │       │              │                     (no micro-tremor) │
    │       │              └── cross-correlate                     │
    │       │                  with stick  ─────► IMU MISMATCH     │
    │       │                                    (move w/o motion) │
    │       │                                                      │
    │  Reaction time ──► distribution fit ─────► INHUMAN REACT    │
    │       │              │                     (<150ms sustain)  │
    │       │              └── consistency ─────► BOT              │
    │       │                                    (identical μ)    │
    │       │                                                      │
    │  Touch pad ──────► entropy measure ──────► AUTOMATED TOUCH  │
    │                                            (low entropy)    │
    │                                                              │
    └──────────────────────────────────────────────────────────────┘
```

---

## Slide 10: Tournament Mode Architecture

```
    ┌─────────────────────────────────────────────────────────┐
    │                  Tournament Match                        │
    │                                                          │
    │  Player A                              Player B          │
    │  ┌────────────┐                       ┌────────────┐    │
    │  │ Controller  │                       │ Controller  │    │
    │  │ + ESP32-S3  │                       │ + ESP32-S3  │    │
    │  │             │                       │             │    │
    │  │ PoAC Chain: │                       │ PoAC Chain: │    │
    │  │ A₁→A₂→...→Aₙ                      │ B₁→B₂→...→Bₙ   │
    │  │             │                       │             │    │
    │  │ bounty_id=  │                       │ bounty_id=  │    │
    │  │ MATCH_42    │                       │ MATCH_42    │    │
    │  └──────┬──────┘                       └──────┬──────┘    │
    │         │ BLE                                  │ BLE       │
    │  ┌──────▼──────┐                       ┌──────▼──────┐    │
    │  │ App A       │                       │ App B       │    │
    │  └──────┬──────┘                       └──────┬──────┘    │
    │         │                                      │           │
    │         └──────────────┬────────────────────────┘           │
    │                        │ MQTT/HTTP                         │
    │                 ┌──────▼──────┐                            │
    │                 │   Bridge     │                            │
    │                 └──────┬──────┘                            │
    │                        │                                   │
    │                 ┌──────▼──────────────────────────┐        │
    │                 │  IoTeX: BountyMarket             │        │
    │                 │                                  │        │
    │                 │  aggregateSwarmReport(MATCH_42): │        │
    │                 │    deviceCount: 2                │        │
    │                 │    totalSamples: 2400            │        │
    │                 │    confidenceScore: 9500         │        │
    │                 │    consensusInference: NOMINAL   │        │
    │                 │                                  │        │
    │                 │  emit PhysicalOracleReport(      │        │
    │                 │    "Match MATCH_42: FAIR PLAY    │        │
    │                 │     verified, confidence 95%"    │        │
    │                 │  )                               │        │
    │                 └─────────────────────────────────┘        │
    │                                                            │
    └────────────────────────────────────────────────────────────┘
```

---

## Slide 11: Security Architecture

```
    ┌──────────────────────────────────────────────────────────┐
    │              Trust Boundaries                             │
    │                                                          │
    │  ┌─────────────────────────────────────────────────┐     │
    │  │ TRUSTED: Controller Hardware (ESP32-S3)          │     │
    │  │                                                   │     │
    │  │  ┌───────────┐  ┌──────────┐  ┌──────────────┐  │     │
    │  │  │ Secure    │  │ PoAC     │  │ Input        │  │     │
    │  │  │ Key Store │  │ Engine   │  │ Polling      │  │     │
    │  │  │ (NVS enc) │  │          │  │ (direct SPI) │  │     │
    │  │  │           │  │ Sign     │  │              │  │     │
    │  │  │ ECDSA key │  │ Chain    │  │ No software  │  │     │
    │  │  │ never     │  │ Commit   │  │ can inject   │  │     │
    │  │  │ exported  │  │          │  │ fake inputs  │  │     │
    │  │  └───────────┘  └──────────┘  └──────────────┘  │     │
    │  │                                                   │     │
    │  └─────────────────────────┬─────────────────────────┘     │
    │                            │ BLE (encrypted)               │
    │  ┌─────────────────────────▼─────────────────────────┐     │
    │  │ SEMI-TRUSTED: Companion App                        │     │
    │  │                                                     │     │
    │  │  Can read PoAC records (verify, not forge)         │     │
    │  │  Can send commands (bounty inject, config)         │     │
    │  │  Cannot sign records (no key access)               │     │
    │  │  Cannot modify records in transit (sig check)      │     │
    │  └─────────────────────────┬───────────────────────────┘     │
    │                            │ MQTT/HTTPS                    │
    │  ┌─────────────────────────▼─────────────────────────┐     │
    │  │ UNTRUSTED: Bridge + Network                        │     │
    │  │                                                     │     │
    │  │  Can relay records (correct or withhold)           │     │
    │  │  Cannot forge records (no key)                     │     │
    │  │  Cannot reorder records (monotonic counter)        │     │
    │  │  Omission detected (chain linkage gap)             │     │
    │  └─────────────────────────┬───────────────────────────┘     │
    │                            │ RPC                           │
    │  ┌─────────────────────────▼─────────────────────────┐     │
    │  │ TRUSTLESS: IoTeX L1 Blockchain                     │     │
    │  │                                                     │     │
    │  │  Verifies signatures (P256 precompile)             │     │
    │  │  Enforces chain integrity (counter + hash)         │     │
    │  │  Manages bounty escrow (trustless settlement)      │     │
    │  │  Public auditability (anyone can verify)           │     │
    │  └─────────────────────────────────────────────────────┘     │
    │                                                              │
    └──────────────────────────────────────────────────────────────┘
```

---

## Slide 12: Development Roadmap

```
    Week:  1   2   3   4   5   6   7   8   9  10  11  12  ...  20  26
           │   │   │   │   │   │   │   │   │   │   │   │        │   │
    ═══════╪═══╪═══╪═══╪═══╪═══╪═══╪═══╪═══╪═══╪═══╪═══╪════════╪═══╪══
           │                   │
    Phase 0│ ██████████████████ │  PROTOTYPE
           │ ESP32-S3 devkit   │  - SPI bus tap on DS4 controller
           │ Wired connection  │  - Port VAPI agent to ESP-IDF
           │ PC serial debug   │  - Basic PoAC generation
           │                   │  - Anti-cheat heuristic fallback
           │                   │
           │              Phase 1│ ██████████████████  INTEGRATED
           │                    │  - Custom flex PCB inside DS4
           │                    │  - BLE pairing with companion app
           │                    │  - TinyML anti-cheat model v1
           │                    │  - Haptic feedback integration
           │                    │
           │                         Phase 2│ ████████████  CONTRACTS
           │                                │  - Gaming PoACVerifier
           │                                │  - Gaming BountyMarket
           │                                │  - Testnet deployment
           │                                │
           │                              Phase 3│ ████████████████  APP
           │                                     │  - Full Electron UI
           │                                     │  - Android app
           │                                     │  - Dev tools suite
           │                                     │
           │                                          Phase 4│ ████████
           │                                                 │ TOURNAMENT
           │                                                 │ - Multi-
           │                                                 │   player
           │                                                 │ - Oracle
           │                                                 │ - Speedrun
           │                                                 │
           │                                                      Phase 5
           │                                                      ██████
           │                                                      PROD
           │                                                      - Mfg
           │                                                      - Cert
           │                                                      - Ship
    ═══════╪══════════════════════════════════════════════════════════════
```

---

**Document End — VAPI DualShock Technical Architecture v1.0.0**
