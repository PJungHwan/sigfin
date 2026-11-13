from copy import deepcopy
import json
import unittest

from strategies.models import Strategy, StrategyValidationError, default_strategy
from strategies.storage import (
    MAX_JSON_BYTES, download_filename, strategy_from_dict, strategy_from_json,
    strategy_to_dict, strategy_to_json,
)


class StrategyStorageTests(unittest.TestCase):
    def test_json_roundtrip_unicode_and_bytes(self):
        original = default_strategy()
        encoded = strategy_to_json(original)
        self.assertIn("RSI 조건 예시", encoded)
        self.assertEqual(strategy_from_json(encoded), original)
        self.assertEqual(strategy_from_json(encoded.encode("utf-8")), original)
        self.assertEqual(strategy_from_json(b"\xef\xbb\xbf" + encoded.encode("utf-8")), original)
        self.assertEqual(strategy_from_dict(strategy_to_dict(original)), original)

    def test_invalid_json_encoding_duplicate_fields_and_size_are_blocked(self):
        for payload in ("{bad}", b"\xff\xfe", '{"name":"one","name":"two"}',
                        "{" + " " * MAX_JSON_BYTES + "}", "NaN", "Infinity", "[]", "null"):
            with self.subTest(payload=str(payload)[:30]):
                with self.assertRaises(StrategyValidationError):
                    strategy_from_json(payload)

    def test_schema_wrong_types_and_unknown_fields_are_blocked(self):
        base = strategy_to_dict(default_strategy())
        changes = [
            lambda item: item.update(schema_version=2),
            lambda item: item.update(schema_version=True),
            lambda item: item.update(schema_version="1"),
            lambda item: item.update(python_code="anything"),
            lambda item: item["long"].update(combine="XOR"),
            lambda item: item["long"].update(direction="SHORT"),
            lambda item: item["long"]["conditions"][0].update(left="__import__('os')"),
            lambda item: item["long"]["conditions"][0].update(operator="=="),
            lambda item: item["long"]["conditions"][0]["right"].update(value=True),
            lambda item: item["long"]["conditions"][0]["right"].update(value=101),
            lambda item: item["long"]["conditions"][0]["right"].update(indicator="close"),
            lambda item: item["long"]["conditions"][0].update(type="group"),
        ]
        for mutation in changes:
            with self.subTest(mutation=changes.index(mutation)):
                payload = deepcopy(base)
                mutation(payload)
                with self.assertRaises(StrategyValidationError):
                    strategy_from_json(json.dumps(payload))

    def test_download_filename_is_safe_and_unique(self):
        original = default_strategy()
        model = Strategy("../../CON\\private:strategy", original.long, original.short)
        first, second = download_filename(model), download_filename(model)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("strategy-"))
        self.assertTrue(first.endswith(".json"))
        for character in ("/", "\\", ":", ".."):
            self.assertNotIn(character, first)

    def test_empty_groups_roundtrip_as_disabled(self):
        payload = strategy_to_dict(default_strategy())
        payload["long"]["conditions"] = []
        payload["short"]["conditions"] = []
        model = strategy_from_dict(payload)
        self.assertEqual(strategy_from_json(strategy_to_json(model)), model)
