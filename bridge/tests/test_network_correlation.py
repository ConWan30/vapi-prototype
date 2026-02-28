"""
Phase 26 — NetworkCorrelationDetector Tests

TestDistanceMatrix (3):
1. build_distance_matrix → shape (N, N), diagonal zeros
2. None from compute_distance → epsilon*10 fill, no crash
3. symmetric: mat[i,j] == mat[j,i]

TestDBSCAN (5):
4. 3 devices with dist < epsilon → 1 cluster of 3
5. 1 outlier (dist > epsilon from all) → not in any cluster
6. 2 tight clusters of 3 → 2 separate clusters
7. < min_samples devices → [] returned
8. all distances = 0 → 1 cluster containing all N devices

TestFarmDetection (4):
9.  farm_suspicion_score = 0 for noise device
10. farm_suspicion_score > 0 for device in dense cluster
11. get_flagged_clusters excludes clusters with avg_d >= epsilon*0.5
12. tight 3-device cluster → is_flagged=True, suspicion_score > 0.5
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from vapi_bridge.network_correlation_detector import NetworkCorrelationDetector, DeviceCluster

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_detector(devices, distances, epsilon=1.0, min_samples=3):
    """Build a NetworkCorrelationDetector with mocked store and prover.

    distances: dict (dev_i, dev_j) → float. Symmetric; missing pairs → None.
    """
    store = MagicMock()
    store.get_all_fingerprinted_devices.return_value = devices

    prover = MagicMock()
    def _compute_dist(a, b):
        return distances.get((a, b), distances.get((b, a), None))
    prover.compute_distance.side_effect = _compute_dist

    return NetworkCorrelationDetector(store, prover, epsilon=epsilon, min_samples=min_samples)


# ===========================================================================
# TestDistanceMatrix
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestDistanceMatrix(unittest.TestCase):

    def test_1_shape_and_diagonal(self):
        """build_distance_matrix → shape (N, N) with diagonal = 0."""
        devices = ["a", "b", "c"]
        det = _make_detector(devices, {("a", "b"): 0.5, ("a", "c"): 0.8, ("b", "c"): 0.3})
        mat = det.build_distance_matrix(devices)
        self.assertEqual(mat.shape, (3, 3))
        for i in range(3):
            self.assertAlmostEqual(mat[i, i], 0.0)

    def test_2_none_distance_gets_epsilon_fill(self):
        """None from compute_distance → epsilon*10 fill, no crash."""
        devices = ["a", "b"]
        det = _make_detector(devices, {})  # no known distances → all None
        mat = det.build_distance_matrix(devices)
        self.assertEqual(mat.shape, (2, 2))
        # Off-diagonal should be epsilon * 10
        self.assertAlmostEqual(mat[0, 1], det.epsilon * 10)
        self.assertAlmostEqual(mat[1, 0], det.epsilon * 10)

    def test_3_symmetric(self):
        """Distance matrix must be symmetric: mat[i,j] == mat[j,i]."""
        devices = ["a", "b", "c", "d"]
        dists = {("a", "b"): 0.5, ("a", "c"): 0.9, ("b", "c"): 0.2, ("a", "d"): 1.5}
        det = _make_detector(devices, dists)
        mat = det.build_distance_matrix(devices)
        for i in range(4):
            for j in range(4):
                self.assertAlmostEqual(mat[i, j], mat[j, i], places=10)


# ===========================================================================
# TestDBSCAN
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestDBSCAN(unittest.TestCase):

    def test_4_three_close_devices_one_cluster(self):
        """3 devices with pairwise dist < epsilon → 1 cluster of 3."""
        devices = ["a", "b", "c"]
        dists = {("a", "b"): 0.3, ("a", "c"): 0.4, ("b", "c"): 0.3}
        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        mat = det.build_distance_matrix(devices)
        clusters = det._dbscan(mat, devices)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(set(clusters[0]), {"a", "b", "c"})

    def test_5_outlier_not_in_any_cluster(self):
        """1 outlier (dist > epsilon from all) → not in any cluster."""
        devices = ["a", "b", "c", "outlier"]
        dists = {
            ("a", "b"): 0.3, ("a", "c"): 0.4, ("b", "c"): 0.3,
            ("a", "outlier"): 5.0, ("b", "outlier"): 5.0, ("c", "outlier"): 5.0,
        }
        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        mat = det.build_distance_matrix(devices)
        clusters = det._dbscan(mat, devices)
        # outlier should not appear in any cluster
        all_members = [d for cluster in clusters for d in cluster]
        self.assertNotIn("outlier", all_members)

    def test_6_two_tight_clusters(self):
        """2 tight clusters of 3 → 2 separate clusters."""
        devices = ["a1", "a2", "a3", "b1", "b2", "b3"]
        dists = {}
        # Within cluster A: dist 0.2; within cluster B: dist 0.2
        # Between clusters: dist 5.0
        for x in ["a1", "a2", "a3"]:
            for y in ["a1", "a2", "a3"]:
                if x < y:
                    dists[(x, y)] = 0.2
        for x in ["b1", "b2", "b3"]:
            for y in ["b1", "b2", "b3"]:
                if x < y:
                    dists[(x, y)] = 0.2
        for x in ["a1", "a2", "a3"]:
            for y in ["b1", "b2", "b3"]:
                dists[(x, y)] = 5.0

        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        mat = det.build_distance_matrix(devices)
        clusters = det._dbscan(mat, devices)
        self.assertEqual(len(clusters), 2)

    def test_7_fewer_than_min_samples_returns_empty(self):
        """< min_samples devices → [] returned from detect_clusters."""
        devices = ["a", "b"]  # only 2, min_samples=3
        store = MagicMock()
        store.get_all_fingerprinted_devices.return_value = devices
        prover = MagicMock()
        prover.compute_distance.return_value = 0.1
        det = NetworkCorrelationDetector(store, prover, epsilon=1.0, min_samples=3)
        clusters = det.detect_clusters()
        self.assertEqual(clusters, [])

    def test_8_all_distances_zero_one_cluster(self):
        """All pairwise distances = 0 → 1 cluster containing all N devices."""
        devices = ["a", "b", "c", "d"]
        dists = {(x, y): 0.0 for x in devices for y in devices if x < y}
        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        mat = det.build_distance_matrix(devices)
        clusters = det._dbscan(mat, devices)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(set(clusters[0]), set(devices))


# ===========================================================================
# TestFarmDetection
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestFarmDetection(unittest.TestCase):

    def test_9_farm_score_zero_for_noise_device(self):
        """farm_suspicion_score = 0.0 for a noise device (not in any cluster)."""
        devices = ["noise", "a", "b", "c"]
        store = MagicMock()
        store.get_all_fingerprinted_devices.return_value = devices
        prover = MagicMock()

        def _d(x, y):
            # noise is far from everything; a,b,c are close to each other
            if "noise" in (x, y):
                return 5.0
            return 0.2

        prover.compute_distance.side_effect = _d
        det = NetworkCorrelationDetector(store, prover, epsilon=1.0, min_samples=2)
        score = det.get_farm_suspicion_score("noise")
        self.assertAlmostEqual(score, 0.0)

    def test_10_farm_score_positive_in_dense_cluster(self):
        """farm_suspicion_score > 0 for device in a dense cluster."""
        devices = ["a", "b", "c"]
        dists = {("a", "b"): 0.1, ("a", "c"): 0.1, ("b", "c"): 0.1}
        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        score = det.get_farm_suspicion_score("a")
        self.assertGreater(score, 0.0)

    def test_11_flagged_clusters_excludes_loose_clusters(self):
        """get_flagged_clusters excludes clusters with avg_d >= epsilon*0.5."""
        devices = ["a", "b", "c"]
        # avg_d = 0.6 which is >= epsilon*0.5 = 0.5 → NOT flagged
        dists = {("a", "b"): 0.6, ("a", "c"): 0.6, ("b", "c"): 0.6}
        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        flagged = det.get_flagged_clusters()
        # Cluster has avg_d=0.6 >= epsilon*0.5=0.5 → is_flagged=False
        for c in flagged:
            self.assertTrue(c.is_flagged)
        # Verify this specific cluster is NOT flagged
        all_clusters = det.detect_clusters()
        if all_clusters:
            self.assertFalse(all_clusters[0].is_flagged)

    def test_12_tight_cluster_is_flagged(self):
        """Tight 3-device cluster (avg_d << epsilon*0.5) → is_flagged=True, score > 0.5."""
        devices = ["a", "b", "c"]
        dists = {("a", "b"): 0.05, ("a", "c"): 0.05, ("b", "c"): 0.05}
        det = _make_detector(devices, dists, epsilon=1.0, min_samples=2)
        flagged = det.get_flagged_clusters()
        self.assertEqual(len(flagged), 1)
        self.assertTrue(flagged[0].is_flagged)
        self.assertGreater(flagged[0].farm_suspicion_score, 0.5)


if __name__ == "__main__":
    unittest.main()
