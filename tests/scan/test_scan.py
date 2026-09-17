# -*- coding: utf-8 -*-
"""Fixture-tree tests for xscholar/scan.py: build a real (small) tree with git-init'd repos, a real
`git worktree add`, a nested repo, a fake secret file, a local-only folder and a node_modules dir,
then assert every field the ticket lists -- and that .env content never reaches the output, and that
a local-only record carries nothing beyond path/kind/head_sha/local_only.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from xscholar import scan  # noqa: E402

SECRET_VALUE = "sk-not-a-real-secret-ABCDEF123456"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True, text=True)


def _init_repo(path, remote=None, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


def _commit_all(path, message="commit"):
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)


class ScanFixtureTests(unittest.TestCase):
    """One shared fixture tree, built once, scanned once; each test checks one slice of the result."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)

        # repo1: a real git repo with a remote, a pyproject.toml (-> pytest candidate), a README,
        # a fake .env (secret flag, content must never surface), and a node_modules dir to be skipped.
        cls.repo1 = _init_repo(cls.root / "repo1", remote="https://github.com/example/repo1.git")
        (cls.repo1 / "pyproject.toml").write_text("[project]\nname='repo1'\n", encoding="utf-8")
        (cls.repo1 / "README.md").write_text("# repo1\n", encoding="utf-8")
        (cls.repo1 / ".env").write_text("TOKEN=%s\n" % SECRET_VALUE, encoding="utf-8")
        node_modules = cls.repo1 / "node_modules" / "somepkg"
        node_modules.mkdir(parents=True)
        (node_modules / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
        _commit_all(cls.repo1, "initial")

        # nested repo: its own git repo, INSIDE repo1, with NO remote -> local_only via the
        # no-remote rule (not the glob rule).
        cls.nested = _init_repo(cls.repo1 / "vendor" / "nestedrepo")
        (cls.nested / "file.txt").write_text("nested\n", encoding="utf-8")
        _commit_all(cls.nested, "nested initial")

        # worktree1: a REAL `git worktree add` off repo1, on its own branch.
        cls.worktree1 = cls.root / "worktree1"
        _git(cls.repo1, "worktree", "add", "-q", "-b", "wt-branch", str(cls.worktree1))

        # submodule: a REAL `git submodule add` inside repo1 -- must NOT be classified as a worktree
        # (both use a `.git` FILE, but the pointer target differs: .../worktrees/ vs .../modules/).
        cls.upstream = _init_repo(pathlib.Path(cls._tmp.name).parent / ("upstream-" + cls.__name__))
        (cls.upstream / "f.txt").write_text("upstream\n", encoding="utf-8")
        _commit_all(cls.upstream, "upstream initial")
        _git(cls.repo1, "-c", "protocol.file.allow=always", "submodule", "add",
             str(cls.upstream), "vendor/submod")
        cls.submodule = cls.repo1 / "vendor" / "submod"

        # plainfolder: no .git anywhere, has docs/*.md and a Cargo.toml; must NOT be forced
        # local-only just because it (like every plain folder) has no remote.
        cls.plain = cls.root / "plainfolder"
        (cls.plain / "docs").mkdir(parents=True)
        (cls.plain / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
        (cls.plain / "Cargo.toml").write_text("[package]\nname='p'\n", encoding="utf-8")

        # local-only folder: matches the default --local-only glob ("*Family court*").
        cls.local_only_dir = cls.root / "Family court records"
        cls.local_only_dir.mkdir(parents=True)
        (cls.local_only_dir / "notes.txt").write_text("private\n", encoding="utf-8")

        cls.records = scan.scan_root(cls.root)
        cls.by_path = {r["path"]: r for r in cls.records}
        cls.raw_json = json.dumps(cls.records)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        shutil.rmtree(cls.upstream, ignore_errors=True)

    def test_repo1_git_facts(self):
        rec = self.by_path[str(self.repo1)]
        self.assertEqual(rec["kind"], "git repo")
        self.assertEqual(rec["branch"], "main")
        self.assertRegex(rec["head_sha"], r"^[0-9a-f]{40}$")
        # dirty=True is correct here: the nested repo directory created after the commit is an
        # untracked path from repo1's own point of view (it was never added as a submodule).
        self.assertTrue(rec["dirty"])
        self.assertEqual(rec["remotes"], ["github.com/example/repo1"])
        self.assertIsNotNone(rec["last_commit_time"])
        self.assertFalse(rec["local_only"])

    def test_repo1_build_files_and_candidate_gates(self):
        rec = self.by_path[str(self.repo1)]
        self.assertIn("pyproject", rec["build_files"])
        self.assertIn("pyproject.toml", rec["build_files"]["pyproject"])
        self.assertIn("pytest", rec["candidate_gates"])

    def test_repo1_docs(self):
        rec = self.by_path[str(self.repo1)]
        doc_paths = [d["path"] for d in rec["docs"]]
        self.assertIn("README.md", doc_paths)
        for doc in rec["docs"]:
            if doc["path"] == "README.md":
                self.assertEqual(doc["size"], (self.repo1 / "README.md").stat().st_size)

    def test_secret_flag_path_only_never_content(self):
        rec = self.by_path[str(self.repo1)]
        self.assertIn(".env", rec["secret_flags"])
        self.assertNotIn(SECRET_VALUE, self.raw_json)

    def test_node_modules_skipped(self):
        rec = self.by_path[str(self.repo1)]
        for build_list in rec["build_files"].values():
            for relpath in build_list:
                self.assertNotIn("node_modules", relpath)
        for doc in rec["docs"]:
            self.assertNotIn("node_modules", doc["path"])
        self.assertNotIn(str(self.repo1 / "node_modules"), self.by_path)

    def test_worktree_points_at_main_repo(self):
        rec = self.by_path[str(self.worktree1)]
        self.assertEqual(rec["kind"], "git worktree")
        self.assertEqual(rec["main_repo"], str(self.repo1))
        self.assertEqual(rec["branch"], "wt-branch")
        self.assertEqual(rec["remotes"], ["github.com/example/repo1"])
        self.assertFalse(rec["local_only"])

    def test_submodule_not_classified_as_worktree(self):
        rec = self.by_path[str(self.submodule)]
        self.assertEqual(rec["kind"], "git submodule")
        # git's own --show-superproject-working-tree answers with forward slashes even on Windows.
        self.assertEqual(pathlib.PureWindowsPath(rec["main_repo"]).as_posix(), self.repo1.as_posix())

    def test_nested_repo_found_and_local_only_by_no_remote(self):
        rec = self.by_path[str(self.nested)]
        self.assertEqual(rec["kind"], "git repo")
        self.assertTrue(rec["local_only"])
        self.assertEqual(set(rec.keys()), {"path", "kind", "head_sha", "local_only"})
        self.assertRegex(rec["head_sha"], r"^[0-9a-f]{40}$")

    def test_plain_folder_not_forced_local_only(self):
        rec = self.by_path[str(self.plain)]
        self.assertEqual(rec["kind"], "plain folder")
        self.assertFalse(rec["local_only"])
        self.assertIn("cargo", rec["build_files"])
        doc_paths = [d["path"] for d in rec["docs"]]
        self.assertIn(str(pathlib.Path("docs") / "guide.md"), doc_paths)

    def test_local_only_glob_minimal_record(self):
        rec = self.by_path[str(self.local_only_dir)]
        self.assertTrue(rec["local_only"])
        self.assertEqual(set(rec.keys()), {"path", "kind", "head_sha", "local_only"})
        self.assertNotIn("private", self.raw_json)

    def test_every_top_level_child_has_a_record(self):
        top_level_paths = {str(p) for p in self.by_path if pathlib.Path(p).parent == self.root}
        expected = {str(self.repo1), str(self.worktree1), str(self.plain), str(self.local_only_dir)}
        self.assertEqual(top_level_paths, expected)

    def test_local_only_folder_not_descended_into(self):
        # a nested git repo INSIDE the local-only folder must produce no record at all: local-only
        # means the scan never walks that folder's contents, not just that it minimizes what it finds.
        nested_in_local = self.local_only_dir / "inner" / "nestedrepo"
        _init_repo(nested_in_local)
        (nested_in_local / "f.txt").write_text("x\n", encoding="utf-8")
        _commit_all(nested_in_local, "inner initial")
        try:
            records = scan.scan_root(self.root)
            by_path = {r["path"]: r for r in records}
            self.assertNotIn(str(nested_in_local), by_path)
            self.assertNotIn(str(self.local_only_dir / "inner"), by_path)
            rec = by_path[str(self.local_only_dir)]
            self.assertEqual(set(rec.keys()), {"path", "kind", "head_sha", "local_only"})
        finally:
            shutil.rmtree(self.local_only_dir / "inner", ignore_errors=True)

    def test_build_underscore_and_dot_history_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = _init_repo(root / "repoB", remote="https://github.com/example/repoB.git")
            skipped_a = repo / "build_w6100" / "_deps" / "somevendor"
            skipped_a.mkdir(parents=True)
            (skipped_a / "CMakeLists.txt").write_text("", encoding="utf-8")
            skipped_b = repo / ".history" / "old"
            skipped_b.mkdir(parents=True)
            (skipped_b / "Cargo.toml").write_text("", encoding="utf-8")
            _commit_all(repo, "init")

            records = scan.scan_root(root)
            by_path = {r["path"]: r for r in records}
            self.assertNotIn(str(skipped_a), by_path)
            self.assertNotIn(str(repo / "build_w6100"), by_path)
            self.assertNotIn(str(skipped_b), by_path)
            self.assertNotIn(str(repo / ".history"), by_path)
            rec = by_path[str(repo)]
            self.assertNotIn("cmake", rec["build_files"])
            self.assertNotIn("cargo", rec["build_files"])

    def test_git_status_does_not_touch_index(self):
        # the BLOCKING review finding: plain `git status` opportunistically refreshes and rewrites
        # .git/index. Use a fresh, dirty (untracked file present) repo -- not the shared class
        # fixture, whose index has already been touched by setUpClass's own scan -- so this measures
        # a clean before/after around exactly the calls scan.py makes.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(pathlib.Path(tmp) / "repo")
            (repo / "tracked.txt").write_text("a\n", encoding="utf-8")
            _commit_all(repo, "init")
            (repo / "untracked.txt").write_text("b\n", encoding="utf-8")  # keeps status non-empty

            index_path = repo / ".git" / "index"
            before = index_path.stat().st_mtime_ns
            scan.git_info(repo)
            after = index_path.stat().st_mtime_ns

            self.assertEqual(before, after)
            self.assertFalse((repo / ".git" / "index.lock").exists())


class HostPathFromRemoteTests(unittest.TestCase):

    def test_https(self):
        self.assertEqual(scan.host_path_from_remote("https://github.com/user/repo.git"), "github.com/user/repo")

    def test_ssh_scp_style(self):
        self.assertEqual(scan.host_path_from_remote("git@github.com:user/repo.git"), "github.com/user/repo")

    def test_ssh_url_style(self):
        self.assertEqual(scan.host_path_from_remote("ssh://git@example.com/group/repo.git"), "example.com/group/repo")

    def test_no_credentials_leak_into_output(self):
        # a URL with embedded credentials must not carry them into the host/path result
        result = scan.host_path_from_remote("https://user:secrettoken@github.com/user/repo.git")
        self.assertNotIn("secrettoken", result)


if __name__ == "__main__":
    unittest.main()
