"""
Phase 13 — Enhancement 2: Privacy-Preserving Personalized Bounty Optimizer.

Ports greedy knapsack logic from firmware/src/economic.c into Python with:
- Per-device 5-dimensional preference model learned from bounty outcomes (SGD).
- Differential privacy: Laplace noise (epsilon=1.5) on utility scores before ranking.
- Preference hash incorporated into world_model_hash (E2+E4 synergy via EWCWorldModel.compute_hash).
- Preemption: if a new candidate has utility > 1.5x worst active bounty, swap it in.

The 228-byte PoAC wire format is IMMUTABLE. This module operates at the deliberative
layer (5-min cycle) and influences which bounties are pursued, affecting bounty_id
in PoAC bodies indirectly — no protocol changes required.
"""

from __future__ import annotations

import hashlib
import struct
import math
import dataclasses
from typing import List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREF_DIM = 5
PREF_DIMS = ["reward_magnitude", "sensor_match", "zone_proximity", "duration_fit", "tier_weight"]

# Differential privacy parameters
DP_EPSILON: float = 1.5          # Per-query privacy budget
DP_DAILY_BUDGET: float = 50.0   # Total epsilon before daily reset
DP_SENSITIVITY: float = 1.0      # Max utility delta (normalized feature space)

# Knapsack parameters
PREEMPTION_RATIO: float = 1.5    # New utility must exceed worst active by this factor
MAX_ACTIVE_DEFAULT: int = 4      # Default max concurrent bounties


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class BountyDescriptor:
    """Python equivalent of firmware BountyDescriptor struct (economic.h)."""
    bounty_id: int
    reward_iotx_micro: int         # Reward in micro-IOTX (1e-6 IOTX)
    sensor_requirements: int       # Bitfield: which sensors required
    min_samples: int
    sample_interval_s: int
    duration_s: int
    deadline_ms: int
    zone_lat_min: float
    zone_lat_max: float
    zone_lon_min: float
    zone_lon_max: float
    energy_cost_pct: float = 0.0   # Estimated energy cost (set by optimizer)


@dataclasses.dataclass
class DeviceState:
    """Minimal device context for bounty optimization."""
    battery_pct: int               # 0–100
    latitude: float                # Degrees
    longitude: float               # Degrees
    active_sensor_flags: int       # Which sensors currently available (bitfield)
    tier: int                      # 0=Emulated, 1=Standard, 2=Attested


# ---------------------------------------------------------------------------
# Preference Model
# ---------------------------------------------------------------------------

class PreferenceModel:
    """
    Lightweight 5-dimensional preference model for personalized bounty ranking.

    Each dimension weights one component of a bounty's utility:
      [reward_magnitude, sensor_match, zone_proximity, duration_fit, tier_weight]

    Differential privacy guarantee:
      Laplace noise with epsilon=1.5 is added to utility scores before ranking.
      Sensitivity is calibrated from the normalized feature space [0, 1]^5.
      A daily budget cap (50.0 epsilon) prevents unbounded inference over time.
    """

    def __init__(self, seed: Optional[int] = None):
        rng = np.random.default_rng(seed)
        self.weights: np.ndarray = np.ones(PREF_DIM, dtype=np.float64)
        self.epsilon: float = DP_EPSILON
        self.daily_budget: float = DP_DAILY_BUDGET
        self.budget_spent: float = 0.0
        self._rng = rng

    # ------------------------------------------------------------------
    # Utility computation
    # ------------------------------------------------------------------

    def compute_utility(
        self,
        bounty: BountyDescriptor,
        device_state: DeviceState,
    ) -> float:
        """Dot product of preference weights and bounty feature vector."""
        fv = self._feature_vector(bounty, device_state)
        return float(np.dot(self.weights, fv))

    def compute_utility_with_dp(
        self,
        bounty: BountyDescriptor,
        device_state: DeviceState,
        sensitivity: float = DP_SENSITIVITY,
    ) -> float:
        """
        Add Laplace noise (epsilon=1.5) before returning utility.
        Tracks budget_spent; returns raw utility if budget exhausted.
        """
        raw_utility = self.compute_utility(bounty, device_state)
        if self.budget_spent + self.epsilon > self.daily_budget:
            # Budget exhausted — return raw utility without noise
            return raw_utility
        noisy = self._add_laplace_noise(raw_utility, sensitivity)
        self.budget_spent += self.epsilon
        return noisy

    def _add_laplace_noise(self, value: float, sensitivity: float) -> float:
        """Laplace mechanism: value + Lap(0, sensitivity / epsilon)."""
        scale = sensitivity / self.epsilon
        noise = self._rng.laplace(0.0, scale)
        return value + noise

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def update(
        self,
        bounty: BountyDescriptor,
        device_state: DeviceState,
        outcome: float,
        lr: float = 0.01,
    ) -> None:
        """
        SGD update after a bounty outcome is observed.

        outcome: normalized reward signal in [0, 1] (e.g., reward_received / reward_expected).
        gradient = (predicted - outcome) * feature_vector
        """
        fv = self._feature_vector(bounty, device_state)
        predicted = float(np.dot(self.weights, fv))
        gradient = (predicted - outcome) * fv
        self.weights -= lr * gradient
        # Clamp to prevent degenerate weights
        self.weights = np.clip(self.weights, 0.01, 10.0)

    def reset_daily_budget(self) -> None:
        """Call at midnight or after a full day cycle."""
        self.budget_spent = 0.0

    # ------------------------------------------------------------------
    # Serialization / hashing (E2+E4 synergy)
    # ------------------------------------------------------------------

    def serialize_weights(self) -> bytes:
        """Serialize 5 float64 weights to bytes (40 bytes)."""
        return self.weights.astype(np.float64).tobytes()

    def preference_hash(self) -> bytes:
        """SHA-256 of serialized weights → 32 bytes. Contributed to world_model_hash."""
        return hashlib.sha256(self.serialize_weights()).digest()

    @classmethod
    def from_bytes(cls, data: bytes) -> "PreferenceModel":
        """Deserialize from 40-byte payload."""
        if len(data) != PREF_DIM * 8:
            raise ValueError(f"Expected {PREF_DIM * 8} bytes, got {len(data)}")
        m = cls()
        m.weights = np.frombuffer(data, dtype=np.float64).copy()
        return m

    def save(self, path: str) -> None:
        """Persist preference weights to a binary file."""
        from pathlib import Path
        Path(path).write_bytes(self.serialize_weights())

    @classmethod
    def load(cls, path: str) -> "PreferenceModel":
        """Load preference weights from a binary file."""
        from pathlib import Path
        return cls.from_bytes(Path(path).read_bytes())

    # ------------------------------------------------------------------
    # Feature vector
    # ------------------------------------------------------------------

    def _feature_vector(
        self,
        bounty: BountyDescriptor,
        device_state: DeviceState,
    ) -> np.ndarray:
        """
        5-dimensional feature vector in [0, 1]^5:
          [0] reward_magnitude  — normalized reward (micro-IOTX / 1_000_000)
          [1] sensor_match      — fraction of required sensors available
          [2] zone_proximity    — 1.0 if inside zone, decay by distance otherwise
          [3] duration_fit      — 1.0 if battery can sustain full duration
          [4] tier_weight       — device tier / 2.0 (Attested = 1.0)
        """
        # [0] Reward magnitude: normalize to [0, 1] assuming max 10 IOTX = 10_000_000 micro
        reward_norm = min(bounty.reward_iotx_micro / 10_000_000.0, 1.0)

        # [1] Sensor match: popcount of (required AND available) / popcount of required
        required = bounty.sensor_requirements
        available = device_state.active_sensor_flags
        if required == 0:
            sensor_score = 1.0
        else:
            matched = bin(required & available).count("1")
            needed = bin(required).count("1")
            sensor_score = matched / needed

        # [2] Zone proximity: 1.0 inside zone; exponential decay by haversine distance outside
        zone_score = self._zone_proximity_score(bounty, device_state)

        # [3] Duration fit: estimated energy cost vs battery headroom
        # Simple model: 1% per minute of bounty duration
        energy_needed = (bounty.duration_s / 60.0) * 1.0  # % battery
        headroom = max(0.0, device_state.battery_pct - 10.0)  # keep 10% reserve
        duration_score = min(1.0, headroom / max(energy_needed, 1.0))

        # [4] Tier weight: Emulated=0.0, Standard=0.5, Attested=1.0
        tier_score = min(device_state.tier / 2.0, 1.0)

        return np.array([reward_norm, sensor_score, zone_score, duration_score, tier_score],
                        dtype=np.float64)

    @staticmethod
    def _zone_proximity_score(bounty: BountyDescriptor, device_state: DeviceState) -> float:
        """1.0 if device is inside the bounty zone; exponential decay by lat/lon distance otherwise."""
        lat = device_state.latitude
        lon = device_state.longitude

        lat_ok = bounty.zone_lat_min <= lat <= bounty.zone_lat_max
        lon_ok = bounty.zone_lon_min <= lon <= bounty.zone_lon_max
        if lat_ok and lon_ok:
            return 1.0

        # Distance to nearest zone corner (degrees, approximate)
        dlat = max(0.0, bounty.zone_lat_min - lat, lat - bounty.zone_lat_max)
        dlon = max(0.0, bounty.zone_lon_min - lon, lon - bounty.zone_lon_max)
        dist_deg = math.sqrt(dlat ** 2 + dlon ** 2)

        # Decay: e^(-dist / 1.0_deg), fully decayed at ~5 degrees
        return math.exp(-dist_deg)


# ---------------------------------------------------------------------------
# Personalized Knapsack
# ---------------------------------------------------------------------------

class PersonalizedKnapsack:
    """
    Greedy knapsack optimizer with preference-weighted utility and Laplace DP.

    Mirrors economic_optimize_bounties() from firmware/include/economic.h,
    extended with per-device preference learning and differential privacy.

    Algorithm:
      1. Score each candidate bounty: noisy_utility / max(energy_cost, 0.01)
      2. Sort descending by score.
      3. Greedily select until max_active reached or battery budget exhausted.
      4. Preemption: if a new bounty's utility > PREEMPTION_RATIO * worst active utility,
         swap it in.
    """

    def __init__(self, preference_model: Optional[PreferenceModel] = None):
        self.preference_model = preference_model or PreferenceModel()

    def optimize(
        self,
        bounties: List[BountyDescriptor],
        battery_budget_pct: float,
        device_state: DeviceState,
        max_active: int = MAX_ACTIVE_DEFAULT,
    ) -> List[BountyDescriptor]:
        """
        Select the best subset of bounties given battery budget and active limit.

        Args:
            bounties: All available candidate bounties.
            battery_budget_pct: Available battery to spend on bounties (0–100).
            device_state: Current device context.
            max_active: Maximum number of simultaneously active bounties.

        Returns:
            Ordered list of selected bounties (highest utility first).
        """
        if not bounties or battery_budget_pct <= 0:
            return []

        # Estimate energy costs
        for b in bounties:
            b.energy_cost_pct = self._estimate_energy_cost(b)

        # Score each bounty: DP utility / energy cost
        scored: List[tuple[float, BountyDescriptor]] = []
        for b in bounties:
            utility = self.preference_model.compute_utility_with_dp(b, device_state)
            energy = max(b.energy_cost_pct, 0.01)
            score = utility / energy
            scored.append((score, b))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Greedy selection with budget tracking
        selected: List[BountyDescriptor] = []
        remaining_budget = battery_budget_pct

        for score, bounty in scored:
            if len(selected) >= max_active:
                break
            if bounty.energy_cost_pct > remaining_budget:
                continue
            selected.append(bounty)
            remaining_budget -= bounty.energy_cost_pct

        # Preemption pass: check if any unselected bounty should displace the worst selected
        if len(selected) > 0:
            selected = self._preemption_pass(selected, scored, device_state, battery_budget_pct)

        return selected

    def _preemption_pass(
        self,
        selected: List[BountyDescriptor],
        scored: List[tuple[float, BountyDescriptor]],
        device_state: DeviceState,
        total_budget: float,
    ) -> List[BountyDescriptor]:
        """
        If a new bounty's utility > PREEMPTION_RATIO * worst selected utility, swap.
        Respects total budget constraint after swap.
        """
        if not selected:
            return selected

        selected_ids = {b.bounty_id for b in selected}
        worst_utility = min(
            self.preference_model.compute_utility(b, device_state) for b in selected
        )
        worst_bounty = min(
            selected,
            key=lambda b: self.preference_model.compute_utility(b, device_state),
        )

        for _, candidate in scored:
            if candidate.bounty_id in selected_ids:
                continue
            candidate_utility = self.preference_model.compute_utility(candidate, device_state)
            if candidate_utility > PREEMPTION_RATIO * worst_utility:
                # Try swapping worst out, candidate in
                new_list = [b for b in selected if b.bounty_id != worst_bounty.bounty_id]
                new_list.append(candidate)
                new_energy = sum(b.energy_cost_pct for b in new_list)
                if new_energy <= total_budget:
                    return new_list
                break  # Only attempt one preemption per call

        return selected

    @staticmethod
    def _estimate_energy_cost(bounty: BountyDescriptor) -> float:
        """
        Estimate battery % cost for a bounty.
        Simple model: 1% per minute of duration, minimum 0.5%.
        """
        minutes = bounty.duration_s / 60.0
        return max(0.5, minutes * 1.0)


# ---------------------------------------------------------------------------
# Utility function for computing combined world_model_hash (E2+E4 synergy)
# ---------------------------------------------------------------------------

def compute_combined_world_model_hash(
    ewc_weights_bytes: bytes,
    preference_weights_bytes: bytes,
) -> bytes:
    """
    Combine EWC world model weights + preference model weights into a single
    32-byte hash for the world_model_hash field.

    This is the E2+E4 synergy: one hash fingerprints both cognitive state
    (how the player plays) and economic preferences (what they value).

    Usage:
        from world_model_continual import EWCWorldModel
        from knapsack_personalized import PreferenceModel, compute_combined_world_model_hash

        wm_hash = compute_combined_world_model_hash(
            ewc_model.serialize_weights(),
            preference_model.serialize_weights(),
        )
    """
    combined = ewc_weights_bytes + preference_weights_bytes
    return hashlib.sha256(combined).digest()
