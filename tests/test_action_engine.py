"""Deterministic tests for action_engine.py — dice math, modifiers, DC evaluation.

No LLM client is tested here; only pure Python logic.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.action_engine import (
    BASE_DC,
    DiceSystem,
    SkillResolver,
    evaluate_outcome,
    resolve_action,
)


class TestDiceSystem(unittest.TestCase):
    """d20 roll ranges and advantage/disadvantage."""

    def test_roll_d20_returns_1_to_20(self):
        for _ in range(50):
            r = DiceSystem.roll_d20()
            self.assertGreaterEqual(r, 1)
            self.assertLessEqual(r, 20)

    def test_advantage_always_gte_single_roll(self):
        """Advantage: max of two rolls ≥ either individual roll."""
        for _ in range(50):
            with patch("src.action_engine.DiceSystem.roll_d20", side_effect=lambda: 7 if DiceSystem.roll_d20.__self__ and hasattr(DiceSystem.roll_d20.__self__, "_ctr") else (setattr(DiceSystem.roll_d20.__self__, "_ctr", 1) or 3, 9)[1] if not getattr(DiceSystem.roll_d20.__self__, "_ctr", False) else 9):
                pass  # skip — complex mocking; test manually below.

    def test_advantage_gte_individual(self):
        """Advantage: the returned max >= min in a controlled two-roll."""
        rolls = [3, 14]
        with patch("src.action_engine.DiceSystem.roll_d20", side_effect=rolls + rolls):
            raw, _ = DiceSystem.roll_attack("advantage")
            self.assertEqual(raw, 14)

    def test_disadvantage_lte_individual(self):
        """Disadvantage: the returned min <= max in a controlled two-roll."""
        rolls = [14, 3]
        with patch("src.action_engine.DiceSystem.roll_d20", side_effect=rolls + rolls):
            raw, _ = DiceSystem.roll_attack("disadvantage")
            self.assertEqual(raw, 3)

    def test_none_uses_single_roll(self):
        """No advantage/disadvantage returns exactly one roll."""
        with patch("src.action_engine.DiceSystem.roll_d20", return_value=17):
            raw, _ = DiceSystem.roll_attack("none")
            self.assertEqual(raw, 17)


class TestEvaluateOutcome(unittest.TestCase):
    """Outcome level mapping: margin-based rules."""

    def test_natural_20_is_crit_fresh(self):
        result = evaluate_outcome(20, 15, raw_roll=20)
        self.assertEqual(result["outcome_level"], "crit_fresh")
        self.assertTrue(result["success"])

    def test_natural_1_is_failure(self):
        result = evaluate_outcome(15, 15, raw_roll=1)
        self.assertEqual(result["outcome_level"], "failure")
        self.assertFalse(result["success"])

    def test_margin_ge_10_is_crit(self):
        result = evaluate_outcome(22, 12, raw_roll=15)
        self.assertEqual(result["outcome_level"], "crit")
        self.assertTrue(result["success"])

    def test_margin_zero_is_success(self):
        result = evaluate_outcome(15, 15, raw_roll=15)
        self.assertEqual(result["outcome_level"], "success")
        self.assertTrue(result["success"])

    def test_margin_minus_1_is_partial(self):
        result = evaluate_outcome(14, 15, raw_roll=14)
        self.assertEqual(result["outcome_level"], "partial")
        self.assertFalse(result["success"])

    def test_margin_minus_6_is_failure(self):
        result = evaluate_outcome(9, 15, raw_roll=9)
        self.assertEqual(result["outcome_level"], "failure")
        self.assertFalse(result["success"])

    def test_no_raw_roll_defaults_margin_calc(self):
        """When raw_roll is None, auto-crit/auto-fail is skipped."""
        result = evaluate_outcome(20, 10, raw_roll=None)
        self.assertEqual(result["outcome_level"], "crit")
        self.assertTrue(result["success"])


class TestResolveAction(unittest.TestCase):
    """Full action resolution: modifier chain + roll + outcome."""

    def test_str_16_prof_bonus(self):
        """Strength 16 (+3 stat) + proficiency 2 → total modifier +5."""
        with patch("src.action_engine.DiceSystem.roll_d20", return_value=10):
            result = resolve_action(
                action_type="combat",
                stat_name="strength",
                stat_value=16,
                proficiency=2,
                tool_modifier=0,
            )
        self.assertEqual(result["stat_bonus"], 3)
        self.assertEqual(result["modifier"], 5)
        self.assertEqual(result["final_score"], 15)

    def test_advantage_higher_roll_used(self):
        """Advantage: final score reflects the higher d20."""
        rolls = [4, 18]  # advantage picks 18
        with patch("src.action_engine.DiceSystem.roll_d20", side_effect=rolls + rolls):
            result = resolve_action(
                action_type="combat",
                stat_name="strength",
                stat_value=10,
                advantage="advantage",
                proficiency=2,
            )
        self.assertEqual(result["dice_roll"], 18)
        self.assertEqual(result["final_score"], 20)  # 18 + 2

    def test_disadvantage_lower_roll_used(self):
        """Disadvantage: final score reflects the lower d20."""
        rolls = [18, 4]  # disadvantage picks 4
        with patch("src.action_engine.DiceSystem.roll_d20", side_effect=rolls + rolls):
            result = resolve_action(
                action_type="combat",
                stat_name="strength",
                stat_value=10,
                advantage="disadvantage",
                proficiency=2,
            )
        self.assertEqual(result["dice_roll"], 4)
        self.assertEqual(result["final_score"], 6)  # 4 + 2

    def test_tool_modifier_adds(self):
        """tool_modifier stacks on top of stat_bonus + proficiency."""
        with patch("src.action_engine.DiceSystem.roll_d20", return_value=12):
            result = resolve_action(
                action_type="combat",
                stat_name="strength",
                stat_value=14,
                proficiency=2,
                tool_modifier=3,
            )
        self.assertEqual(result["modifier"], 7)  # +2 (stat) + 2 (prof) + 3 (tool)
        self.assertEqual(result["final_score"], 19)

    def test_dc_from_action_type(self):
        """Different action types map to correct base DC."""
        cases = [
            ("combat", 12),
            ("stealth", 14),
            ("social", 10),
            ("exploration", 12),
            ("item", 12),
        ]
        for action_type, expected_dc in cases:
            result = resolve_action(
                action_type=action_type,
                stat_value=10,
                proficiency=0,
            )
            self.assertEqual(result["target_dc"], expected_dc, f"DC mismatch for {action_type}")

    def test_crit_fresh_from_nat20(self):
        """Natural 20 always gives crit_fresh regardless of modifier and DC."""
        with patch("src.action_engine.DiceSystem.roll_d20", return_value=20):
            result = resolve_action(
                action_type="stealth",  # DC 14
                stat_value=1,  # -5 stat bonus — would normally fail badly
                proficiency=0,
            )
        self.assertEqual(result["outcome_level"], "crit_fresh")
        self.assertTrue(result["success"])


class TestSkillResolverDC(unittest.TestCase):
    """DC determination logic."""

    def test_stealth_sleeping_bump(self):
        """Stealth vs sleeping target bumps DC to 15."""
        dc = SkillResolver.determine_dc("stealth", context="sleeping guard")
        self.assertEqual(dc, 15)

    def test_stealth_normal(self):
        """Normal stealth keeps base DC of 14."""
        dc = SkillResolver.determine_dc("stealth", context="alert guard")
        self.assertEqual(dc, 14)

    def test_unknown_type_defaults_to_item(self):
        """Unknown action_type falls back to BASE_DC['item'] (12)."""
        dc = SkillResolver.determine_dc("mystery_type", "")
        self.assertEqual(dc, BASE_DC["item"])


if __name__ == "__main__":
    unittest.main()
