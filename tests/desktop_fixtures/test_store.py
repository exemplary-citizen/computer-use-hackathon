"""Persistence-core tests: deterministic seeds, atomic writes, label divergence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from desktop_fixtures.store import (
    FIELD_LABELS,
    ContactRecord,
    default_seed,
    load_state,
    state_path,
    write_state_atomic,
)


class SeedTests(unittest.TestCase):
    def test_default_seed_is_deterministic(self) -> None:
        self.assertEqual(default_seed().model_dump(), default_seed().model_dump())

    def test_seed_has_records_with_unique_ids_and_names(self) -> None:
        records = default_seed().records
        self.assertGreaterEqual(len(records), 5)
        self.assertEqual(len({record.id for record in records}), len(records))
        self.assertEqual(len({record.full_name for record in records}), len(records))

    def test_contact_record_rejects_unknown_status(self) -> None:
        record = default_seed().records[0]
        with self.assertRaises(ValidationError):
            record.model_copy(update={"status": "VIP"}).model_validate(
                record.model_copy(update={"status": "VIP"}).model_dump()
            )


class LabelDivergenceTests(unittest.TestCase):
    def test_every_field_label_differs_between_apps(self) -> None:
        for field, per_app in FIELD_LABELS.items():
            with self.subTest(field=field):
                self.assertNotEqual(per_app["a"], per_app["b"])

    def test_labels_cover_every_editable_record_field(self) -> None:
        editable = set(ContactRecord.model_fields) - {"id"}
        self.assertEqual(set(FIELD_LABELS), editable)


class AtomicStateFileTests(unittest.TestCase):
    def test_load_state_seeds_missing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = state_path("a", Path(tmp))
            state = load_state(path)
            self.assertTrue(path.is_file())
            self.assertEqual(state.model_dump(), default_seed().model_dump())

    def test_write_leaves_no_temp_files_and_valid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            path = state_path("b", Path(tmp))
            write_state_atomic(path, default_seed())
            write_state_atomic(path, default_seed())
            leftovers = [p for p in path.parent.iterdir() if p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])
            parsed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["schema_version"], "1.0")

    def test_reset_dump_roundtrip_is_byte_stable(self) -> None:
        with TemporaryDirectory() as tmp:
            path = state_path("a", Path(tmp))
            write_state_atomic(path, default_seed())
            first = path.read_bytes()
            write_state_atomic(path, load_state(path))
            self.assertEqual(first, path.read_bytes())

    def test_state_paths_are_distinct_per_app(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertNotEqual(state_path("a", Path(tmp)), state_path("b", Path(tmp)))


if __name__ == "__main__":
    unittest.main()
