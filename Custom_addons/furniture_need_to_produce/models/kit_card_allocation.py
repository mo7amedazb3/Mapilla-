"""Pure, quantity-conserving allocation for cross-order textile kit cards.

This module deliberately has no ORM writes.  Quantities must already be in
each component's stock UoM.  Execution must revalidate the plan under locks;
a preview is never permission to start an original order in its entirety.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json


def quantity(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid component quantity") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Component quantity must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class KitSource:
    line_id: int
    product_key: int
    qty: Decimal
    # Company, stage, model, buyer, beneficiary: never omit any dimension.
    scope: tuple
    signature: str
    eligible: bool = True
    # Preserve an existing explicitly assigned kit, even across partial rows.
    fixed_kit: tuple = ()


@dataclass(frozen=True)
class KitRecipe:
    recipe_id: int
    company_id: int
    model_id: int
    requirements: tuple


@dataclass(frozen=True)
class KitAllocation:
    recipe_id: int
    scope: tuple
    members: tuple
    token: str


def allocate_kit_cards(sources, recipes, max_cards=2000):
    """Return (complete kits, remaining quantities), without mutating inputs.

    Cross-order pooling is intentional; cross-company/customer/model/stage
    pooling is not.  Ambiguous recipes or differing component snapshots are
    left ungrouped rather than guessed.  Every input quantity is returned
    exactly once, either as an allocation or as an explicit remainder.
    """
    sources = sorted(sources, key=lambda source: source.line_id)
    if len({source.line_id for source in sources}) != len(sources):
        raise ValueError("Duplicate source line")
    if max_cards < 1:
        raise ValueError("max_cards must be positive")
    remaining = {}
    buckets = defaultdict(list)
    by_scope = defaultdict(list)
    for recipe in recipes:
        requirements = {}
        for product_key, amount in recipe.requirements:
            amount = quantity(amount)
            if not amount or not product_key:
                raise ValueError("Kit components must be positive")
            requirements[product_key] = requirements.get(product_key, Decimal(0)) + amount
        if not requirements:
            raise ValueError("Empty kit recipe")
        by_scope[(recipe.company_id, recipe.model_id)].append((recipe, requirements))
    for source in sources:
        if len(source.scope) != 5 or not source.line_id or not source.product_key:
            raise ValueError("Incomplete source identity")
        remaining[source.line_id] = quantity(source.qty)
        if source.eligible and source.qty:
            buckets[(source.scope, source.fixed_kit)].append(source)

    cards = []
    for (scope, fixed_kit), bucket in sorted(buckets.items(), key=lambda item: repr(item[0])):
        company, stage, model, buyer, beneficiary = scope
        options = by_scope.get((company, model), [])
        if fixed_kit:
            options = [option for option in options if option[0].recipe_id == fixed_kit[0]]
        if len(options) != 1:
            continue
        recipe, requirements = options[0]
        component_sources = defaultdict(list)
        for source in bucket:
            component_sources[source.product_key].append(source)
        if any(key not in component_sources for key in requirements):
            continue
        # In particular, two fauteuil with different fabrics/BOM snapshots
        # cannot silently become the two identical members of one kit.
        if any(len({source.signature for source in component_sources[key]}) != 1
               for key in requirements):
            continue
        count = min(int(sum((remaining[source.line_id] for source in component_sources[key]),
                            Decimal(0)) // amount)
                    for key, amount in requirements.items())
        if fixed_kit:
            count = min(count, 1)
        if len(cards) + count > max_cards:
            raise ValueError("Too many kit cards; narrow the planning scope")
        for index in range(count):
            members = []
            for key, amount in sorted(requirements.items()):
                needed = amount
                for source in component_sources[key]:
                    used = min(needed, remaining[source.line_id])
                    if used:
                        members.append((source.line_id, used))
                        remaining[source.line_id] -= used
                        needed -= used
                    if not needed:
                        break
                if needed:
                    raise AssertionError("Non-conserving kit allocation")
            # Include the full source snapshot and occurrence: two kits can
            # have identical source IDs/quantities but must not share a token.
            snapshot = [(source.line_id, str(quantity(source.qty)), source.signature)
                        for source in bucket]
            raw = json.dumps([recipe.recipe_id, scope, fixed_kit, snapshot, index,
                              [(line_id, str(amount)) for line_id, amount in members]],
                             ensure_ascii=False, separators=(",", ":"))
            token = "kit:" + hashlib.sha256(raw.encode()).hexdigest()
            cards.append(KitAllocation(recipe.recipe_id, scope, tuple(members), token))
    return cards, {line_id: amount for line_id, amount in remaining.items() if amount}
