import unittest

from app.inference.decision import classify_screening_score, explain_screening_score


class ScreeningDecisionTests(unittest.TestCase):
    def test_unvalidated_scores_are_inconclusive_instead_of_false_alarms(self) -> None:
        self.assertEqual(classify_screening_score(-5.9, 0.25, False), "inconclusive")
        self.assertEqual(classify_screening_score(5.9, 0.25, False), "inconclusive")

    def test_validated_scores_use_the_configured_decision_margin(self) -> None:
        self.assertEqual(classify_screening_score(-0.3, 0.25, True), "suspicious")
        self.assertEqual(classify_screening_score(0.3, 0.25, True), "likely genuine")
        self.assertEqual(classify_screening_score(0.1, 0.25, True), "inconclusive")

    def test_explanation_describes_score_direction_and_validation(self) -> None:
        explanation = explain_screening_score(-5.9, "inconclusive", 0.25, False)
        self.assertIn("lower score leans toward spoof-like", explanation)
        self.assertIn("not been validated", explanation)
        self.assertIn("Can’t tell", explanation)

    def test_validated_explanation_reports_the_cutoff(self) -> None:
        explanation = explain_screening_score(-0.3, "suspicious", 0.25, True)
        self.assertIn("below the suspicious cutoff of -0.25", explanation)


if __name__ == "__main__":
    unittest.main()
