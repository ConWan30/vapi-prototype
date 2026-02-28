/**
 * VAPI — Deployer Keystore Decryption Helper
 *
 * Reads an Ethereum keystore JSON file, decrypts it with the password from
 * the environment, and writes the raw private key ("0x...") to stdout.
 *
 * Called synchronously by hardhat.config.js when DEPLOYER_KEY_SOURCE=keystore:
 *   execSync(`node scripts/decrypt-key.js "${keystorePath}"`)
 *
 * Environment:
 *   DEPLOYER_KEYSTORE_PASSWORD_ENV  — name of the env var holding the password
 *                                     (default: DEPLOYER_KEYSTORE_PASSWORD)
 *   <password-env-var>              — the actual decryption password
 *
 * Exit codes:
 *   0 — success; private key printed to stdout (no trailing newline)
 *   1 — error; diagnostic message printed to stderr
 *
 * Security notes:
 *   • Never log or print the password.
 *   • The private key is written ONLY to stdout so the caller can capture it.
 *   • Ensure DEPLOYER_KEYSTORE_PASSWORD is not committed to version control.
 */

"use strict";

const { ethers } = require("ethers");
const fs = require("fs");

async function main() {
  const [, , keystorePath] = process.argv;

  if (!keystorePath) {
    console.error("Usage: node decrypt-key.js <keystore.json>");
    process.exit(1);
  }

  const pwEnv =
    process.env.DEPLOYER_KEYSTORE_PASSWORD_ENV || "DEPLOYER_KEYSTORE_PASSWORD";
  const password = process.env[pwEnv];

  if (!password) {
    console.error(
      `Password env var '${pwEnv}' is not set. ` +
        "Set DEPLOYER_KEYSTORE_PASSWORD (or DEPLOYER_KEYSTORE_PASSWORD_ENV to " +
        "point at a different env var)."
    );
    process.exit(1);
  }

  let keystoreJson;
  try {
    keystoreJson = fs.readFileSync(keystorePath, "utf8");
  } catch (err) {
    console.error(`Cannot read keystore file '${keystorePath}': ${err.message}`);
    process.exit(1);
  }

  let wallet;
  try {
    wallet = await ethers.Wallet.fromEncryptedJson(keystoreJson, password);
  } catch (err) {
    console.error(`Keystore decryption failed: ${err.message}`);
    process.exit(1);
  }

  // Write private key to stdout (no newline — caller does .trim())
  process.stdout.write(wallet.privateKey);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
