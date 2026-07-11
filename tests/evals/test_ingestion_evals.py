"""Gold-fixture schema and release-threshold tests."""

import unittest
from pathlib import Path

from automation_foundry.evals import IngestionObservation, load_gold_fixtures, score_ingestion

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "ingestion_gold"


class IngestionEvalTests(unittest.TestCase):
    """Keep gold annotations complete and scoring fail-closed."""

    def setUp(self) -> None:
        self.fixtures = load_gold_fixtures(FIXTURE_ROOT)

    def test_all_six_required_gold_modes_are_versioned(self) -> None:
        self.assertEqual(
            {fixture.fixture_name for fixture in self.fixtures},
            {
                "combined_aligned",
                "combined_conflict",
                "malformed_input",
                "silent_video",
                "sop_only",
                "video_only",
            },
        )

    def test_canonical_reviewer_matches_pass_release_thresholds(self) -> None:
        observations = []
        for fixture in self.fixtures:
            step_ids = {step.id for step in fixture.critical_steps}
            boundary_ids = {step.id for step in fixture.critical_steps if step.confirmation_boundary}
            observations.append(
                IngestionObservation(
                    fixture_name=fixture.fixture_name,
                    matched_step_ids=step_ids,
                    matched_confirmation_ids=boundary_ids,
                    evidence_backed_step_ids=step_ids,
                    visible_conflicts=set(fixture.expected_conflicts),
                    generated_skill="Stop for approval before Save, then verify visible success.",
                    rejected_before_generation=not fixture.valid_input,
                )
            )

        score = score_ingestion(self.fixtures, observations)

        self.assertTrue(score.passed, score.failures)
        self.assertEqual(score.critical_step_recall, 1)
        self.assertEqual(score.confirmation_boundary_recall, 1)

    def test_missing_confirmation_boundary_fails_even_above_quality_threshold(self) -> None:
        observations = []
        removed = False
        for fixture in self.fixtures:
            step_ids = {step.id for step in fixture.critical_steps}
            boundary_ids = {step.id for step in fixture.critical_steps if step.confirmation_boundary}
            if boundary_ids and not removed:
                boundary_ids.clear()
                removed = True
            observations.append(
                IngestionObservation(
                    fixture_name=fixture.fixture_name,
                    matched_step_ids=step_ids,
                    matched_confirmation_ids=boundary_ids,
                    evidence_backed_step_ids=step_ids,
                    visible_conflicts=set(fixture.expected_conflicts),
                    generated_skill="Stop for approval before Save.",
                    rejected_before_generation=not fixture.valid_input,
                )
            )

        score = score_ingestion(self.fixtures, observations)

        self.assertGreaterEqual(score.critical_step_recall, 0.9)
        self.assertLess(score.confirmation_boundary_recall, 1)
        self.assertFalse(score.passed)

    def test_app_specific_coordinates_fail_portability_gate(self) -> None:
        observations = []
        for fixture in self.fixtures:
            step_ids = {step.id for step in fixture.critical_steps}
            boundary_ids = {step.id for step in fixture.critical_steps if step.confirmation_boundary}
            observations.append(
                IngestionObservation(
                    fixture_name=fixture.fixture_name,
                    matched_step_ids=step_ids,
                    matched_confirmation_ids=boundary_ids,
                    evidence_backed_step_ids=step_ids,
                    visible_conflicts=set(fixture.expected_conflicts),
                    generated_skill="Click at 420, 220 and then Save.",
                    rejected_before_generation=not fixture.valid_input,
                )
            )

        score = score_ingestion(self.fixtures, observations)

        self.assertFalse(score.portable_skills)
        self.assertFalse(score.passed)


if __name__ == "__main__":
    unittest.main()
