# -*- coding: utf-8 -*-
"""Originals without a datastore: the content-addressed store, intake's contract for originals, and the
label validator's check of a quote against the original at the unit's locator.

The owner's rule: anything ingested must link to an original that can be pulled back, byte for byte.
Every test here was first run against a named deliberate break of the code and failed (see the report of
the change that added this file); each asserts the effect, not the call.

Synthetic, non-legal instance: the demo incident vocabulary, with its own originals section.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from groundworks import config  # noqa: E402
from groundworks import originals  # noqa: E402
from groundworks import store as storemod  # noqa: E402
from groundworks.pipeline import chunker  # noqa: E402
from groundworks.pipeline import intake  # noqa: E402
from groundworks.pipeline import validator  # noqa: E402

DEMO = HERE.parent / "instances" / "demo-incident" / "instance.yaml"
LOG = b"11:40 api-1 ERROR pool exhausted: waited 30000ms\n11:41 api-2 WARN retrying upstream call\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"


def instance(mode="required", spans=None):
    data = config.parse_yaml(DEMO.read_text(encoding="utf-8"))
    data["originals"] = {"mode": mode, "spans": spans or {"log_line": {"line": "line"}}}
    return config.load(data)


def unit(i, text, document="app.log", **extra):
    row = {"id": "u%d" % i, "type": "log_line", "text": text, "status": "in_window", "grade": "original_log",
           "locator": {"host": "api-1", "file": "app.log", "line": i, "ts": "2026-03-04T11:40:00Z"}}
    if document is not None:
        row["document"] = document
    row.update(extra)
    return row


class Adapter:
    def __init__(self, documents, units):
        self._documents, self._units = documents, units

    def documents(self):
        return iter(self._documents)

    def units(self):
        return iter(self._units)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = storemod.FileStore(self.dir / "originals")

    def files(self):
        return sorted(p for p in (self.dir / "originals" / "sha256").rglob("*") if p.is_file())


class StoreTests(Base):
    def test_the_same_bytes_put_twice_are_stored_once(self):
        first = self.store.put(LOG)
        second = self.store.put(bytes(LOG))
        self.assertEqual(first, second)
        self.assertEqual(first, storemod.sha256_bytes(LOG))
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(list(self.store.hashes()), [first])
        self.assertEqual(self.store.get(first), LOG)

    def test_an_altered_original_is_refused_on_read_and_named(self):
        sha = self.store.put(LOG)
        path = self.files()[0]
        path.write_bytes(LOG.replace(b"exhausted", b"fine     "))
        with self.assertRaises(storemod.CorruptOriginal) as err:
            self.store.get(sha, name="app.log")
        self.assertIn(sha, str(err.exception))
        self.assertIn("app.log", str(err.exception))
        self.assertIn("CORRUPT", str(err.exception))
        self.assertEqual(self.store.verify(sha), "corrupt")

    def test_a_missing_original_is_refused_on_read_and_named(self):
        sha = self.store.put(LOG)
        self.files()[0].unlink()
        with self.assertRaises(storemod.MissingOriginal) as err:
            self.store.get(sha, name="app.log")
        self.assertIn("app.log", str(err.exception))
        self.assertEqual(self.store.verify(sha), "missing")
        self.assertFalse(self.store.has(sha))

    def test_putting_the_true_bytes_again_repairs_a_damaged_copy(self):
        sha = self.store.put(LOG)
        self.files()[0].write_bytes(b"damaged")
        self.assertEqual(self.store.put(LOG), sha)
        self.assertEqual(self.store.get(sha), LOG)
        self.assertEqual(self.store.repaired, 1)

    def test_binary_originals_are_stored_and_verified_like_any_other(self):
        sha = self.store.put(PNG)
        self.assertEqual(self.store.get(sha), PNG)
        self.assertFalse(originals.is_text({"media_type": "image/png"}, PNG))

    def test_the_store_ignores_itself_in_whatever_repository_it_sits_in(self):
        if not shutil.which("git"):
            self.skipTest("NOT EXECUTED: git is not on PATH")
        repo = self.dir / "private_instance"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "instance.yaml").write_text("id: x\n", encoding="utf-8")
        storemod.FileStore(repo / "originals").put(LOG)
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                                check=True, capture_output=True, text=True).stdout
        self.assertIn("instance.yaml", status)           # the walk sees untracked files at all
        self.assertNotIn("originals", status, status)

    def test_a_store_inside_the_engine_repository_is_refused_outside_instances(self):
        with self.assertRaises(storemod.StoreError) as err:
            storemod.FileStore(storemod.ENGINE_ROOT / "groundworks" / "originals")
        self.assertIn("inside the engine repository", str(err.exception))
        storemod.refuse_engine_repo(storemod.ENGINE_ROOT / "instances" / "demo-incident" / "originals")


class IntakeTests(Base):
    def test_the_same_original_under_two_keys_is_one_document(self):
        adapter = Adapter([{"key": "app.log", "bytes": LOG, "media_type": "text/plain"},
                           {"key": "copy-of-app.log", "bytes": LOG, "media_type": "text/plain"}],
                          [unit(1, "pool exhausted"), unit(2, "retrying upstream call", document="copy-of-app.log")])
        counts = intake.write_units(adapter, self.dir, instance(), store=self.store)
        rows = intake.read_units(self.dir / "documents.jsonl")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["keys"], ["app.log", "copy-of-app.log"])
        self.assertEqual((counts["documents"], counts["documents_deduplicated"]), (1, 1))
        self.assertEqual(len(self.files()), 1)
        units = intake.read_units(self.dir / "units.jsonl")
        self.assertEqual({u["document"] for u in units}, {rows[0]["sha256"]})

    def test_required_refuses_a_unit_without_an_original_by_name(self):
        adapter = Adapter([{"key": "app.log", "bytes": LOG, "media_type": "text/plain"}],
                          [unit(1, "pool exhausted"), unit(2, "retrying upstream call", document=None)])
        with self.assertRaises(intake.IntakeError) as err:
            intake.write_units(adapter, self.dir, instance("required"), store=self.store)
        self.assertIn("unit u2", str(err.exception))
        self.assertIn("required", str(err.exception))

    def test_transition_keeps_the_unit_and_names_it(self):
        adapter = Adapter([{"key": "app.log", "bytes": LOG, "media_type": "text/plain"},
                           {"key": "lost.log", "capture": "none", "media_type": "text/plain",
                            "source_uri": "synthetic://gone"}],
                          [unit(1, "pool exhausted"), unit(2, "retrying upstream call", document=None),
                           unit(3, "a line from a file nobody kept", document="lost.log")])
        counts = intake.write_units(adapter, self.dir, instance("transition"), store=self.store)
        self.assertEqual((counts["units_with_original"], counts["units_without_original"]), (1, 2))
        self.assertEqual(counts["without_original"], ["u2", "u3"])
        gap = intake.read_units(self.dir / "without_original.jsonl")
        self.assertEqual([g["id"] for g in gap], ["u2", "u3"])
        self.assertIn("never captured", gap[1]["reason"])

    def test_a_unit_naming_an_original_the_adapter_never_supplied_is_refused(self):
        adapter = Adapter([{"key": "app.log", "bytes": LOG, "media_type": "text/plain"}],
                          [unit(1, "pool exhausted", document="other.log")])
        with self.assertRaises(intake.IntakeError) as err:
            intake.write_units(adapter, self.dir, instance(), store=self.store)
        self.assertIn("other.log", str(err.exception))

    def test_an_image_only_original_may_have_units_with_no_text_but_a_text_one_may_not(self):
        adapter = Adapter([{"key": "scan.png", "bytes": PNG, "media_type": "image/png"}],
                          [unit(1, "", document="scan.png")])
        counts = intake.write_units(adapter, self.dir, instance(), store=self.store)
        self.assertEqual(counts["units_with_original"], 1)
        self.assertEqual(intake.read_units(self.dir / "units.jsonl")[0]["text"], "")
        adapter = Adapter([{"key": "app.log", "bytes": LOG, "media_type": "text/plain"}], [unit(1, "", document="app.log")])
        with self.assertRaises(intake.IntakeError) as err:
            intake.write_units(adapter, self.dir, instance(), store=self.store)
        self.assertIn("text", str(err.exception))

    def test_the_unit_hash_binds_the_extraction_to_its_original(self):
        a = unit(1, "pool exhausted", document="a" * 64)
        b = unit(1, "pool exhausted", document="b" * 64)
        self.assertNotEqual(intake.unit_hash(a), intake.unit_hash(b))
        plain = unit(1, "pool exhausted", document=None)
        self.assertEqual(intake.unit_hash(plain), "sha256:" + __import__("hashlib").sha256(json.dumps(
            [plain["type"], plain["locator"], plain["text"]], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest())


class LocatorInsideOriginalTests(Base):
    """Intake refuses a unit whose locator points outside its own original. Measured 2026-09-16 before the
    fix: line 99 and line 0 of a 2-line original, and character 5000 of an 89-character one, were all kept."""

    DOC = [{"key": "app.log", "bytes": LOG, "media_type": "text/plain"}]

    def intake(self, units, inst=None):
        return intake.write_units(Adapter(self.DOC, units), self.dir, inst or instance(), store=self.store)

    def test_a_line_past_the_last_line_or_before_the_first_is_refused_by_name(self):
        for line in (3, 99, 0):
            with self.subTest(line=line), self.assertRaises(intake.IntakeError) as err:
                self.intake([unit(1, "pool exhausted"), unit(line, "retrying upstream call")])
            self.assertIn("unit u%d" % line, str(err.exception))
            self.assertIn("does not point into its own original", str(err.exception))
        counts = self.intake([unit(1, "pool exhausted"), unit(2, "retrying upstream call")])
        self.assertEqual((counts["locators_checked"], counts["locators_not_checkable"]), (2, {}))

    def test_a_character_span_past_the_length_or_reversed_is_refused(self):
        inst = instance(spans={"log_line": {"chars": ["line", "line"]}})
        for offset in (len(LOG) + 1, 5000):
            with self.subTest(offset=offset), self.assertRaises(intake.IntakeError):
                self.intake([unit(offset, "pool exhausted")], inst)
        self.assertEqual(self.intake([unit(len(LOG), "pool exhausted")], inst)["locators_checked"], 1)
        reversed_span = unit(1, "pool exhausted")
        reversed_span["locator"]["ts"] = 0
        with self.assertRaises(intake.IntakeError):
            self.intake([reversed_span], instance(spans={"log_line": {"lines": ["line", "ts"]}}))

    def test_what_cannot_be_checked_is_counted_by_reason_never_as_checked(self):
        docs = [{"key": "scan.png", "bytes": PNG, "media_type": "image/png"}]
        counts = intake.write_units(Adapter(docs, [unit(99, "", document="scan.png")]), self.dir, instance(),
                                    store=self.store)
        self.assertEqual((counts["locators_checked"], counts["locators_not_checkable"]), (0, {"binary original": 1}))
        counts = self.intake([unit(99, "pool exhausted")], instance(spans={"change": {"whole": True}}))
        self.assertEqual(counts["locators_checked"], 0)
        self.assertEqual(counts["locators_not_checkable"], {"no span declared for unit type log_line": 1})


class ValidatorTests(Base):
    def run_labels(self, documents, units, labels, inst=None):
        inst = inst or instance()
        intake.write_units(Adapter(documents, units), self.dir, inst, store=self.store)
        chunker.chunk(self.dir / "units.jsonl", self.dir, inst)
        path = self.dir / "labels.jsonl"
        path.write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
        return validator.validate(self.dir / "slices" / "log_line_0000.jsonl", path, inst, store=self.store)

    def label(self, uid, quote):
        return {"id": uid, "tier": 3, "dir": "for_goal", "issues": ["saturation"], "targets": [], "role": "error",
                "reason": "r", "quote": quote}

    def test_a_quote_in_the_extraction_but_not_in_the_original_at_the_locator_fails(self):
        doc = [{"key": "app.log", "bytes": LOG, "media_type": "text/plain"}]
        good = self.run_labels(doc, [unit(1, "pool exhausted: waited 30000ms")], [self.label("u1", "pool exhausted: waited")])
        self.assertTrue(good["ok"], good["errors"])
        self.assertEqual(good["original_checked"], 1)
        # the extraction says something line 1 of the original does not (it is line 2's text)
        bad = self.run_labels(doc, [unit(1, "retrying upstream call")], [self.label("u1", "retrying upstream call")])
        self.assertFalse(bad["ok"])
        self.assertEqual([(e["unit"], e["field"]) for e in bad["errors"]], [("u1", "original")])
        self.assertIn("not found word for word in the original", bad["errors"][0]["error"])

    def test_a_missing_original_is_an_error_not_a_skip(self):
        doc = [{"key": "app.log", "bytes": LOG, "media_type": "text/plain"}]
        intake.write_units(Adapter(doc, [unit(1, "pool exhausted")]), self.dir, instance(), store=self.store)
        self.files()[0].unlink()
        chunker.chunk(self.dir / "units.jsonl", self.dir, instance())
        path = self.dir / "labels.jsonl"
        path.write_text(json.dumps(self.label("u1", "pool exhausted")) + "\n", encoding="utf-8")
        report = validator.validate(self.dir / "slices" / "log_line_0000.jsonl", path, instance(), store=self.store)
        self.assertFalse(report["ok"])
        self.assertIn("MISSING", report["errors"][0]["error"])

    def test_what_cannot_be_checked_is_counted_by_reason_never_as_checked(self):
        docs = [{"key": "scan.png", "bytes": PNG, "media_type": "image/png"}]
        report = self.run_labels(docs, [unit(1, "pool exhausted", document="scan.png")], [self.label("u1", "pool exhausted")])
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["original_checked"], 0)
        self.assertEqual(report["original_not_checkable"], {"binary original": 1})


class ConfigTests(unittest.TestCase):
    def test_originals_is_human_owned_and_checked(self):
        data = config.parse_yaml(DEMO.read_text(encoding="utf-8"))
        self.assertEqual(originals.mode(config.load(data)), "required")
        data["human_owned"].remove("originals")
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("originals", str(err.exception))
        data = config.parse_yaml(DEMO.read_text(encoding="utf-8"))
        data["originals"] = {"mode": "sometimes", "spans": {"log_line": {"line": "page"}}}
        with self.assertRaises(config.ConfigError) as err:
            config.load(data)
        self.assertIn("originals.mode", str(err.exception))
        self.assertIn("'page' is not a locator field", str(err.exception))

    def test_an_instance_that_declares_nothing_is_undeclared_not_required_and_says_so(self):
        data = config.parse_yaml(DEMO.read_text(encoding="utf-8"))
        del data["originals"]
        data["human_owned"].remove("originals")
        self.assertEqual(originals.mode(config.load(data)), originals.UNDECLARED)


if __name__ == "__main__":
    unittest.main()
