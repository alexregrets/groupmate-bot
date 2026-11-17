import socket
from pathlib import Path

import dotenv
import pytest


def test_dotenv_loading_disabled():
    assert dotenv.load_dotenv() is False
    assert dotenv.dotenv_values() == {}


def test_production_env_and_state_blocked():
    root = Path(__file__).resolve().parent.parent
    for path in (root / ".env", root / "state" / "groupmate.db"):
        with pytest.raises(PermissionError, match="Production"):
            path.read_bytes()


def test_external_dns_and_socket_blocked():
    with pytest.raises(PermissionError, match="DNS"):
        socket.getaddrinfo("example.test", 443)
    with socket.socket() as sock:
        with pytest.raises(PermissionError, match="Network"):
            sock.connect(("192.0.2.1", 443))
