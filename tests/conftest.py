import ipaddress
import os
import socket

import pytest
from fastapi.testclient import TestClient

# Establish a safe test environment before test modules import application settings.
# The app exposes OpenAPI only in development, which the existing contract tests
# exercise. Test-mode Settings are verified separately without loading .env.
os.environ["APP_ENV"] = "development"
os.environ["CHATBOT_AI_ENABLED"] = "false"
os.environ["CHATBOT_AI_PROVIDER"] = "noop"
os.environ["CHATBOT_AI_EMBEDDINGS_ENABLED"] = "false"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("CHATBOT_AI_API_KEY", None)

from app.core.config import Settings, get_settings  # noqa: E402

Settings.model_config = Settings.model_config | {"env_file": None}
get_settings.cache_clear()


def _is_loopback_host(host: object) -> bool:
    if host is None:
        return True
    value = str(host).strip("[]").casefold()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked_hosts: list[str] = []
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if not _is_loopback_host(host):
            blocked_hosts.append(str(host))
            raise AssertionError("External network access is disabled in tests")
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(sock: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_loopback_host(host):
            blocked_hosts.append(str(host))
            raise AssertionError("External network access is disabled in tests")
        return original_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
    assert not blocked_hosts, f"Tests attempted external connections: {blocked_hosts}"


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)
