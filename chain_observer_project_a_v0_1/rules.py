"""Declared property-rule validation and evaluation, retained from V1.3."""
from __future__ import annotations
import copy
import math
import operator
from typing import Any, Callable, Optional

VALID_CHECKS = {"PASS", "VIOLATION", "UNDETERMINED"}
COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
    "lte": operator.le, "gt": operator.gt, "gte": operator.ge,
}

class RuleConfigurationError(ValueError):
    pass


def validate_rules(rules: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise RuleConfigurationError("rules must be a list; use [] explicitly for no declared checks.")
    validated = copy.deepcopy(rules)
    for index, rule in enumerate(validated):
        if not isinstance(rule, dict):
            raise RuleConfigurationError(f"Rule {index} must be a dictionary.")
        name = rule.get("property")
        if not isinstance(name, str) or not name.strip():
            raise RuleConfigurationError(f"Rule {index} requires a non-empty string property.")
        mode = rule.get("mode", "preserve")
        if not isinstance(mode, str) or mode not in {"preserve", "constraint"}:
            raise RuleConfigurationError(f"Rule {index} has unsupported mode: {mode!r}.")
        tolerance = rule.get("tolerance", 0)
        try:
            valid = (not isinstance(tolerance, bool) and isinstance(tolerance, (int, float))
                     and math.isfinite(float(tolerance)) and tolerance >= 0)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise RuleConfigurationError(f"Rule {index} tolerance must be a finite non-negative number.")
        if mode == "constraint":
            comparator = rule.get("comparator")
            if not isinstance(comparator, str) or comparator not in COMPARATORS:
                raise RuleConfigurationError(f"Rule {index} has unsupported comparator: {comparator!r}.")
            if "expected" not in rule:
                raise RuleConfigurationError(f"Rule {index} requires 'expected' for constraint mode.")
    return validated


def evaluate_rule(before: dict[str, Any], after: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    name, mode, tolerance = rule["property"], rule.get("mode", "preserve"), rule.get("tolerance", 0)
    if name not in before or name not in after:
        return {"property": name, "result": "UNDETERMINED", "reason": "Property missing before or after transformation."}
    old, new = before[name], after[name]
    try:
        if mode == "preserve":
            if (isinstance(old, (int, float)) and not isinstance(old, bool)
                    and isinstance(new, (int, float)) and not isinstance(new, bool)):
                old_nan = isinstance(old, float) and math.isnan(old)
                new_nan = isinstance(new, float) and math.isnan(new)
                passed = (old_nan and new_nan) if (old_nan or new_nan) else math.isclose(
                    old, new, rel_tol=float(tolerance), abs_tol=float(tolerance))
            else:
                passed = old == new
        else:
            passed = COMPARATORS[rule["comparator"]](new, rule["expected"])
    except (TypeError, ValueError, OverflowError) as exc:
        return {"property": name, "before": copy.deepcopy(old), "after": copy.deepcopy(new),
                "result": "UNDETERMINED", "reason": f"Not comparable: {exc}"}
    return {"property": name, "before": copy.deepcopy(old), "after": copy.deepcopy(new),
            "rule": copy.deepcopy(rule), "result": "PASS" if passed else "VIOLATION",
            "reason": "Declared property rule satisfied." if passed else "Declared property rule was violated."}


def evaluate_rules(before: dict[str, Any], after: dict[str, Any], rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    checks = [evaluate_rule(before, after, rule) for rule in rules]
    overall = ("UNDETERMINED" if not checks else "VIOLATION" if any(c["result"] == "VIOLATION" for c in checks)
               else "UNDETERMINED" if any(c["result"] == "UNDETERMINED" for c in checks) else "PASS")
    return checks, overall
