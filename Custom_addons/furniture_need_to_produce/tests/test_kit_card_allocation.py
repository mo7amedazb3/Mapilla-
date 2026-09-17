"""Run directly with Python; no production database or Odoo writes."""
import importlib.util
from pathlib import Path
import sys
import unittest
from decimal import Decimal

spec = importlib.util.spec_from_file_location(
    "kit_card_allocation", Path(__file__).parents[1] / "models/kit_card_allocation.py")
allocation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = allocation
spec.loader.exec_module(allocation)
Source = allocation.KitSource
Recipe = allocation.KitRecipe
allocate = allocation.allocate_kit_cards
SCOPE = (1, "upholstery", 6, 10, 20)


def source(line, product, qty, **kwargs):
    return Source(line, product, Decimal(str(qty)), kwargs.pop("scope", SCOPE),
                  kwargs.pop("signature", "standard"), **kwargs)


def recipe(recipe_id=5280, **kwargs):
    return Recipe(recipe_id, kwargs.get("company", 1), kwargs.get("model", 6),
                  kwargs.get("requirements", ((101, 1), (102, 1), (103, 2))))


class TestKitCardAllocation(unittest.TestCase):
    def assert_conserved(self, sources, cards, remaining):
        for item in sources:
            used = sum((qty for card in cards for line_id, qty in card.members
                        if line_id == item.line_id), Decimal(0))
            self.assertEqual(item.qty, used + remaining.get(item.line_id, 0))

    def test_two_kits_from_three_different_orders(self):
        sources = [source(11, 101, 2), source(22, 102, 2), source(33, 103, 4)]
        cards, rest = allocate(sources, [recipe()])
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].members, ((11, Decimal(1)), (22, Decimal(1)), (33, Decimal(2))))
        self.assertNotEqual(cards[0].token, cards[1].token)
        self.assertFalse(rest)
        self.assert_conserved(sources, cards, rest)

    def test_component_can_come_from_multiple_orders(self):
        sources = [source(1, 101, 1), source(2, 102, 1), source(3, 103, 1), source(4, 103, 1)]
        cards, rest = allocate(sources, [recipe()])
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0].members), 4)
        self.assert_conserved(sources, cards, rest)

    def test_leftovers_never_disappear(self):
        sources = [source(1, 101, 2), source(2, 102, 1), source(3, 103, 5), source(4, 999, 3)]
        cards, rest = allocate(sources, [recipe()])
        self.assertEqual(len(cards), 1)
        self.assertEqual(rest, {1: Decimal(1), 3: Decimal(3), 4: Decimal(3)})
        self.assert_conserved(sources, cards, rest)

    def test_all_scope_dimensions_are_isolated(self):
        for dimension, other in enumerate((2, "tailoring", 7, 11, 21)):
            changed = list(SCOPE)
            changed[dimension] = other
            sources = [source(1, 101, 1), source(2, 102, 1), source(3, 103, 2, scope=tuple(changed))]
            cards, rest = allocate(sources, [recipe()])
            self.assertFalse(cards)
            self.assert_conserved(sources, cards, rest)

    def test_custom_and_incompatible_components_remain_separate(self):
        for extra in ({"eligible": False}, {"signature": "custom-fabric"}):
            sources = [source(1, 101, 1), source(2, 102, 1), source(3, 103, 1), source(4, 103, 1, **extra)]
            cards, rest = allocate(sources, [recipe()])
            self.assertFalse(cards)
            self.assert_conserved(sources, cards, rest)

    def test_ambiguous_recipe_not_guessed(self):
        sources = [source(1, 101, 1), source(2, 102, 1), source(3, 103, 2)]
        cards, rest = allocate(sources, [recipe(), recipe(5282)])
        self.assertFalse(cards)
        self.assert_conserved(sources, cards, rest)

    def test_fixed_kits_are_not_cross_pooled(self):
        sources = [source(1, 101, 1, fixed_kit=(5280, 900, 1)),
                   source(2, 102, 1, fixed_kit=(5280, 900, 2)),
                   source(3, 103, 2, fixed_kit=(5280, 900, 1))]
        cards, rest = allocate(sources, [recipe()])
        self.assertFalse(cards)
        self.assert_conserved(sources, cards, rest)

    def test_non_unit_kit_output_and_fractional_components(self):
        sources = [source(1, 101, "1.5"), source(2, 102, ".75")]
        cards, rest = allocate(sources, [recipe(requirements=((101, ".5"), (102, ".25")))])
        self.assertEqual(len(cards), 3)
        self.assert_conserved(sources, cards, rest)

    def test_deterministic_and_stale_snapshot_tokens(self):
        sources = [source(1, 101, 2), source(2, 102, 2), source(3, 103, 4)]
        cards, _ = allocate(sources, [recipe()])
        self.assertEqual(cards, allocate(list(reversed(sources)), [recipe()])[0])
        changed = [source(1, 101, 3), *sources[1:]]
        self.assertNotEqual(cards[0].token, allocate(changed, [recipe()])[0][0].token)

    def test_invalid_data_fails_closed(self):
        for amount in ("NaN", "Infinity", "-1"):
            with self.assertRaises(ValueError):
                allocate([source(1, 101, amount)], [recipe()])
        with self.assertRaises(ValueError):
            allocate([source(1, 101, 1), source(1, 101, 1)], [recipe()])
        with self.assertRaises(ValueError):
            allocate([], [recipe(requirements=())])

    def test_capacity_limit_does_not_silently_drop_cards(self):
        sources = [source(1, 101, 2), source(2, 102, 2), source(3, 103, 4)]
        with self.assertRaises(ValueError):
            allocate(sources, [recipe()], max_cards=1)


if __name__ == "__main__":
    unittest.main()
