"""
Phase 26 — NetworkCorrelationDetector.

Builds a pairwise biometric distance matrix across all fingerprinted devices and
runs a manual BFS DBSCAN to identify organized bot farms as correlated clusters.

Pure numpy. Read-only. Reuses ContinuityProver.compute_distance() for biometric
distance measurement.
"""

import time
from collections import deque
from dataclasses import dataclass, field

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


@dataclass
class DeviceCluster:
    cluster_id: int
    device_ids: list
    avg_intra_distance: float
    farm_suspicion_score: float     # [0, 1]
    is_flagged: bool


class NetworkCorrelationDetector:
    """Density-based clustering of devices by biometric similarity.

    Reuses ContinuityProver.compute_distance() for pairwise L4 Mahalanobis distance.
    epsilon and min_samples follow DBSCAN convention.
    """

    def __init__(self, store, prover, epsilon: float = 1.0, min_samples: int = 3):
        self._store = store
        self._prover = prover
        self.epsilon = epsilon
        self.min_samples = min_samples

    def build_distance_matrix(self, device_ids: list) -> "np.ndarray":
        """Build N×N symmetric distance matrix.

        Diagonal = 0. Missing/failed compute_distance → epsilon * 10 fill.
        """
        if not HAS_NUMPY:
            n = len(device_ids)
            return [[0.0] * n for _ in range(n)]

        n = len(device_ids)
        mat = np.full((n, n), self.epsilon * 10, dtype=np.float64)
        np.fill_diagonal(mat, 0.0)

        for i in range(n):
            for j in range(i + 1, n):
                try:
                    d = self._prover.compute_distance(device_ids[i], device_ids[j])
                    val = d if d is not None else self.epsilon * 10
                except Exception:
                    val = self.epsilon * 10
                mat[i, j] = val
                mat[j, i] = val  # symmetric

        return mat

    def _dbscan(self, dist_mat, device_ids: list) -> list:
        """Manual BFS DBSCAN. Returns list of cluster groups (noise excluded).

        Uses visited_in_queue set to prevent O(N²) queue growth in dense clusters.
        Noise devices (label=-1) can be absorbed as border points during core
        expansion — correct DBSCAN behaviour.
        """
        n = len(device_ids)
        if n == 0:
            return []

        labels = [-1] * n  # -1 = unvisited/noise
        cluster_id = 0

        for i in range(n):
            if labels[i] != -1:
                continue

            # Find neighbours within epsilon
            neighbors = [
                j for j in range(n)
                if j != i and dist_mat[i, j] <= self.epsilon
            ]

            if len(neighbors) < self.min_samples - 1:
                # Not a core point — remains noise for now
                continue

            # Core point: start a new cluster
            labels[i] = cluster_id
            queue: deque = deque()
            visited_in_queue: set = {i}
            for nb in neighbors:
                if nb not in visited_in_queue:
                    queue.append(nb)
                    visited_in_queue.add(nb)

            while queue:
                cur = queue.popleft()
                if labels[cur] == -1:
                    labels[cur] = cluster_id  # absorb noise as border point

                if labels[cur] != cluster_id:
                    continue  # already in another cluster (border point of multiple)

                cur_neighbors = [
                    j for j in range(n)
                    if j != cur and dist_mat[cur, j] <= self.epsilon
                ]
                if len(cur_neighbors) >= self.min_samples - 1:
                    for nb in cur_neighbors:
                        if nb not in visited_in_queue:
                            queue.append(nb)
                            visited_in_queue.add(nb)

            cluster_id += 1

        # Collect clusters
        clusters: list = []
        for cid in range(cluster_id):
            members = [device_ids[i] for i in range(n) if labels[i] == cid]
            if members:
                clusters.append(members)

        return clusters

    def detect_clusters(self) -> list:
        """Build distance matrix and detect device clusters."""
        if not HAS_NUMPY:
            return []

        device_ids = self._store.get_all_fingerprinted_devices()
        if len(device_ids) < self.min_samples:
            return []

        dist_mat = self.build_distance_matrix(device_ids)
        raw_clusters = self._dbscan(dist_mat, device_ids)

        result = []
        for cid, members in enumerate(raw_clusters):
            # Compute average intra-cluster distance
            indices = [device_ids.index(d) for d in members]
            intra_dists = []
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    intra_dists.append(dist_mat[indices[i], indices[j]])
            avg_d = float(np.mean(intra_dists)) if intra_dists else 0.0

            cluster_size = len(members)
            farm_score = min(
                1.0,
                (cluster_size - 2) / 5.0 + (self.epsilon - avg_d) / self.epsilon
            )
            farm_score = max(0.0, farm_score)

            is_flagged = (
                cluster_size >= self.min_samples and avg_d < self.epsilon * 0.5
            )

            result.append(DeviceCluster(
                cluster_id=cid,
                device_ids=members,
                avg_intra_distance=round(avg_d, 4),
                farm_suspicion_score=round(farm_score, 4),
                is_flagged=is_flagged,
            ))

        return result

    def get_farm_suspicion_score(self, device_id: str) -> float:
        """Return farm_suspicion_score for a specific device, 0.0 if not in any cluster."""
        for cluster in self.detect_clusters():
            if device_id in cluster.device_ids:
                return cluster.farm_suspicion_score
        return 0.0

    def get_flagged_clusters(self) -> list:
        """Return only clusters marked is_flagged=True."""
        return [c for c in self.detect_clusters() if c.is_flagged]
