"""Run tests without dotenv, external sockets or production state access."""
import os
import sys
import tempfile
from pathlib import Path

# Reuse the isolated interpreter when invoked with system Python after setup.
root = Path(__file__).resolve().parent
venv_python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if sys.prefix == sys.base_prefix and venv_python.exists():
    import subprocess
    sys.exit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))

import dotenv
dotenv.load_dotenv = lambda *a, **k: False
dotenv.dotenv_values = lambda *a, **k: {}
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
sys.dont_write_bytecode = True


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.name == ".env" or path == root / "state" or root / "state" in path.parents:
            raise PermissionError("Production data access disabled for tests")
    if event in ("socket.connect", "socket.sendto"):
        address = args[1] if event == "socket.connect" else args[-1]
        # Windows asyncio uses loopback sockets internally. No external traffic is allowed.
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise PermissionError("Network disabled for tests")
    if event == "socket.getaddrinfo" and args[0] not in ("localhost", "127.0.0.1", "::1"):
        raise PermissionError("DNS disabled for tests")


sys.addaudithook(audit)
import pytest

with tempfile.TemporaryDirectory(prefix="groupmate-tests-") as temporary:
    sys.exit(pytest.main([str(root / "tests"), "-p", "no:cacheprovider", "--basetemp", temporary, *sys.argv[1:]]))
