/**
 * Phase 29 — ZK Trusted Setup Ceremony
 *
 * Automates the snarkjs Powers-of-Tau ceremony and per-circuit zkey generation
 * for both VAPI circuits (TeamProof.circom and PitlSessionProof.circom).
 *
 * Run:
 *   npx hardhat run scripts/run-ceremony.js
 *   -- OR --
 *   node scripts/run-ceremony.js
 *
 * Prerequisites:
 *   npm install snarkjs circom   (or: npm i snarkjs && cargo install circom)
 *
 * Outputs (written to contracts/circuits/):
 *   pot12_final.ptau               — Shared Powers of Tau (2^12 constraints)
 *   TeamProof_final.zkey           — Circuit proving key
 *   TeamProof_verification_key.json
 *   PitlSessionProof_final.zkey
 *   PitlSessionProof_verification_key.json
 *
 * WARNING:
 *   This script uses a SINGLE-CONTRIBUTOR ceremony, which is NOT production-grade.
 *   For production use (mainnet), run a multi-party computation (MPC) ceremony:
 *   https://github.com/iden3/snarkjs#7-contribute-to-the-phase-2-ceremony
 *
 * After running:
 *   npx hardhat test   (ZK tests that previously skipped will now pass)
 */

const { execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const CIRCUITS_DIR = path.join(__dirname, "..", "circuits");
const PTAU_FILE = path.join(CIRCUITS_DIR, "pot12_final.ptau");
const PTAU_URL =
  "https://hermez.s3-eu-west-1.amazonaws.com/powersOfTau28_hez_final_12.ptau";

function run(cmd, opts = {}) {
  console.log(`  $ ${cmd}`);
  execSync(cmd, { stdio: "inherit", ...opts });
}

function fileExists(p) {
  return fs.existsSync(p);
}

// ---------------------------------------------------------------------------
// Step 1: Download Powers of Tau (if not already present)
// ---------------------------------------------------------------------------
if (!fileExists(PTAU_FILE)) {
  console.log("\n[1/3] Downloading Powers of Tau (2^12)...");
  console.log(`  Source: ${PTAU_URL}`);
  run(`curl -L "${PTAU_URL}" -o "${PTAU_FILE}"`);
  console.log("  Downloaded.");
} else {
  console.log("\n[1/3] Powers of Tau already present — skipping download.");
}

// ---------------------------------------------------------------------------
// Step 2: Compile circuits (if .r1cs not present)
// ---------------------------------------------------------------------------
for (const circuit of ["TeamProof", "PitlSessionProof"]) {
  const circomFile = path.join(CIRCUITS_DIR, `${circuit}.circom`);
  const r1csFile = path.join(CIRCUITS_DIR, `${circuit}.r1cs`);

  if (!fileExists(circomFile)) {
    console.error(`\nERROR: Circuit file not found: ${circomFile}`);
    process.exit(1);
  }

  if (!fileExists(r1csFile)) {
    console.log(`\n[2/3] Compiling ${circuit}.circom...`);
    run(`circom "${circomFile}" --r1cs --wasm -o "${CIRCUITS_DIR}"`);
    console.log(`  Compiled → ${r1csFile}`);
  } else {
    console.log(`\n[2/3] ${circuit}.r1cs already compiled — skipping.`);
  }
}

// ---------------------------------------------------------------------------
// Step 3: Generate .zkey files for each circuit
// ---------------------------------------------------------------------------
for (const circuit of ["TeamProof", "PitlSessionProof"]) {
  const r1csFile    = path.join(CIRCUITS_DIR, `${circuit}.r1cs`);
  const zkey0File   = path.join(CIRCUITS_DIR, `${circuit}_0.zkey`);
  const zkeyFinal   = path.join(CIRCUITS_DIR, `${circuit}_final.zkey`);
  const vkeyFile    = path.join(CIRCUITS_DIR, `${circuit}_verification_key.json`);

  if (fileExists(zkeyFinal)) {
    console.log(`\n[3/3] ${circuit}_final.zkey already exists — skipping.`);
    continue;
  }

  console.log(`\n[3/3] Setting up ${circuit}...`);

  // Phase 2: circuit-specific setup
  console.log(`  groth16 setup...`);
  run(`npx snarkjs groth16 setup "${r1csFile}" "${PTAU_FILE}" "${zkey0File}"`);

  // Single dev contribution (NOT production-grade — see warning above)
  console.log(`  Contributing (dev-only)...`);
  run(
    `echo "vapi_phase29_dev_entropy" | npx snarkjs zkey contribute ` +
    `"${zkey0File}" "${zkeyFinal}" --name="Phase29Dev" -v`
  );

  // Export verification key
  console.log(`  Exporting verification key...`);
  run(`npx snarkjs zkey export verificationkey "${zkeyFinal}" "${vkeyFile}"`);

  // Clean up intermediate
  if (fileExists(zkey0File)) {
    fs.unlinkSync(zkey0File);
  }

  console.log(`  DONE: ${zkeyFinal}`);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
console.log("\n" + "=".repeat(60));
console.log("ZK Ceremony Complete");
console.log("=".repeat(60));
for (const circuit of ["TeamProof", "PitlSessionProof"]) {
  const zkey = path.join(CIRCUITS_DIR, `${circuit}_final.zkey`);
  const vkey = path.join(CIRCUITS_DIR, `${circuit}_verification_key.json`);
  console.log(`  ${circuit}:`);
  console.log(`    .zkey:  ${fileExists(zkey) ? "OK" : "MISSING"} → ${zkey}`);
  console.log(`    vkey:   ${fileExists(vkey) ? "OK" : "MISSING"} → ${vkey}`);
}
console.log("\nRun tests: npx hardhat test");
console.log("ZK tests that previously skipped will now pass.");
