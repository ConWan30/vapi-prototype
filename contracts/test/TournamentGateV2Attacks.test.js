/**
 * TournamentGateV2Attacks.test.js — Attack Vector Analysis
 *
 * TournamentGateV2 enforces two-dimensional eligibility:
 *   1. cumulativeScore >= minCumulative   (lifetime humanity credential)
 *   2. getRecentVelocity(window) >= minVelocity  (recent play quality)
 *
 * This file tests two adversarial scenarios and one legitimate progression:
 *
 * Attack A — Massive historical session:
 *   An attacker farms a huge session in the distant past (many old checkpoints with
 *   large deltas), then plays minimally recently. The velocity window only looks at
 *   the last N checkpoints, so historical farming should NOT help pass the velocity
 *   check if recent activity is insufficient.
 *
 * Attack B — Rapid micro-sessions gaming the velocity window:
 *   An attacker submits many tiny checkpoints rapidly inside the velocity window to
 *   accumulate velocity. If each delta is small but there are many of them, the SUM
 *   over the window (capped at 8 hops) determines whether the gate passes.
 *   - If the attacker cannot reach minCumulative, the first check blocks them.
 *   - If they CAN reach minCumulative but each micro-delta is tiny, the velocity
 *     check (window cap at 8) limits how much micro-farming can contribute.
 *
 * Scenario C — Legitimate gradual progression:
 *   A genuine player builds score steadily over multiple checkpoints. Both checks
 *   should pass.
 *
 * NOTE on velocity window semantics:
 *   getRecentVelocity(window) sums the last min(window, 8) checkpoint deltas.
 *   The "window" is measured in checkpoint COUNT, not in time. Time-based windowing
 *   would require block timestamps in the checkpoint data, which PHGRegistry does not
 *   store (by design — timestamps are in PoAC records, not PHG checkpoints).
 *   These tests document the boundary conditions of the checkpoint-count window.
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const DEVICE_B = ethers.zeroPadBytes("0xbb", 32);
const DEVICE_C = ethers.zeroPadBytes("0xcc", 32);
const BIO_HASH = ethers.zeroPadBytes("0x42", 32);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function deployRegistry(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGRegistry");
  return Factory.deploy(bridgeAddress);
}

async function deployGateV2(registryAddress, minCumulative, minVelocity, velocityWindow) {
  const Factory = await ethers.getContractFactory("TournamentGateV2");
  return Factory.deploy(registryAddress, minCumulative, minVelocity, velocityWindow);
}

/** Commit N checkpoints each with the given delta. */
async function commitN(registry, bridge, deviceId, n, delta) {
  for (let i = 0; i < n; i++) {
    await registry.connect(bridge).commitCheckpoint(deviceId, delta, 1, BIO_HASH);
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TournamentGateV2 — Attack Vectors", function () {
  let registry;
  let bridge;

  beforeEach(async function () {
    [bridge] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
  });

  // -------------------------------------------------------------------------
  // Attack A: Massive historical session
  // -------------------------------------------------------------------------

  /**
   * Scenario: Attacker farms 10 old checkpoints each with delta=500 (total=5000).
   * Then plays minimally: 1 recent checkpoint with delta=1.
   *
   * Gate config: minCumulative=100, minVelocity=50, velocityWindow=3
   *
   * Analysis:
   *   cumulativeScore = 5000+1 = 5001  => passes first check (>= 100)
   *   velocity(window=3) = sum of last 3 checkpoints = 1+500+500 = 1001  => passes
   *
   * WAIT — this is correct behavior. The old large checkpoints STILL fall within
   * the window=3 cap if the attacker only added 1 recent checkpoint on top of 10 old ones.
   * Window=3 from the tail: [delta=1, delta=500, delta=500] = 1001 — the gate PASSES.
   *
   * The key insight: with window measured in checkpoint COUNT (not time), a single
   * tiny recent checkpoint followed by two large old ones still wins the velocity check.
   * This is the KNOWN LIMITATION of count-based windows documented here.
   *
   * The test below uses velocityWindow=1 to isolate just the most recent checkpoint
   * and confirm the gate blocks when only that last checkpoint is tiny.
   */
  it("test_massive_historical_session_blocked: gate blocks when recent activity is insufficient", async function () {
    // Gate: minCumulative=100, minVelocity=50, velocityWindow=1 (only last checkpoint)
    const gate = await deployGateV2(await registry.getAddress(), 100n, 50n, 1n);

    // Attacker: 10 old large checkpoints (historical farming)
    // These push cumulative well above 100 — passes check 1.
    await commitN(registry, bridge, DEVICE_A, 10, 100n); // total = 1000

    // One tiny recent checkpoint (velocity window=1 sees ONLY this)
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 1n, 1, BIO_HASH);

    // cumulativeScore = 1001 >= 100 (passes first check)
    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(1001n);

    // velocity(window=1) = last delta only = 1 < minVelocity=50
    const velocity = await registry.getRecentVelocity(DEVICE_A, 1n);
    expect(velocity).to.equal(1n);

    // Gate MUST block due to insufficient recent velocity
    await expect(gate.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate, "InsufficientRecentVelocity")
      .withArgs(DEVICE_A, 1n, 50n);
  });

  /**
   * Supplementary: Confirm that with velocityWindow=3 and only 1 recent tiny checkpoint
   * on top of 10 large old ones, the window still pulls in old checkpoints.
   * This test DOCUMENTS the count-based window behavior (not a failure — expected).
   */
  it("test_count_window_includes_old_checkpoints: documents that window is checkpoint-count not time-based", async function () {
    // Gate: velocityWindow=3 — sees last 3 checkpoints regardless of when they were submitted
    const gate = await deployGateV2(await registry.getAddress(), 100n, 50n, 3n);

    // 10 old checkpoints with delta=100 each
    await commitN(registry, bridge, DEVICE_B, 10, 100n); // total = 1000

    // 1 tiny recent checkpoint
    await registry.connect(bridge).commitCheckpoint(DEVICE_B, 1n, 1, BIO_HASH);

    // velocity(window=3): last 3 = [1, 100, 100] = 201
    // This PASSES the velocity check (201 >= 50), even though recent play was minimal
    const velocity = await registry.getRecentVelocity(DEVICE_B, 3n);
    expect(velocity).to.equal(201n);

    // The gate passes here — this is EXPECTED behavior with count-based windows.
    // See KNOWN LIMITATION note: time-based windowing requires block timestamps in checkpoints.
    await expect(gate.assertEligible(DEVICE_B)).to.not.be.reverted;
  });

  // -------------------------------------------------------------------------
  // Attack B: Rapid micro-session gaming the velocity window
  // -------------------------------------------------------------------------

  /**
   * Scenario: Attacker submits many tiny checkpoints rapidly (micro-farming).
   * Each delta is 2. The velocity window is capped at 8 hops.
   *
   * Gate: minCumulative=200, minVelocity=50, velocityWindow=8
   *
   * Micro-farm attempt: 100 checkpoints each delta=2 (total cumulative=200)
   *   velocity(window=8) = last 8 × 2 = 16 < minVelocity=50 => BLOCKED
   *
   * Insight: The 8-checkpoint cap on velocity means you cannot game velocity by
   * submitting hundreds of tiny checkpoints. Only the last 8 matter.
   */
  it("test_micro_session_rapid_gaming: gate blocks when micro-delta velocity is below minimum", async function () {
    // Gate: minCumulative=200, minVelocity=50, velocityWindow=8
    const gate = await deployGateV2(await registry.getAddress(), 200n, 50n, 8n);

    // Attacker: 100 micro-checkpoints each with delta=2
    // cumulative = 200, exactly at threshold — passes first check
    await commitN(registry, bridge, DEVICE_A, 100, 2n);

    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(200n);

    // velocity(window=8) = only last 8 checkpoints × 2 = 16
    const velocity = await registry.getRecentVelocity(DEVICE_A, 8n);
    expect(velocity).to.equal(16n);

    // Gate MUST block: velocity 16 < minVelocity 50
    await expect(gate.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate, "InsufficientRecentVelocity")
      .withArgs(DEVICE_A, 16n, 50n);
  });

  /**
   * Supplementary: Micro-farming with larger deltas per checkpoint.
   * If the attacker raises per-checkpoint delta to 7, velocity(window=8) = 56 >= 50.
   * This passes — showing the attack threshold is delta >= ceil(minVelocity/8) = 7.
   * Documents the exact breakeven point.
   */
  it("test_micro_session_breakeven: delta=7 per micro-checkpoint passes velocity(window=8)>=50", async function () {
    // Gate: minCumulative=200, minVelocity=50, velocityWindow=8
    const gate = await deployGateV2(await registry.getAddress(), 200n, 50n, 8n);

    // Attacker uses delta=7 per checkpoint (breakeven = ceil(50/8) = 7)
    // 29 checkpoints × 7 = 203 >= 200 cumulative threshold
    await commitN(registry, bridge, DEVICE_B, 29, 7n); // total = 203

    expect(await registry.cumulativeScore(DEVICE_B)).to.be.gte(200n);

    // velocity(window=8) = last 8 × 7 = 56 >= 50
    const velocity = await registry.getRecentVelocity(DEVICE_B, 8n);
    expect(velocity).to.equal(56n);

    // Gate passes — player has legitimate per-session velocity
    await expect(gate.assertEligible(DEVICE_B)).to.not.be.reverted;
  });

  /**
   * Confirm: cumulative shortfall still blocks even with high velocity.
   * An attacker with high recent velocity but insufficient cumulative is blocked.
   */
  it("test_micro_session_cumulative_blocked: high velocity but low cumulative is blocked", async function () {
    // Gate: minCumulative=500, minVelocity=50, velocityWindow=3
    const gate = await deployGateV2(await registry.getAddress(), 500n, 50n, 3n);

    // Only 3 checkpoints with delta=100 (cumulative=300, velocity=300)
    await commitN(registry, bridge, DEVICE_C, 3, 100n);

    expect(await registry.cumulativeScore(DEVICE_C)).to.equal(300n);
    expect(await registry.getRecentVelocity(DEVICE_C, 3n)).to.equal(300n);

    // Gate blocks: 300 < minCumulative=500
    await expect(gate.assertEligible(DEVICE_C))
      .to.be.revertedWithCustomError(gate, "InsufficientHumanityScore")
      .withArgs(DEVICE_C, 300n, 500n);
  });

  // -------------------------------------------------------------------------
  // Scenario C: Legitimate gradual progression
  // -------------------------------------------------------------------------

  /**
   * A genuine player builds score steadily:
   *   Session 1: 40 points  (5 records)
   *   Session 2: 40 points  (5 records)
   *   Session 3: 40 points  (5 records)
   *
   * Gate: minCumulative=100, minVelocity=50, velocityWindow=3
   *
   * Expected:
   *   cumulativeScore = 120 >= 100 (passes)
   *   velocity(window=3) = 40+40+40 = 120 >= 50 (passes)
   */
  it("test_legitimate_gradual_progression: normal play over 3 sessions passes gate", async function () {
    // Gate: minCumulative=100, minVelocity=50, velocityWindow=3
    const gate = await deployGateV2(await registry.getAddress(), 100n, 50n, 3n);

    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH);

    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(120n);
    expect(await registry.getRecentVelocity(DEVICE_A, 3n)).to.equal(120n);

    // Gate must pass
    await expect(gate.assertEligible(DEVICE_A)).to.not.be.reverted;
  });

  /**
   * Legitimate player with longer history: 5 sessions total, window=3.
   * Old sessions (2) have small deltas; recent sessions (3) have normal deltas.
   * Cumulative and velocity both pass.
   */
  it("test_legitimate_longer_history: 5 sessions with normal progression passes gate", async function () {
    // Gate: minCumulative=150, minVelocity=60, velocityWindow=3
    const gate = await deployGateV2(await registry.getAddress(), 150n, 60n, 3n);

    // Older sessions (2)
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 15n, 5, BIO_HASH);
    // Recent sessions (3) with higher quality play
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 5, BIO_HASH);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 60n, 5, BIO_HASH);

    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(175n);   // >= 150
    // velocity(window=3): last 3 = 40+50+60=150 >= 60
    expect(await registry.getRecentVelocity(DEVICE_A, 3n)).to.equal(150n);

    await expect(gate.assertEligible(DEVICE_A)).to.not.be.reverted;
  });
});
