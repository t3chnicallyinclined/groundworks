"""The sweep may only delete edges on the strength of a run that actually read labels.

Found by audit, not by use, and it is the worst defect this repo has had: `loader.load()` given a
labels directory with no `*.jsonl` files returned success and deleted every `bears_on` edge in the
scope. Measured on the demo instance's own units and labels, on the committed code:

    run 1, real labels : {'labels': 66, 'edges': 79, 'swept': 0}   bears_on = 79
    run 2, EMPTY dir   : {'labels': 0,  'edges': 0,  'swept': 79}  bears_on = 0

No exception. The module promises "a failed write blocks the sweep" and it does -- but writing zero
statements is not a failed write. A mistyped path, a .json/.jsonl slip, or a labelling step that
produced nothing was enough to wipe the evidence-to-conclusion edges and report success.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402
from xscholar import gates  # noqa: E402
from xscholar import surreal_bin  # noqa: E402
from xscholar.pipeline import chunker  # noqa: E402
from xscholar.pipeline import intake  # noqa: E402
from xscholar.pipeline import loader  # noqa: E402


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PORT = 8028
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "instances" / "demo-incident"
SURREAL = surreal_bin.find()


class SweepTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not SURREAL:
            raise unittest.SkipTest("NOT EXECUTED: " + surreal_bin.MISSING)
        if surreal_bin.port_in_use(PORT):
            raise unittest.SkipTest(surreal_bin.occupied(PORT))
        cls.proc = subprocess.Popen([str(SURREAL), "start", "--bind", "127.0.0.1:%d" % PORT,
                                     "--user", "root", "--pass", "root", "memory"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("NOT EXECUTED: throwaway SurrealDB did not start on %d" % PORT)
        cls.inst = config.load(DEMO / "instance.yaml")
        # Build the fixture rather than borrowing instances/demo-incident/out/. That directory is
        # written by the demo, which CI runs AFTER the suite, so depending on it made this test skip
        # in a fresh checkout -- silently, until the run summary started naming skips. A test that
        # cannot run where it matters is the defect this file is about.
        cls.fixture = tempfile.TemporaryDirectory()
        work = pathlib.Path(cls.fixture.name)
        adapter = _load(DEMO / "adapter.py")
        labeler = _load(DEMO / "labeler_stub.py")
        intake.write_units(adapter.Adapter(), work, cls.inst)
        chunks = chunker.chunk(work / "units.jsonl", work, cls.inst)
        labels = work / "labels"
        labels.mkdir()
        for chunk in chunks:
            rows = [labeler.label(u) for u in loader.read_units(work / "slices" / (chunk["id"] + ".jsonl"))]
            (labels / (chunk["id"] + ".jsonl")).write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        cls.units, cls.labels_dir = work / "units.jsonl", labels

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.conn = dbmod.Db(url="http://127.0.0.1:%d/sql" % PORT, ns="sweep", db=self.id().split(".")[-1][:60])
        self.conn.script(gates.schema_surql(self.inst))

    def edges(self):
        rows = self.conn.sql("SELECT count() AS n FROM bears_on GROUP ALL", expect=1)[0]
        return rows[0]["n"] if rows else 0

    def test_an_empty_labels_directory_keeps_every_edge_and_says_so(self):
        first = loader.load(self.__class__.units, self.__class__.labels_dir, self.conn, self.inst, scope="s")
        self.assertGreater(first["edges"], 0, "the demo's own labels must produce edges, or this proves nothing")
        before = self.edges()
        self.assertEqual(before, first["edges"])

        empty = self.work / "empty"
        empty.mkdir()
        second = loader.load(self.__class__.units, empty, self.conn, self.inst, scope="s")

        self.assertEqual(self.edges(), before, "an empty labels directory deleted edges")
        self.assertEqual(second["labels"], 0)
        self.assertIn("SKIPPED", str(second["sweep"]))
        self.assertIn("no *.jsonl", str(second["sweep"]))

    def test_a_missing_labels_directory_is_refused_not_treated_as_empty(self):
        loader.load(self.__class__.units, self.__class__.labels_dir, self.conn, self.inst, scope="s")
        before = self.edges()
        with self.assertRaises(loader.LoadError) as raised:
            loader.load(self.__class__.units, self.work / "typo", self.conn, self.inst, scope="s")
        self.assertIn("does not exist", str(raised.exception))
        self.assertEqual(self.edges(), before, "a mistyped path deleted edges")

    def test_a_directory_holding_only_the_wrong_extension_keeps_every_edge(self):
        """The .json/.jsonl slip: the directory exists and looks full, and matches nothing."""
        loader.load(self.__class__.units, self.__class__.labels_dir, self.conn, self.inst, scope="s")
        before = self.edges()
        wrong = self.work / "wrong_ext"
        wrong.mkdir()
        (wrong / "c1.json").write_text('{"id": "x", "tier": 0}\n', encoding="utf-8")
        result = loader.load(self.__class__.units, wrong, self.conn, self.inst, scope="s")
        self.assertEqual(self.edges(), before)
        self.assertIn("SKIPPED", str(result["sweep"]))

    def test_a_real_run_still_sweeps_the_edges_it_no_longer_claims(self):
        """The guard must not have turned the sweep off. An edge the new labels dropped still goes.

        An identical re-run sweeps nothing, and that is correct: edge ids are stable, so the same
        edges are re-written under the new run token and none are left stale. To see the sweep work,
        the second run has to stop claiming an edge.
        """
        first = loader.load(self.__class__.units, self.__class__.labels_dir, self.conn, self.inst, scope="s")
        before = self.edges()

        trimmed = self.work / "trimmed"
        trimmed.mkdir()
        dropped = 0
        for src in sorted(self.__class__.labels_dir.glob("*.jsonl")):
            lines = []
            for line in src.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if not dropped and len(row.get("targets") or []) == 1:
                    row["targets"] = []      # this label stops bearing on its conclusion
                    dropped = 1
                lines.append(json.dumps(row))
            (trimmed / src.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(dropped, 1, "no single-target label to drop; the fixture cannot prove the sweep")

        second = loader.load(self.__class__.units, trimmed, self.conn, self.inst, scope="s")
        self.assertEqual(second["swept"], 1, "the edge the new labels no longer claim was not swept")
        self.assertEqual(self.edges(), before - 1)
        self.assertEqual(second["edges"], first["edges"] - 1)


if __name__ == "__main__":
    unittest.main()
