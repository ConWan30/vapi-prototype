"""
test_network_correlation_edge_cases.py — Edge case tests for NetworkCorrelationDetector.

Covers degenerate input conditions that the BFS DBSCAN implementation must handle
gracefully without raising exceptions or producing incorrect cluster counts.

Tested scenarios:
  1. Empty device set → no clusters
  2. Single device → no clusters (below min_samples=3)
  3. Two devices → no clusters (below min_samples=3)
  4. All devices identical (zero inter-device distance) → single giant cluster

API note: NetworkCorrelationDetector.__init__(store, prover, epsilon=1.0, min_samples=3)
  - store.get_all_fingerprinted_devices() returns a list of device_id strings
  - prover.compute_distance(id_a, id_b) returns a float distance (or raises)
  - detect_clusters() fetches from store, builds matrix, runs BFS DBSCAN
"""

import os
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Import — use path relative to this file so it works on all platforms
# ---------------------------------------------------------------------------
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_BRIDGE_DIR = os.path.dirname(_THIS_DIR)   # bridge/
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

try:
    from vapi_bridge.network_correlation_detector import NetworkCorrelationDetector
    _IMPORT_OK  = True
    _IMPORT_ERR = ""
except Exception as exc:
    _IMPORT_OK  = False
    _IMPORT_ERR = str(exc)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_store(device_ids: list) -> object:
    """Return a mock store whose get_all_fingerprinted_devices returns `device_ids`."""
    store = types.SimpleNamespace()
    store.get_all_fingerprinted_devices = lambda: list(device_ids)
    return store


def _make_prover(distances: dict) -> object:
    """
    Return a mock prover with compute_distance(id_a, id_b) -> float.

    `distances` maps frozenset({id_a, id_b}) → float.
    If a pair is not in the dict, returns None (→ epsilon * 10 fill by detector).
    """
    prover = types.SimpleNamespace()

    def compute_distance(id_a, id_b):
        return distances.get(frozenset({id_a, id_b}))

    prover.compute_distance = compute_distance
    return prover


def _make_zero_distance_prover(device_ids: list) -> object:
    """Return a prover where all pairwise distances are 0 (identical devices)."""
    distances = {
        frozenset({a, b}): 0.0
        for i, a in enumerate(device_ids)
        for j, b in enumerate(device_ids)
        if i < j
    }
    return _make_prover(distances)


def _make_far_distance_prover(device_ids: list) -> object:
    """Return a prover where all pairwise distances are 10.0 (far apart)."""
    distances = {
        frozenset({a, b}): 10.0
        for i, a in enumerate(device_ids)
        for j, b in enumerate(device_ids)
        if i < j
    }
    return _make_prover(distances)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(_IMPORT_OK, f"NetworkCorrelationDetector import failed: {_IMPORT_ERR}")
class TestNetworkCorrelationEdgeCases(unittest.TestCase):
    """
    Edge case tests for NetworkCorrelationDetector.detect_clusters().

    The detector uses a manual BFS DBSCAN with configurable epsilon and min_samples.
    Default: epsilon=1.0 (L2 distance threshold), min_samples=3 (min cluster size).
    Devices with pairwise distance < epsilon are considered neighbors.
    """

    def _detector(self, device_ids, distances_or_prover=None, epsilon=1.0, min_samples=3):
        """Construct a detector with given device IDs and optional distance map."""
        store = _make_store(device_ids)
        if distances_or_prover is None:
            prover = _make_far_distance_prover(device_ids)
        elif isinstance(distances_or_prover, dict):
            prover = _make_prover(distances_or_prover)
        else:
            prover = distances_or_prover
        return NetworkCorrelationDetector(store, prover, epsilon=epsilon, min_samples=min_samples)

    def test_1_empty_device_set_returns_no_clusters(self):
        """With no devices, detect_clusters must return an empty list without raising."""
        detector = self._detector([])
        result = detector.detect_clusters()
        self.assertIsInstance(result, list,
                              "detect_clusters() must return a list for empty input.")
        self.assertEqual(len(result), 0,
                         "Empty device set must produce zero clusters.")

    def test_2_single_device_returns_no_clusters(self):
        """
        A single device cannot form a cluster (min_samples=3).
        Must return an empty list, not raise an exception.

        Why: BFS DBSCAN with min_samples=3 requires at least 3 devices in a
        neighborhood to form a cluster. One device has 0 neighbors → noise → excluded.
        """
        detector = self._detector(["device_aabb"])
        result = detector.detect_clusters()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0,
                         "Single device cannot form a cluster (min_samples=3). "
                         "Expected 0 clusters.")

    def test_3_two_devices_returns_no_clusters(self):
        """
        Two devices very close together (distance=0 < epsilon=1.0) still cannot form
        a cluster because min_samples=3 requires 3 members.

        This is a common off-by-one edge case in DBSCAN implementations where the
        seed device counts toward min_samples. Test explicitly catches both
        interpretations: either 2 devices do NOT form a cluster (correct DBSCAN),
        or they do (off-by-one). We assert the correct behavior.
        """
        devices = ["device_aa", "device_bb"]
        distances = {frozenset({"device_aa", "device_bb"}): 0.0}
        detector = self._detector(devices, distances)
        result = detector.detect_clusters()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0,
                         "Two devices (even with distance=0) cannot form a cluster "
                         "(min_samples=3 requires 3 members). "
                         "If this fails, the DBSCAN seed counting is off-by-one.")

    def test_4_all_devices_identical_forms_one_cluster(self):
        """
        10 devices with zero pairwise distance → all within epsilon → one cluster.

        Why: This tests the degenerate case where all devices appear identical
        (same HID data — a clear farming signal). The detector must produce at
        least one cluster containing all 10 devices, and that cluster must be
        flagged (avg_intra_distance = 0 < epsilon * 0.5).

        The is_flagged attribute should be True.
        farm_suspicion_score should be 1.0 (maximum for a large identical cluster).
        """
        devices = [f"device_{i:02d}" for i in range(10)]
        prover = _make_zero_distance_prover(devices)
        detector = self._detector(devices, prover)
        result = detector.detect_clusters()
        self.assertIsInstance(result, list)

        # At least one cluster must exist
        self.assertGreaterEqual(len(result), 1,
                                f"10 identical devices must form at least 1 cluster. "
                                f"Got {len(result)} clusters.")

        # All 10 devices must be accounted for across all clusters
        total_devices_in_clusters = sum(len(c.device_ids) for c in result)
        self.assertEqual(total_devices_in_clusters, 10,
                         f"All 10 devices should be in clusters. "
                         f"Got {total_devices_in_clusters} across {len(result)} cluster(s).")

        # At least one cluster must be flagged (avg_distance=0 < epsilon*0.5=0.5)
        flagged = [c for c in result if c.is_flagged]
        self.assertGreaterEqual(len(flagged), 1,
                                "Cluster of 10 identical devices must be flagged as suspicious "
                                "(avg_intra_distance=0 < epsilon*0.5=0.5).")

        # Farm suspicion score should be at or near maximum (1.0) for giant cluster
        max_score = max(c.farm_suspicion_score for c in result)
        self.assertGreaterEqual(max_score, 0.9,
                                f"Identical 10-device cluster farm_suspicion_score={max_score:.3f} "
                                f"should be near 1.0.")

        print(f"\n[test_4] {len(result)} cluster(s); "
              f"{total_devices_in_clusters} devices; "
              f"{len(flagged)} flagged; "
              f"max_score={max_score:.3f}. PASS.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
