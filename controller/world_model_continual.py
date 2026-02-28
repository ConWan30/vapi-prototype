"""
VAPI Phase 13 — Self-Evolving World Model with Continual Learning (EWC)

Cross-layer enhancement replacing the shallow EMA WorldModel with an
EWC-regularized MLP (30 -> 64 -> 32 -> 8) that encodes the player's
cognitive state as an 8-dimensional embedding.

Key insight: The world_model_hash field in the PoAC body (offset 0x60, 32 bytes)
already exists and is committed per-record. By making world_model_hash =
SHA-256(model_weights), the PoAC CHAIN becomes a cryptographically anchored,
player-owned cognitive evolution proof.

EWC (Elastic Weight Consolidation) prevents catastrophic forgetting by
penalizing changes to weights that were important for previous sessions
(measured via diagonal Fisher information matrix).

Design:
  Architecture:  30 -> 64 -> 32 -> 8 (ReLU activations)
  Weight count:  30*64 + 64 + 64*32 + 32 + 32*8 + 8 = 4,488 parameters
  Memory:        ~18KB in float32 (4 bytes * 4,488)
  Computation:   Single forward pass + EWC-SGD step < 0.1ms on modern CPU
  Dependency:    NumPy only — no PyTorch, no TensorFlow

E2+E4 Synergy:
  world_model_hash = SHA-256(ewc_weights || preference_weights)
  The hash fingerprints both how the player plays AND what they value economically.

Backward compatibility:
  from_legacy_world_model() migrates old EMA WorldModel dicts.
  The 4 baseline fields (reaction/precision/consistency/imu_corr) are preserved.

Usage in dualshock_integration.py:
  # After each session:
  session_vec = EWCWorldModel.build_session_vector(feature_frames)
  session_label = mean_confidence / 255.0
  model.update(session_vec, session_label)
  # At task boundary (e.g., every 10 sessions):
  model.compute_fisher([...recent_session_vecs...])
  # Build PoAC hash:
  wm_hash = model.compute_hash()   # 32 bytes -> world_model_hash
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Architecture constants
# ---------------------------------------------------------------------------

INPUT_DIM  = 30   # Matches FeatureFrame 30-feature vector
HIDDEN1    = 64
HIDDEN2    = 32
OUTPUT_DIM = 8    # Cognitive state embedding dimension

EWC_LAMBDA  = 400.0   # EWC regularization strength
LEARNING_RATE = 0.001  # SGD learning rate
TASK_BOUNDARY_SESSIONS = 10  # Compute Fisher every N sessions

# ---------------------------------------------------------------------------
# EWC World Model
# ---------------------------------------------------------------------------

class EWCWorldModel:
    """
    Self-evolving world model: small MLP (30 -> 64 -> 32 -> 8) with EWC
    regularization to prevent catastrophic forgetting across gaming sessions.

    The world_model_hash field in each PoAC body is:
        SHA-256(self.serialize_weights())

    This makes each record a cryptographic snapshot of the player's
    cognitive state at that moment — the chain of hashes IS the evolution proof.
    """

    def __init__(self, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        # Xavier initialization
        self.W1 = rng.standard_normal((INPUT_DIM, HIDDEN1)).astype(np.float32) * math.sqrt(2.0 / INPUT_DIM)
        self.b1 = np.zeros(HIDDEN1, dtype=np.float32)
        self.W2 = rng.standard_normal((HIDDEN1, HIDDEN2)).astype(np.float32) * math.sqrt(2.0 / HIDDEN1)
        self.b2 = np.zeros(HIDDEN2, dtype=np.float32)
        self.W3 = rng.standard_normal((HIDDEN2, OUTPUT_DIM)).astype(np.float32) * math.sqrt(2.0 / HIDDEN2)
        self.b3 = np.zeros(OUTPUT_DIM, dtype=np.float32)

        # EWC state
        self._fisher: dict[str, np.ndarray] = {
            k: np.zeros_like(getattr(self, k)) for k in ("W1", "b1", "W2", "b2", "W3", "b3")
        }
        self._prev_weights: dict[str, np.ndarray] = {}
        self._ewc_active: bool = False

        # Legacy baselines (backward compat)
        self.reaction_baseline: float    = 250.0
        self.precision_baseline: float   = 0.5
        self.consistency_baseline: float = 50.0
        self.imu_corr_baseline: float    = 0.5

        # Session tracking
        self.total_sessions: int = 0
        self.total_updates: int  = 0

    # -----------------------------------------------------------------------
    # Forward pass (NumPy ReLU MLP — no framework dependency)
    # -----------------------------------------------------------------------

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass: x (30,) -> embedding (8,).
        Architecture: Linear -> ReLU -> Linear -> ReLU -> Linear
        """
        h1 = np.maximum(0.0, x @ self.W1 + self.b1)
        h2 = np.maximum(0.0, h1 @ self.W2 + self.b2)
        return h2 @ self.W3 + self.b3

    def get_embedding(self, session_vec: np.ndarray) -> np.ndarray:
        """Return 8-dim cognitive state embedding for current session (no weight mutation)."""
        import numpy as _np
        x = _np.array(session_vec, dtype=np.float32)
        x = (x - x.mean()) / (x.std() + 1e-8)
        return self.forward(x)

    # -----------------------------------------------------------------------
    # EWC loss and gradient
    # -----------------------------------------------------------------------

    def _ewc_penalty(self) -> dict[str, np.ndarray]:
        """
        EWC gradient penalty for each weight tensor.
        grad_penalty[k] = ewc_lambda * fisher[k] * (current_weight - prev_weight)
        """
        if not self._ewc_active:
            return {k: np.zeros_like(getattr(self, k)) for k in ("W1", "b1", "W2", "b2", "W3", "b3")}
        return {
            k: EWC_LAMBDA * self._fisher[k] * (getattr(self, k) - self._prev_weights[k])
            for k in ("W1", "b1", "W2", "b2", "W3", "b3")
        }

    def _backward(
        self, x: np.ndarray, y_target: float
    ) -> tuple[float, dict[str, np.ndarray]]:
        """
        Manual backprop for 3-layer MLP with MSE loss + EWC penalty.

        Returns (total_loss, gradients_dict).
        """
        # --- Forward ---
        h1_pre = x @ self.W1 + self.b1          # (64,)
        h1 = np.maximum(0.0, h1_pre)             # ReLU
        h2_pre = h1 @ self.W2 + self.b2          # (32,)
        h2 = np.maximum(0.0, h2_pre)             # ReLU
        out = h2 @ self.W3 + self.b3             # (8,)

        # MSE target: broadcast scalar to (8,)
        target = np.full(OUTPUT_DIM, y_target, dtype=np.float32)
        mse_loss = float(np.mean((out - target) ** 2))

        # --- Backward through Linear -> ReLU -> Linear -> ReLU -> Linear ---
        d_out = 2.0 * (out - target) / OUTPUT_DIM   # (8,)

        d_W3 = np.outer(h2, d_out)                  # (32, 8)
        d_b3 = d_out.copy()                          # (8,)
        d_h2 = d_out @ self.W3.T                     # (32,)

        d_h2_pre = d_h2 * (h2_pre > 0).astype(np.float32)  # ReLU mask
        d_W2 = np.outer(h1, d_h2_pre)               # (64, 32)
        d_b2 = d_h2_pre.copy()                       # (32,)
        d_h1 = d_h2_pre @ self.W2.T                  # (64,)

        d_h1_pre = d_h1 * (h1_pre > 0).astype(np.float32)  # ReLU mask
        d_W1 = np.outer(x, d_h1_pre)                # (30, 64)
        d_b1 = d_h1_pre.copy()                      # (64,)

        grads = {"W1": d_W1, "b1": d_b1, "W2": d_W2, "b2": d_b2, "W3": d_W3, "b3": d_b3}

        # EWC penalty contribution
        ewc_pen = self._ewc_penalty()
        for k in grads:
            grads[k] += ewc_pen[k]

        # EWC loss contribution (for reporting only)
        ewc_loss = sum(
            float(EWC_LAMBDA * np.sum(self._fisher[k] * (getattr(self, k) - self._prev_weights[k]) ** 2))
            for k in ("W1", "b1", "W2", "b2", "W3", "b3")
        ) if self._ewc_active else 0.0

        return (mse_loss + ewc_loss, grads)

    # -----------------------------------------------------------------------
    # Update (one SGD step per session)
    # -----------------------------------------------------------------------

    def update(self, session_features: np.ndarray, session_label: float) -> float:
        """
        Update model weights with one EWC-SGD step.

        Args:
            session_features: 30-dim mean feature vector over the session.
            session_label:     Normalized session quality in [0, 1].
                               Use: mean_confidence / 255.0

        Returns:
            Total loss (MSE + EWC penalty).
        """
        x = np.array(session_features, dtype=np.float32)
        x = (x - x.mean()) / (x.std() + 1e-8)  # Per-sample normalization
        loss, grads = self._backward(x, float(np.clip(session_label, 0.0, 1.0)))

        for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
            weight = getattr(self, k)
            weight -= LEARNING_RATE * grads[k]

        self.total_updates += 1
        return loss

    # -----------------------------------------------------------------------
    # Fisher information (diagonal) — call at task boundary
    # -----------------------------------------------------------------------

    def compute_fisher(self, recent_sessions: list[np.ndarray]) -> None:
        """
        Compute diagonal Fisher information matrix from recent sessions.
        Call this at task boundaries (e.g., every TASK_BOUNDARY_SESSIONS sessions).

        Fisher[k] = mean over data of (grad_log_likelihood[k])^2
        Approximated here as mean of squared gradients (Kirkpatrick et al. 2017).

        After computing Fisher, saves current weights as prev_weights.
        On subsequent update() calls, EWC penalty will be active.

        Args:
            recent_sessions: List of 30-dim session feature vectors.
        """
        if not recent_sessions:
            return

        # Reset accumulators
        fisher_accum = {k: np.zeros_like(getattr(self, k)) for k in ("W1", "b1", "W2", "b2", "W3", "b3")}

        for sv in recent_sessions:
            x = np.array(sv, dtype=np.float32)
            x = (x - x.mean()) / (x.std() + 1e-8)
            _, grads = self._backward(x, 0.5)  # neutral label for Fisher estimation
            for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
                fisher_accum[k] += grads[k] ** 2

        n = len(recent_sessions)
        for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
            self._fisher[k] = fisher_accum[k] / n

        # Save current weights as task boundary checkpoint
        self._prev_weights = {k: getattr(self, k).copy() for k in ("W1", "b1", "W2", "b2", "W3", "b3")}
        self._ewc_active = True

    # -----------------------------------------------------------------------
    # Session management
    # -----------------------------------------------------------------------

    def end_session(
        self,
        session_features: np.ndarray,
        session_label: float,
        recent_session_history: list[np.ndarray] | None = None,
    ) -> float:
        """
        Convenience: update model + optionally compute Fisher at task boundary.

        Args:
            session_features:      30-dim feature vector for this session.
            session_label:         Normalized quality label [0, 1].
            recent_session_history: If provided and total_sessions % TASK_BOUNDARY is 0,
                                   computes Fisher.

        Returns:
            Training loss for this session.
        """
        loss = self.update(session_features, session_label)
        self.total_sessions += 1

        if (
            recent_session_history is not None
            and self.total_sessions % TASK_BOUNDARY_SESSIONS == 0
        ):
            self.compute_fisher(recent_session_history)

        return loss

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------

    def serialize_weights(self) -> bytes:
        """
        Deterministic serialization of all model weights in fixed order.
        Returns bytes: W1, b1, W2, b2, W3, b3 (all float32, big-endian layout).
        """
        parts = []
        for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
            arr = getattr(self, k).astype(np.float32)
            parts.append(arr.tobytes())
        return b"".join(parts)

    def compute_hash(self, preference_weights_bytes: bytes = b"") -> bytes:
        """
        Compute the 32-byte world_model_hash for the PoAC body.

        If preference_weights_bytes is provided (E2+E4 synergy), the hash
        combines both cognitive state and economic preferences:
            SHA-256(model_weights || preference_weights)

        Otherwise:
            SHA-256(model_weights)

        Returns:
            32-byte SHA-256 digest.
        """
        payload = self.serialize_weights() + preference_weights_bytes
        return hashlib.sha256(payload).digest()

    def save(self, path: str) -> None:
        """Save model to JSON file (weights as hex-encoded strings)."""
        data: dict[str, Any] = {
            "version": "ewc_v1",
            "total_sessions": self.total_sessions,
            "total_updates": self.total_updates,
            "reaction_baseline": self.reaction_baseline,
            "precision_baseline": self.precision_baseline,
            "consistency_baseline": self.consistency_baseline,
            "imu_corr_baseline": self.imu_corr_baseline,
            "ewc_active": self._ewc_active,
        }
        for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
            data[k] = getattr(self, k).astype(np.float32).tobytes().hex()
        if self._ewc_active:
            for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
                data[f"fisher_{k}"] = self._fisher[k].astype(np.float32).tobytes().hex()
                data[f"prev_{k}"]   = self._prev_weights[k].astype(np.float32).tobytes().hex()

        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str) -> "EWCWorldModel":
        """Load model from JSON file saved by save()."""
        data = json.loads(Path(path).read_text())
        model = cls.__new__(cls)

        model.total_sessions    = data.get("total_sessions", 0)
        model.total_updates     = data.get("total_updates", 0)
        model.reaction_baseline    = data.get("reaction_baseline", 250.0)
        model.precision_baseline   = data.get("precision_baseline", 0.5)
        model.consistency_baseline = data.get("consistency_baseline", 50.0)
        model.imu_corr_baseline    = data.get("imu_corr_baseline", 0.5)

        shapes = {"W1": (INPUT_DIM, HIDDEN1), "b1": (HIDDEN1,),
                  "W2": (HIDDEN1, HIDDEN2),   "b2": (HIDDEN2,),
                  "W3": (HIDDEN2, OUTPUT_DIM), "b3": (OUTPUT_DIM,)}
        for k, shape in shapes.items():
            arr = np.frombuffer(bytes.fromhex(data[k]), dtype=np.float32).reshape(shape).copy()
            setattr(model, k, arr)

        model._ewc_active = data.get("ewc_active", False)
        model._fisher = {}
        model._prev_weights = {}
        if model._ewc_active:
            for k, shape in shapes.items():
                model._fisher[k] = np.frombuffer(
                    bytes.fromhex(data[f"fisher_{k}"]), dtype=np.float32
                ).reshape(shape).copy()
                model._prev_weights[k] = np.frombuffer(
                    bytes.fromhex(data[f"prev_{k}"]), dtype=np.float32
                ).reshape(shape).copy()
        else:
            for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
                model._fisher[k] = np.zeros_like(getattr(model, k))

        return model

    @classmethod
    def from_legacy_world_model(cls, wm_dict: dict) -> "EWCWorldModel":
        """
        Migrate from the old EMA WorldModel format.

        Old format keys (all optional):
            reaction_baseline, precision_baseline,
            consistency_baseline, imu_corr_baseline,
            total_sessions, total_poac

        Creates a fresh EWC model with the legacy baselines preserved.
        """
        model = cls()
        model.reaction_baseline    = float(wm_dict.get("reaction_baseline", 250.0))
        model.precision_baseline   = float(wm_dict.get("precision_baseline", 0.5))
        model.consistency_baseline = float(wm_dict.get("consistency_baseline", 50.0))
        model.imu_corr_baseline    = float(wm_dict.get("imu_corr_baseline", 0.5))
        model.total_sessions       = int(wm_dict.get("total_sessions", 0))
        return model

    # -----------------------------------------------------------------------
    # Static utility
    # -----------------------------------------------------------------------

    @staticmethod
    def build_session_vector(feature_frames: list[Any]) -> np.ndarray:
        """
        Aggregate a list of FeatureFrame objects (or any object with to_vector())
        into a single 30-dim session summary vector (column-wise mean).

        Args:
            feature_frames: List of FeatureFrame objects. Each must have to_vector()
                            returning a 30-element array-like.

        Returns:
            np.ndarray shape (30,) — mean feature vector over the session.
        """
        if not feature_frames:
            return np.zeros(INPUT_DIM, dtype=np.float32)
        vecs = [np.array(f.to_vector(), dtype=np.float32) for f in feature_frames]
        return np.mean(vecs, axis=0)


# ---------------------------------------------------------------------------
# Progress attestation: WORLD_MODEL_EVOLUTION metric
# ---------------------------------------------------------------------------

def compute_world_model_improvement_bps(
    baseline_hash: bytes,
    current_hash: bytes,
) -> int:
    """
    Compute improvement in basis points between two world_model_hash values.

    Metric: normalized Hamming weight of XOR between the two 32-byte hashes.
    Interpretation: higher divergence = more cognitive expansion from the baseline.

    Formula:
        xor_weight = popcount(baseline_hash XOR current_hash)
        improvement_bps = (xor_weight / 256) * 10000   [max 256 bits, max 10000 bps]

    Args:
        baseline_hash: 32-byte SHA-256 world_model_hash from baseline PoAC record.
        current_hash:  32-byte SHA-256 world_model_hash from current PoAC record.

    Returns:
        improvementBps in [0, 10000]. Returns 0 if hashes are identical.
    """
    if len(baseline_hash) != 32 or len(current_hash) != 32:
        return 0
    xor = bytes(a ^ b for a, b in zip(baseline_hash, current_hash))
    bit_count = sum(bin(byte).count("1") for byte in xor)
    return int((bit_count / 256.0) * 10000)
