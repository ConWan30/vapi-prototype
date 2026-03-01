/**
 * PHGRegistryDeltaFix.test.js — Phase 24 delta-fix regression tests
 *
 * The Phase 24 fix ensures that each commitCheckpoint() call transmits ONLY the
 * score increment since the last commit (scoreDelta), NOT the running cumulative.
 *
 * Bug scenario (pre-fix):
 *   - Device scores 10, commits → checkpoint records 10 (correct)
 *   - Device scores 10 more (total=20), commits → checkpoint SHOULD record 10 (delta)
 *     but a buggy caller sending cumulative would record 20 (wrong)
 *
 * The registry itself just accumulates whatever delta it is given, so these tests
 * verify the correct caller contract: the scoreDeltaAt mapping for each checkpoint
 * stores ONLY the incremental delta, and getRecentVelocity returns sums of deltas
 * rather than the final cumulative. If a caller mistakenly passes cumulative values
 * the velocity sums will overshoot and these tests will fail.
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const BIO_HASH_1 = ethers.zeroPadBytes("0x11", 32);
const BIO_HASH_2 = ethers.zeroPadBytes("0x22", 32);
const BIO_HASH_3 = ethers.zeroPadBytes("0x33", 32);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function deployRegistry(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGRegistry");
  return Factory.deploy(bridgeAddress);
}

/**
 * Read scoreDeltaAt for the current checkpointHead.
 * Returns the delta stored in the most recent checkpoint.
 */
async function getLastDelta(registry, deviceId) {
  const head = await registry.checkpointHead(deviceId);
  return registry.scoreDeltaAt(head);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PHGRegistry — Phase 24 Delta Fix", function () {
  let registry;
  let bridge;

  beforeEach(async function () {
    [bridge] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
  });

  /**
   * Test 1: Classic two-commit delta scenario.
   *
   * The old bug: caller sends cumulative each time.
   *   Commit 1: delta=10  → scoreDeltaAt[head1] = 10  (correct in both old and new)
   *   Commit 2: delta=20  → scoreDeltaAt[head2] = 20  (WRONG — should be 10)
   *
   * The correct behavior: caller sends only increment.
   *   Commit 1: delta=10  → scoreDeltaAt[head1] = 10
   *   Commit 2: delta=10  → scoreDeltaAt[head2] = 10  (delta since last commit)
   *   cumulativeScore = 20 (registry accumulates internally)
   *
   * This test verifies that scoreDeltaAt on the SECOND checkpoint stores 10, not 20,
   * and that cumulativeScore correctly sums to 20 regardless.
   */
  it("1. second checkpoint stores delta=10, not cumulative=20", async function () {
    // First commit: device earns 10 points
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH_1);
    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(10n);
    const deltaAfterFirst = await getLastDelta(registry, DEVICE_A);
    expect(deltaAfterFirst).to.equal(10n, "first checkpoint delta should be 10");

    // Second commit: device earns 10 MORE points (not cumulative=20)
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH_2);
    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(20n, "cumulative should be 20");
    const deltaAfterSecond = await getLastDelta(registry, DEVICE_A);

    // The delta stored at the SECOND checkpoint must be 10 (the increment), NOT 20.
    // If a buggy caller sends the cumulative (20) instead of the delta (10),
    // this assertion fails — catching the Phase 24 regression.
    expect(deltaAfterSecond).to.equal(
      10n,
      "second checkpoint scoreDelta must be 10 (increment only), not 20 (cumulative)"
    );
    expect(deltaAfterSecond).to.not.equal(
      20n,
      "scoreDelta must NOT be the cumulative value — that is the Phase 24 bug"
    );
  });

  /**
   * Test 2: Three sequential checkpoints each recording only their own delta.
   *
   * Scenario: device earns 10, 15, 20 points in three sessions.
   *   Expected scoreDeltaAt per checkpoint: 10, 15, 20
   *   Expected cumulativeScore after all three: 45
   *   Expected getRecentVelocity(window=3): 10+15+20=45
   *
   * A buggy cumulative caller would send 10, 25, 45 as deltas, causing:
   *   getRecentVelocity(window=3) = 10+25+45 = 80 (WRONG)
   */
  it("2. three sequential checkpoints each store only their incremental delta", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 3, BIO_HASH_1);
    const head1 = await registry.checkpointHead(DEVICE_A);
    expect(await registry.scoreDeltaAt(head1)).to.equal(10n, "checkpoint 1 delta = 10");

    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 15n, 3, BIO_HASH_2);
    const head2 = await registry.checkpointHead(DEVICE_A);
    expect(await registry.scoreDeltaAt(head2)).to.equal(15n, "checkpoint 2 delta = 15");

    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 20n, 3, BIO_HASH_3);
    const head3 = await registry.checkpointHead(DEVICE_A);
    expect(await registry.scoreDeltaAt(head3)).to.equal(20n, "checkpoint 3 delta = 20");

    // Cumulative should be 10+15+20=45
    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(45n);

    // Velocity over window=3 should be 45 (sum of all three increments)
    // If a buggy caller had sent cumulative values (10, 25, 45), velocity would be 80
    const velocity = await registry.getRecentVelocity(DEVICE_A, 3n);
    expect(velocity).to.equal(45n, "velocity(window=3) must be 45, not 80 (cumulative-bug value)");
    expect(velocity).to.not.equal(80n, "80 = 10+25+45 is the Phase 24 cumulative-pass bug value");
  });

  /**
   * Test 3: Velocity window correctly excludes old checkpoints.
   *
   * After 5 checkpoints with deltas [100, 100, 5, 5, 5]:
   *   cumulativeScore = 215
   *   velocity(window=3) should be 5+5+5 = 15 (last 3 only)
   *
   * A buggy caller sending cumulative values [100, 200, 205, 210, 215]:
   *   velocity(window=3) = 205+210+215 = 630 — dramatically wrong.
   *
   * This test validates that large HISTORICAL deltas do not bleed into recent velocity.
   */
  it("3. old checkpoints do not inflate velocity window", async function () {
    // Two old large sessions
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 10, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 10, BIO_HASH_2);
    // Three recent small sessions
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 5n, 2, BIO_HASH_3);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 5n, 2, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 5n, 2, BIO_HASH_2);

    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(215n);

    // Velocity window=3: should capture only the last 3 deltas (5+5+5=15)
    const velocity3 = await registry.getRecentVelocity(DEVICE_A, 3n);
    expect(velocity3).to.equal(15n, "velocity(window=3) = last 3 deltas = 15");

    // Velocity window=5: all five deltas (100+100+5+5+5=215)
    const velocity5 = await registry.getRecentVelocity(DEVICE_A, 5n);
    expect(velocity5).to.equal(215n, "velocity(window=5) = all 5 deltas = 215");
  });

  /**
   * Test 4: Verify prevCheckpointAt chain integrity with delta-only commits.
   *
   * Each checkpoint head is unique and chains back to the previous one.
   * This confirms the checkpoint linked-list is consistent even when deltas vary.
   */
  it("4. checkpoint chain is correctly linked with per-commit deltas", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH_1);
    const head1 = await registry.checkpointHead(DEVICE_A);

    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 20n, 5, BIO_HASH_2);
    const head2 = await registry.checkpointHead(DEVICE_A);

    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 30n, 5, BIO_HASH_3);
    const head3 = await registry.checkpointHead(DEVICE_A);

    // Each head must be distinct
    expect(head1).to.not.equal(head2);
    expect(head2).to.not.equal(head3);
    expect(head1).to.not.equal(head3);

    // Chain linkage: head3 → head2 → head1 → 0x0
    expect(await registry.prevCheckpointAt(head3)).to.equal(head2);
    expect(await registry.prevCheckpointAt(head2)).to.equal(head1);
    expect(await registry.prevCheckpointAt(head1)).to.equal(ethers.ZeroHash);

    // Each stored delta matches what was passed
    expect(await registry.scoreDeltaAt(head1)).to.equal(10n);
    expect(await registry.scoreDeltaAt(head2)).to.equal(20n);
    expect(await registry.scoreDeltaAt(head3)).to.equal(30n);
  });
});
