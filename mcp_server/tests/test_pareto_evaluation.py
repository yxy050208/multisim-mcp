from __future__ import annotations

import unittest

from multisim_mcp.ranked_evaluation import (
    normalize_objectives,
    objective_vector,
    pareto_dominates,
    pareto_fronts,
    weighted_compromise,
)


def _verification(gain: float, power: float) -> dict[str, object]:
    return {
        "requirements": [
            {
                "id": "gain",
                "measurement": {"status": "measured", "value": gain},
            },
            {
                "id": "power",
                "measurement": {"status": "measured", "value": power},
            },
        ]
    }


class ParetoEvaluationTest(unittest.TestCase):
    def test_normalizes_and_extracts_minimization_scores(self) -> None:
        objectives = normalize_objectives(
            [
                {
                    "requirement_id": "gain",
                    "goal": "maximize",
                    "epsilon": 0.01,
                    "weight": 2,
                },
                {"requirement_id": "power", "goal": "minimize"},
            ],
            {"gain", "power"},
        )
        vector = objective_vector(_verification(10.0, 2.0), objectives)
        self.assertEqual([item["score"] for item in vector], [-10.0, 2.0])
        self.assertEqual(vector[0]["epsilon"], 0.01)
        self.assertEqual(vector[0]["weight"], 2.0)

    def test_epsilon_dominance_and_fronts_are_deterministic(self) -> None:
        objectives = normalize_objectives(
            [
                {"requirement_id": "gain", "goal": "maximize"},
                {"requirement_id": "power", "goal": "minimize"},
            ],
            {"gain", "power"},
        )
        evaluations = [
            {"id": "balanced", "objectives": objective_vector(_verification(8, 2), objectives)},
            {"id": "fast", "objectives": objective_vector(_verification(10, 4), objectives)},
            {"id": "dominated", "objectives": objective_vector(_verification(7, 3), objectives)},
            {"id": "efficient", "objectives": objective_vector(_verification(9, 1), objectives)},
        ]
        self.assertFalse(
            pareto_dominates(evaluations[0]["objectives"], evaluations[1]["objectives"])
        )
        self.assertTrue(
            pareto_dominates(evaluations[0]["objectives"], evaluations[2]["objectives"])
        )
        self.assertEqual(pareto_fronts(evaluations), [[1, 3], [0], [2]])

    def test_weighted_compromise_uses_normalized_objective_ranges(self) -> None:
        objectives = normalize_objectives(
            [
                {"requirement_id": "gain", "goal": "maximize", "weight": 2},
                {"requirement_id": "power", "goal": "minimize", "weight": 1},
            ],
            {"gain", "power"},
        )
        front = [
            {"objectives": objective_vector(_verification(8, 1), objectives)},
            {"objectives": objective_vector(_verification(10, 4), objectives)},
        ]
        self.assertEqual(weighted_compromise(front), 1)

    def test_rejects_duplicate_objectives_and_invalid_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "distinct"):
            normalize_objectives(
                [
                    {"requirement_id": "gain", "goal": "maximize"},
                    {"requirement_id": "gain", "goal": "minimize"},
                ],
                {"gain"},
            )
        with self.assertRaisesRegex(ValueError, "weight"):
            normalize_objectives(
                [{"requirement_id": "gain", "goal": "maximize", "weight": 0}],
                {"gain"},
            )


if __name__ == "__main__":
    unittest.main()
