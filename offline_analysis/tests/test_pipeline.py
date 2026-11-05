import csv
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from offline_analysis import pipeline


class PipelineTests(unittest.TestCase):
    def test_convert_stanford_annotations_keeps_pedestrians_and_centers_bbox(self):
        lines = [
            '7 10 20 30 60 100 0 1 0 "Pedestrian"',
            '8 0 0 10 10 101 0 0 0 "Biker"',
        ]

        rows = pipeline.convert_stanford_annotation_lines(lines, fps=30.0)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scene"], "deathCircle")
        self.assertEqual(rows[0]["video_id"], "video0")
        self.assertEqual(rows[0]["decision_mode"], "real")
        self.assertEqual(rows[0]["agent_id"], "real_7")
        self.assertEqual(rows[0]["x_img"], 20.0)
        self.assertEqual(rows[0]["y_img"], 40.0)
        self.assertAlmostEqual(rows[0]["time_ms"], 100 / 30.0 * 1000.0)

    def test_standardize_unity_trace_adds_required_columns_and_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["time_ms", "agent_id", "x_world", "y_world", "decision_mode"],
                )
                writer.writeheader()
                writer.writerow({"time_ms": "0", "agent_id": "a1", "x_world": "0", "y_world": "0", "decision_mode": "llm"})
                writer.writerow({"time_ms": "1000", "agent_id": "a1", "x_world": "3", "y_world": "4", "decision_mode": "llm"})

            rows = pipeline.standardize_unity_trace(path, experiment_id="exp_test", run_id="1", group_mode="llm")

        self.assertEqual(pipeline.UNITY_COLUMNS, list(rows[0].keys()))
        self.assertEqual(rows[0]["scene"], "unity_campus")
        self.assertEqual(rows[0]["decision_source"], "llm")
        self.assertEqual(rows[0]["frame"], 0)
        self.assertEqual(rows[1]["frame"], 1)
        self.assertAlmostEqual(rows[1]["speed"], 5.0)

    def test_distribution_metrics_for_identical_matrices_are_perfect(self):
        matrix = np.array([[0.0, 1.0], [2.0, 3.0]], dtype=float)

        metrics = pipeline.compute_distribution_metrics(matrix, matrix, grid_size=1.0, top_k=2)

        self.assertAlmostEqual(metrics["jsd"], 0.0)
        self.assertAlmostEqual(metrics["recall_top10"], 1.0)
        self.assertAlmostEqual(metrics["hotspot_center_distance"], 0.0)

    def test_supplemental_metrics_reward_nearby_hotspot_overlap(self):
        real = np.zeros((9, 9), dtype=float)
        near = np.zeros((9, 9), dtype=float)
        far = np.zeros((9, 9), dtype=float)
        real[4, 4] = 1.0
        near[4, 5] = 1.0
        far[0, 0] = 1.0

        near_metrics = pipeline.compute_supplemental_metrics(near, real, grid_size=1.0)
        far_metrics = pipeline.compute_supplemental_metrics(far, real, grid_size=1.0)

        self.assertGreater(near_metrics["hotspot_f1_top5pct"], far_metrics["hotspot_f1_top5pct"])
        self.assertLess(near_metrics["coverage_count_abs_diff"], far_metrics["coverage_count_abs_diff"] + 1e-9)

    def test_parse_decision_transcript_extracts_events(self):
        text = """Decision Transcript
Experiment: exp_abc
=== Batch: batch_1 | Time: 2026-08-01 00:00:01.000 ===
[Agent: agent_1#-1]
  CrowdType: tourist | Zone: entry | Stay: 631ms | Patience: 0.15
  Dilemma: demo AlgorithmRec: shop_03
  Exits (4):
  [llm] exit=shop_03 confidence=0.95 latency_ms=123
  Reasoning: matched preference
"""

        events = pipeline.parse_decision_transcript(text)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["experiment_id"], "exp_abc")
        self.assertEqual(events[0]["batch_id"], "batch_1")
        self.assertEqual(events[0]["agent_id"], "agent_1")
        self.assertEqual(events[0]["decision_source"], "llm")
        self.assertEqual(events[0]["candidate_count"], 4)
        self.assertTrue(math.isclose(events[0]["confidence"], 0.95))


if __name__ == "__main__":
    unittest.main()
