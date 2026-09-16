"""Nothing here borrows a server that is already listening.

Found in use, twice on one day: a leftover throwaway server on a suite's port made seven tests
fail with `record already exists`, and a second session held 8025 -- the port this repo's own
README uses for the demo -- for about half an hour. The failure that matters is not the noisy one.
`scripts/run_instance.py` would have run the entire demo against that session's database and
reported success, because our `surreal start` fails to bind quietly and the health poll that
follows is answered by the stranger.

No SurrealDB binary needed: a plain listening socket is enough to occupy a port.
"""
import ast
import contextlib
import http.server
import importlib.util
import pathlib
import re
import socket
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import surreal_bin  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_run_instance():
    spec = importlib.util.spec_from_file_location("run_instance", ROOT / "scripts" / "run_instance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Throwaway ports for this suite's own stand-in servers. The suites own 8023-8034, 8025 is the README
# demo port, 8040 is CI's baseline port, 8001-8003 are live databases -- and 8050-8079 are handed to
# agents for their own throwaway servers, which is why these moved out of that range: DOOMED asserts
# its port is free, so an agent's server on 8053 would have failed this suite for no reason of its own.
STRANGER = 8036
DOOMED = 8037


class _Answers(http.server.BaseHTTPRequestHandler):
    """A stranger on the port. It answers /health exactly as a SurrealDB would."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def stranger_on(port):
    server = http.server.HTTPServer(("127.0.0.1", port), _Answers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _calls(node, name):
    return any(isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) == name
               for n in ast.walk(node))


@contextlib.contextmanager
def occupied_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


class PortGuardTests(unittest.TestCase):

    def test_port_in_use_sees_a_listener_and_a_free_port(self):
        with occupied_port() as port:
            self.assertTrue(surreal_bin.port_in_use(port))
        self.assertFalse(surreal_bin.port_in_use(port), "the port is free again once the listener closes")

    def test_the_message_says_to_stop_it_by_pid_not_by_name(self):
        text = surreal_bin.occupied(8025)
        self.assertIn("8025", text)
        self.assertIn("NOT EXECUTED", text)
        self.assertIn("by PID (never by name)", text)

    def test_the_demo_runner_refuses_an_occupied_port_instead_of_using_it(self):
        run_instance = load_run_instance()
        with occupied_port() as port:
            with self.assertRaises(SystemExit) as raised:
                run_instance.start_memory(port)
        self.assertIn("already in use", str(raised.exception))
        self.assertIn(str(port), str(raised.exception))

    def test_start_memory_will_not_trust_a_health_answer_from_a_server_it_did_not_start(self):
        """The residual race, reproduced: the port was free when it was checked and taken a moment
        later, so our own `surreal start` cannot bind and dies -- but the stranger answers /health
        first, and the whole demo used to run against their database and report success. Measured
        before the fix: start_memory returned a process whose poll() was still None."""
        run_instance = load_run_instance()
        run_instance.SURREAL = sys.executable   # stands in for a server that cannot bind and exits
        with stranger_on(STRANGER):
            with mock.patch.object(surreal_bin, "port_in_use", return_value=False):
                with self.assertRaises(SystemExit) as raised:
                    run_instance.start_memory(STRANGER)
        self.assertIn("NOT EXECUTED", str(raised.exception))
        self.assertIn("our own SurrealDB exited", str(raised.exception))
        self.assertIn("by PID (never by name)", str(raised.exception))

    def test_start_memory_says_so_loudly_when_its_own_server_exits_at_once(self):
        run_instance = load_run_instance()
        run_instance.SURREAL = sys.executable
        self.assertFalse(surreal_bin.port_in_use(DOOMED), "this test needs %d free" % DOOMED)
        with self.assertRaises(SystemExit) as raised:
            run_instance.start_memory(DOOMED)
        self.assertIn("exited at once", str(raised.exception))

    def test_port_in_use_sees_a_listener_bound_to_every_interface(self):
        """A stranger who binds 0.0.0.0 holds 127.0.0.1 too, and our server will not bind."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("", 0))
        sock.listen(1)
        try:
            self.assertTrue(surreal_bin.port_in_use(sock.getsockname()[1]))
        finally:
            sock.close()

    def test_every_suite_that_starts_a_server_refuses_an_occupied_port(self):
        """A new server suite that forgets the guard is the defect coming back. Reading the source
        for the call is not enough: the guard is the `if` and the `raise`, and a call whose result
        goes nowhere reads exactly like one that stops the suite."""
        checked = 0
        for path in sorted(ROOT.glob("tests/test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if not _calls(tree, "Popen"):
                continue   # not a suite that starts its own server
            checked += 1
            with self.subTest(suite=path.name):
                guards = [n for n in ast.walk(tree)
                          if isinstance(n, ast.If) and _calls(n.test, "port_in_use")
                          and any(isinstance(inner, ast.Raise) for inner in ast.walk(n))]
                self.assertTrue(guards, "%s starts a throwaway server without refusing an occupied "
                                        "port and stopping" % path.name)
        self.assertGreater(checked, 1, "no suite was examined; the walk found nothing to check")

    def suite_ports(self):
        ports = {}
        for path in sorted(ROOT.glob("tests/test_*.py")):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "PORT" for t in node.targets):
                    ports.setdefault(node.value.value, []).append(path.name)
        return ports

    def test_no_two_suites_share_a_port(self):
        ports = self.suite_ports()
        self.assertTrue(ports)
        for port, users in ports.items():
            self.assertEqual(len(users), 1, "%s all use port %d" % (" and ".join(users), port))

    def test_no_suite_uses_a_port_that_belongs_to_something_else(self):
        """8001-8003 are live databases and killing or writing into one has cost real data; 8025 is
        the README demo port and 8040 is CI's baseline port. The suites own 8023-8034."""
        # 8023-8039: the original 8023-8034 filled up (11 suites), so it was widened. The stand-in
        # servers this file starts are reserved inside that window so no suite is handed one.
        reserved = {8001: "a live database", 8002: "a live database", 8003: "a live database",
                    8025: "the README demo port", 8040: "CI's gate-baseline port",
                    STRANGER: "this file's stand-in stranger server", DOOMED: "this file's doomed-server port"}
        for port, users in self.suite_ports().items():
            with self.subTest(port=port):
                self.assertNotIn(port, reserved, "%s uses %d, which is %s"
                                 % (users[0], port, reserved.get(port)))
                self.assertTrue(8023 <= port <= 8039, "%s uses %d, outside the suites' range 8023-8039"
                                % (users[0], port))

    def test_no_test_names_a_live_database_anywhere(self):
        """Not only `PORT = `: a URL string naming a live database in any test.

        The check above reads PORT assignments only, and missed tests/test_db.py building
        `Db(url="http://127.0.0.1:8002/sql")` -- 8002 being a live database. It never connected,
        because `version` was stubbed. But a test that names live data is one careless edit away from
        reading or writing it, and a beta tester's instance lost a check exactly that way: its
        fixture was a real row, a research run changed it, and the test silently started passing
        what it should have rejected."""
        live = re.compile(r"(?:127\.0\.0\.1|localhost):(800[123])\b")
        offenders = []
        for path in sorted(ROOT.glob("tests/test_*.py")):
            if path.name == "test_port_guard.py":
                continue   # this file names them, to forbid them
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for port in live.findall(line):
                    offenders.append("%s:%d names live database port %s" % (path.name, number, port))
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
