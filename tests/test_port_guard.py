"""Nothing here borrows a server that is already listening.

Found in use, twice on one day: a leftover throwaway server on a suite's port made seven tests
fail with `record already exists`, and a second session held 8025 -- the port this repo's own
README uses for the demo -- for about half an hour. The failure that matters is not the noisy one.
`scripts/run_instance.py` would have run the entire demo against that session's database and
reported success, because our `surreal start` fails to bind quietly and the health poll that
follows is answered by the stranger.

No SurrealDB binary needed: a plain listening socket is enough to occupy a port.
"""
import contextlib
import importlib.util
import pathlib
import socket
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from xscholar import surreal_bin  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_run_instance():
    spec = importlib.util.spec_from_file_location("run_instance", ROOT / "scripts" / "run_instance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_every_suite_that_starts_a_server_checks_its_port_first(self):
        """A new server suite that forgets the guard is the defect coming back."""
        for path in sorted(ROOT.glob("tests/test_*.py")):
            text = path.read_text(encoding="utf-8")
            if "surreal_bin.MISSING" not in text:
                continue   # not a suite that starts a server
            with self.subTest(suite=path.name):
                self.assertIn("surreal_bin.port_in_use(PORT)", text,
                              "%s starts a throwaway server without refusing an occupied port" % path.name)

    def test_no_two_suites_share_a_port(self):
        ports = {}
        for path in sorted(ROOT.glob("tests/test_*.py")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("PORT = "):
                    port = int(line.split("=")[1].strip())
                    self.assertNotIn(port, ports, "%s and %s both use port %d" % (ports.get(port), path.name, port))
                    ports[port] = path.name
        self.assertTrue(ports)


if __name__ == "__main__":
    unittest.main()
