require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

/**
 * VAPI — Hardhat Configuration for IoTeX Blockchain
 *
 * Networks:
 *   - iotex_testnet: IoTeX testnet (chain ID 4690)
 *   - iotex_mainnet: IoTeX mainnet (chain ID 4689)
 *
 * The IoTeX EVM supports the P256 precompile at address 0x0100,
 * which is used by PoACVerifier.sol for ECDSA-P256 signature verification.
 *
 * Usage:
 *   npx hardhat compile
 *   npx hardhat run scripts/deploy.js --network iotex_testnet
 *   npx hardhat verify --network iotex_testnet <address>
 *
 * Deployer key sources (DEPLOYER_KEY_SOURCE env var):
 *   "env"      — read DEPLOYER_PRIVATE_KEY plaintext from env (default; dev/testnet only)
 *   "keystore" — decrypt Ethereum keystore JSON at DEPLOYER_KEYSTORE_PATH (production)
 *                Password read from env var named by DEPLOYER_KEYSTORE_PASSWORD_ENV
 *                (default: DEPLOYER_KEYSTORE_PASSWORD).
 */

const DEPLOYER_KEY_SOURCE = process.env.DEPLOYER_KEY_SOURCE || "env";

function loadDeployerKey() {
  if (DEPLOYER_KEY_SOURCE === "keystore") {
    const { execSync } = require("child_process");
    const ksPath = process.env.DEPLOYER_KEYSTORE_PATH;
    if (!ksPath) {
      throw new Error("DEPLOYER_KEYSTORE_PATH must be set when DEPLOYER_KEY_SOURCE=keystore");
    }
    const pwEnv = process.env.DEPLOYER_KEYSTORE_PASSWORD_ENV || "DEPLOYER_KEYSTORE_PASSWORD";
    // Call the decrypt helper synchronously (Hardhat config cannot be async)
    const raw = execSync(
      `node "${__dirname}/scripts/decrypt-key.js" "${ksPath}"`,
      { env: { ...process.env, DEPLOYER_KEYSTORE_PASSWORD_ENV: pwEnv } }
    ).toString().trim();
    return raw;  // "0x<hex>"
  }
  return process.env.DEPLOYER_PRIVATE_KEY || "0x" + "0".repeat(64);
}

const DEPLOYER_PRIVATE_KEY = loadDeployerKey();

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      viaIR: true,
      optimizer: {
        enabled: true,
        runs: 200,
      },
      evmVersion: "paris",
    },
  },

  networks: {
    // IoTeX Testnet
    iotex_testnet: {
      url: "https://babel-api.testnet.iotex.io",
      chainId: 4690,
      accounts: [DEPLOYER_PRIVATE_KEY],
      gas: 8000000,
      gasPrice: 1000000000000, // 1000 Gwei (IoTeX testnet)
    },

    // IoTeX Mainnet
    iotex_mainnet: {
      url: "https://babel-api.mainnet.iotex.io",
      chainId: 4689,
      accounts: [DEPLOYER_PRIVATE_KEY],
      gas: 8000000,
      gasPrice: 1000000000000,
    },

    // Local Hardhat node for testing
    hardhat: {
      chainId: 31337,
    },
  },

  // Etherscan-compatible verification for IoTeX
  etherscan: {
    apiKey: {
      iotex_testnet: "not-needed",
      iotex_mainnet: "not-needed",
    },
    customChains: [
      {
        network: "iotex_testnet",
        chainId: 4690,
        urls: {
          apiURL: "https://testnet.iotexscan.io/api",
          browserURL: "https://testnet.iotexscan.io",
        },
      },
      {
        network: "iotex_mainnet",
        chainId: 4689,
        urls: {
          apiURL: "https://iotexscan.io/api",
          browserURL: "https://iotexscan.io",
        },
      },
    ],
  },

  paths: {
    sources: "./contracts",
    tests: "./test",
    cache: "./cache",
    artifacts: "./artifacts",
  },
};
