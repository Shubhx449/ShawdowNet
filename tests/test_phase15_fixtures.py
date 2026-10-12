"""
The frontend tests read fixtures captured from the real routers. This test fails when the routers'
output no longer matches them -- regenerate with `python3 scripts/make_frontend_fixtures.py`, re-run
the frontend tests (`cd frontend && npm test`), and fix frontend/src/algorithms.js if they break.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.make_frontend_fixtures import OUT, generate


class TestFrontendFixturesAreCurrent(unittest.TestCase):
    def test_committed_fixtures_match_what_the_routers_produce_now(self):
        for name, fresh in generate().items():
            with open(os.path.join(OUT, name)) as f:
                committed = json.load(f)
            self.assertEqual(json.loads(json.dumps(fresh, sort_keys=True)), committed,
                             f"{name} is stale. Run: python3 scripts/make_frontend_fixtures.py, then `npm test` in frontend/")


if __name__ == "__main__":
    unittest.main()


def _compatible(a, b, path="$"):
    """Same structure: same dict keys, same scalar kinds. None matches anything (nullable fields), and lists
    are compared by their first element. Returns a description of the first difference, or None."""
    if a is None or b is None:
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return f"{path}: keys differ: only committed={sorted(set(a) - set(b))}, only fresh={sorted(set(b) - set(a))}"
        for k in a:
            d = _compatible(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        return _compatible(a[0], b[0], path + "[0]") if a and b else None
    kind = lambda v: "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else type(v).__name__
    return None if kind(a) == kind(b) else f"{path}: {kind(a)} vs {kind(b)}"


class TestUiFixturesMatchTheBackendShapes(unittest.TestCase):
    """The page tests render real API/WebSocket payloads captured earlier. If the backend's response
    shapes change, those tests would keep passing against stale data -- this makes that impossible."""

    def test_committed_ui_fixtures_have_the_shape_the_backend_sends_now(self):
        from scripts.make_frontend_fixtures import generate_ui_fixtures
        for name, fresh in generate_ui_fixtures().items():
            with open(os.path.join(OUT, name)) as f:
                committed = json.load(f)
            diff = _compatible(committed, json.loads(json.dumps(fresh)))
            self.assertIsNone(diff, f"{name} no longer matches the backend ({diff}). "
                                    f"Run: python3 scripts/make_frontend_fixtures.py, then `npm test` in frontend/")

    def test_the_shape_checker_itself_detects_drift(self):
        self.assertIsNotNone(_compatible({"a": 1}, {"a": 1, "b": 2}))
        self.assertIsNotNone(_compatible({"a": 1}, {"a": "x"}))
        self.assertIsNotNone(_compatible({"a": [{"x": 1}]}, {"a": [{"y": 1}]}))
        self.assertIsNone(_compatible({"a": None}, {"a": "text"}))
        self.assertIsNone(_compatible({"a": 1}, {"a": 2.5}))
