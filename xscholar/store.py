# -*- coding: utf-8 -*-
"""store.py -- the canonical store of originals: exact bytes, addressed by their sha256, verified on read.

Evidence you cannot retrieve is not evidence. A unit's text is an extraction; a URL is a pointer that
changes and disappears. What a unit came from is the byte-exact original, kept here under the sha256 of
those bytes, so an original is:

  immutable        its name is its content; different bytes are a different original
  deduplicated     the same bytes put twice are stored once, under one name
  self-verifying   every get() re-hashes what it read, and a mismatch is CorruptOriginal naming the
                   original -- never silently served

    store = FileStore("/path/to/instance/originals")
    sha = store.put(b"...")               # the sha256 hex of the bytes; idempotent
    sha = store.put_file("scan.pdf")      # streamed, for large files
    data = store.get(sha, name="scan.pdf")  # bytes, or MissingOriginal / CorruptOriginal (named)
    store.verify(sha)                     # "ok" | "missing" | "corrupt"
    for sha in store.hashes(): ...        # every original the store holds (the verify walk)

A backend is anything implementing `Store` (put, put_file, get, has, verify, hashes). The default,
`FileStore`, needs the standard library and a directory: no service, no Docker, no extra process, which
suits "almost anyone, many projects". Others register with `register_backend(name, factory)` and are
named in instance.yaml `originals.store.backend`.

Privacy. Originals are an instance's most sensitive files. A FileStore:
  * writes a `.gitignore` of `*` into its own root before it writes a single original, so the store is
    ignored by git in whatever repository it happens to sit in -- including one nobody configured;
  * refuses a root inside the engine repository anywhere but under `instances/` (the demo's synthetic
    data), so a private instance's originals cannot be put next to engine code by a mistyped path.
Nothing in here assumes an original is text: a scanned page, an image or a binary export is stored,
addressed and verified exactly as a log file is.
Standard library only.
"""
import abc
import hashlib
import os
import pathlib
import re
import tempfile

HEX = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20
ENGINE_ROOT = pathlib.Path(__file__).resolve().parents[1]
GITIGNORE = "# written by xscholar/store.py: originals never go into any repository\n*\n"
DEFAULT_PATH = "originals"


class StoreError(Exception):
    """The store could not do what was asked. Never a quiet empty result."""


class MissingOriginal(StoreError):
    def __init__(self, sha256, name=None, where=None):
        super().__init__("original %s%s is MISSING from the store%s: the unit(s) pointing at it have no "
                         "retrievable original" % (sha256, " (%s)" % name if name else "",
                                                   " at %s" % where if where else ""))
        self.sha256, self.name = sha256, name


class CorruptOriginal(StoreError):
    def __init__(self, sha256, actual, name=None, where=None):
        super().__init__("original %s%s is CORRUPT: the stored bytes%s hash to %s. Refusing to serve it; restore "
                         "the original from its source" % (sha256, " (%s)" % name if name else "",
                                                           " at %s" % where if where else "", actual))
        self.sha256, self.actual, self.name = sha256, actual, name


def sha256_bytes(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise StoreError("an original is bytes, not %s: encode text yourself so the bytes stored are the "
                         "bytes you mean" % type(data).__name__)
    return hashlib.sha256(bytes(data)).hexdigest()


def check_hex(sha256):
    if not isinstance(sha256, str) or not HEX.match(sha256):
        raise StoreError("%r is not a sha256 (64 lowercase hex characters)" % (sha256,))
    return sha256


class Store(abc.ABC):
    """What every backend provides. get() must verify; verify() must re-hash, not check existence."""

    @abc.abstractmethod
    def put(self, data):
        """Store bytes; return their sha256 hex. Idempotent. A stored copy that no longer matches its name
        is replaced by these bytes (which do match) and the repair is counted in `repaired`."""

    @abc.abstractmethod
    def put_file(self, path):
        """Store a file's bytes, streamed; return the sha256 hex."""

    @abc.abstractmethod
    def get(self, sha256, name=None):
        """The bytes, re-hashed. MissingOriginal or CorruptOriginal, naming `name`, otherwise."""

    @abc.abstractmethod
    def has(self, sha256):
        """True when something is stored under this name. Says nothing about whether it is intact."""

    @abc.abstractmethod
    def verify(self, sha256):
        """'ok', 'missing' or 'corrupt', by re-hashing."""

    @abc.abstractmethod
    def hashes(self):
        """Every sha256 the store holds, including corrupt ones (the walk needs to see them)."""

    def describe(self):
        return type(self).__name__


class FileStore(Store):
    """<root>/sha256/<first two hex>/<sha256>. Written to a temp file in the same directory tree, then
    renamed into place, so a crash leaves no half-written original under a real name."""

    def __init__(self, root, allow_engine_repo=False):
        self.root = pathlib.Path(root).expanduser().resolve()
        if not allow_engine_repo:
            refuse_engine_repo(self.root)
        self.repaired = 0

    def describe(self):
        return "file store at %s" % self.root

    def _path(self, sha256):
        check_hex(sha256)
        return self.root / "sha256" / sha256[:2] / sha256

    def _ready(self):
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists() or ignore.read_text(encoding="utf-8") != GITIGNORE:
            ignore.write_text(GITIGNORE, encoding="utf-8")
        (self.root / "tmp").mkdir(exist_ok=True)

    def _hash_file(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
        return digest.hexdigest()

    def _commit(self, tmp_path, sha256):
        target = self._path(sha256)
        if target.exists():
            if self._hash_file(target) == sha256:
                os.remove(tmp_path)
                return sha256
            self.repaired += 1          # the stored copy was damaged; these bytes are the true original
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, target)
        actual = self._hash_file(target)
        if actual != sha256:
            raise CorruptOriginal(sha256, actual, where=str(target))
        return sha256

    def put(self, data):
        sha256 = sha256_bytes(data)
        self._ready()
        fd, tmp = tempfile.mkstemp(dir=str(self.root / "tmp"))
        with os.fdopen(fd, "wb") as fh:
            fh.write(bytes(data))
        return self._commit(tmp, sha256)

    def put_file(self, path):
        source = pathlib.Path(path)
        if not source.is_file():
            raise StoreError("no file %s to store" % source)
        self._ready()
        digest = hashlib.sha256()
        fd, tmp = tempfile.mkstemp(dir=str(self.root / "tmp"))
        with os.fdopen(fd, "wb") as out, open(source, "rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
                out.write(block)
        return self._commit(tmp, digest.hexdigest())

    def get(self, sha256, name=None):
        path = self._path(sha256)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise MissingOriginal(sha256, name) from None    # no path: an MCP caller never sees the instance's directory
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise CorruptOriginal(sha256, actual, name)
        return data

    def has(self, sha256):
        return self._path(sha256).is_file()

    def verify(self, sha256):
        path = self._path(sha256)
        if not path.is_file():
            return "missing"
        return "ok" if self._hash_file(path) == sha256 else "corrupt"

    def hashes(self):
        base = self.root / "sha256"
        if not base.is_dir():
            return
        for sub in sorted(base.iterdir()):
            if not sub.is_dir():
                continue
            for item in sorted(sub.iterdir()):
                if item.is_file():
                    yield item.name if HEX.match(item.name) else "INVALID_NAME:%s" % item


def refuse_engine_repo(root):
    """A store may sit inside the engine repository only under instances/ (synthetic demo data)."""
    root = pathlib.Path(root).resolve()
    try:
        relative = root.relative_to(ENGINE_ROOT)
    except ValueError:
        return
    if not relative.parts or relative.parts[0] != "instances" or len(relative.parts) < 3:
        raise StoreError("refusing an originals store at %s: it is inside the engine repository %s and not under "
                         "instances/<name>/. Originals belong to an instance, in the instance's own directory"
                         % (root, ENGINE_ROOT))


BACKENDS = {"file": lambda spec, base: FileStore(_resolve(spec.get("path") or DEFAULT_PATH, base))}


def register_backend(name, factory):
    """factory(spec: dict, base_dir: pathlib.Path) -> Store."""
    BACKENDS[str(name)] = factory


def _resolve(path, base):
    path = pathlib.Path(str(path)).expanduser()
    return path if path.is_absolute() else pathlib.Path(base) / path


def open_store(spec, base_dir):
    """A Store from instance.yaml `originals.store`: a path (the file backend) or {backend, ...}."""
    if spec is None or isinstance(spec, str):
        spec = {"backend": "file", "path": spec or DEFAULT_PATH}
    if not isinstance(spec, dict):
        raise StoreError("originals.store must be a path or a mapping with a backend, not %r" % (spec,))
    backend = spec.get("backend", "file")
    if backend not in BACKENDS:
        raise StoreError("unknown originals store backend %r; registered: %s" % (backend, ", ".join(sorted(BACKENDS))))
    return BACKENDS[backend](spec, pathlib.Path(base_dir))


def for_instance(inst):
    """The instance's own store, resolved against the directory its instance.yaml is in."""
    if not getattr(inst, "source", None):
        raise StoreError("instance %s was not loaded from a file, so it has no directory to keep originals in; "
                         "pass a store explicitly" % inst.id)
    section = inst.data.get("originals")
    spec = section.get("store") if isinstance(section, dict) else None
    return open_store(spec, pathlib.Path(inst.source).resolve().parent)
