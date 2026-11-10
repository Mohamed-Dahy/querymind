"""The SSRF guard for users' own databases (5.9): only public addresses, only database ports,
names libpq can't misread, and the connection goes to the exact address that was checked.

DNS is replaced in the tests that use host names; IP literals (and their odd spellings) go
through the real resolver, which turns them into numbers without asking a DNS server.
"""

import ipaddress
import socket
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr, ValidationError

from querymind.db.errors import (
    RemoteLoginError,
    RemoteTlsError,
    RemoteUnreachableError,
    UnsafeConnectionError,
)
from querymind.db.remote import ConnectionSpec, check_address, connect_remote, is_public

PUBLIC = "93.184.216.34"  # a public address (documentation ranges are not "global")
PASSWORD = SecretStr("correct-horse-battery-staple")


def resolves_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> list[str]:
    """Make every name resolve to these addresses. Returns the names that were looked up."""
    asked: list[str] = []

    def fake_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> list[Any]:
        asked.append(host)
        family = socket.AF_INET6
        return [
            (socket.AF_INET if ":" not in a else family, socket.SOCK_STREAM, 6, "", (a, port))
            for a in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return asked


# --- Which addresses are public ------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback: our own server
        "10.1.2.3",  # private networks
        "172.16.0.9",
        "192.168.1.10",
        "169.254.169.254",  # the cloud metadata service: hands out the server's credentials
        "100.64.0.1",  # shared (carrier-grade NAT)
        "0.0.0.0",  # "this host"
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "::",  # IPv6 "any"
        "fe80::1",  # link-local
        "fd00:ec2::254",  # AWS's IPv6 metadata address (unique local)
        "::ffff:10.0.0.1",  # IPv4-mapped: 10.0.0.1 in disguise
        "2002:a00:1::",  # 6to4 of 10.0.0.1
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "2001:db8::1",  # documentation
    ],
)
def test_internal_and_special_addresses_are_not_public(address: str) -> None:
    assert not is_public(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are(address: str) -> None:
    assert is_public(ipaddress.ip_address(address))


# --- check_address -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "2130706433", "0x7f000001", "127.1", "0", "::1", "169.254.169.254"],
)
def test_odd_spellings_of_loopback_never_pass(host: str) -> None:
    """Resolve first, check after: "2130706433" or "127.1" become 127.0.0.1 before the check (or
    don't resolve at all). Either way, refused."""
    with pytest.raises((UnsafeConnectionError, RemoteUnreachableError)):
        check_address(host, 5432)


def test_a_name_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolves_to(monkeypatch, "10.0.0.5")

    with pytest.raises(UnsafeConnectionError, match="public internet"):
        check_address("db.attacker.example", 5432)


def test_one_private_address_among_public_ones_is_enough_to_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolves_to(monkeypatch, PUBLIC, "127.0.0.1")

    with pytest.raises(UnsafeConnectionError):
        check_address("db.example.com", 5432)


def test_a_public_name_gives_the_address_to_connect_to(monkeypatch: pytest.MonkeyPatch) -> None:
    resolves_to(monkeypatch, "2606:4700:4700::1111", PUBLIC)

    assert check_address("db.example.com", 5432) == PUBLIC  # IPv4 first: the same answer each time


@pytest.mark.parametrize("port", [22, 80, 443, 6379, 8080, 5431, 5440])
def test_only_database_ports(port: int) -> None:
    with pytest.raises(UnsafeConnectionError, match="PostgreSQL ports"):
        check_address(PUBLIC, port)


@pytest.mark.parametrize("port", [5432, 5433, 5439, 6432, 6543])
def test_postgres_and_pooler_ports_are_allowed(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    resolves_to(monkeypatch, PUBLIC)

    assert check_address("db.example.com", port) == PUBLIC


def test_development_mode_allows_private_addresses() -> None:
    assert check_address("127.0.0.1", 5433, allow_private=True) == "127.0.0.1"


def test_an_unknown_name_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(RemoteUnreachableError):
        check_address("no-such-host.example", 5432)


# --- What a user may type ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"dbname": "host=10.0.0.5"},  # libpq could read it as a connection setting
        {"dbname": "postgresql://evil@10.0.0.5/x"},
        {"user": "reader password=x"},
        {"user": "it's"},
        {"host": "db.example.com/x"},
        {"host": "user@db.example.com"},
        {"host": "[::1]"},
        {"host": "db.example.com host=10.0.0.5"},
    ],
)
def test_names_libpq_could_misread_are_refused(fields: dict[str, str]) -> None:
    base = {"host": "db.example.com", "port": 5432, "dbname": "sales", "user": "reader"}

    with pytest.raises(ValidationError):
        ConnectionSpec.model_validate(base | fields)


# --- connect_remote ------------------------------------------------------------------------------


class Recorder:
    """Stands in for psycopg.connect: keeps the connection string, returns a fake connection."""

    def __init__(self, error: Exception | None = None) -> None:
        self.conninfo: dict[str, str] = {}
        self.error = error

    def __call__(self, conninfo: str) -> object:
        self.conninfo = psycopg.conninfo.conninfo_to_dict(conninfo)  # type: ignore[assignment]
        if self.error is not None:
            raise self.error
        return type("FakeConnection", (), {"read_only": False})()


SPEC = ConnectionSpec(host="db.example.com", port=5432, dbname="sales", user="reader")


def test_it_connects_to_the_checked_address_with_tls_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = resolves_to(monkeypatch, PUBLIC)
    recorder = Recorder()
    monkeypatch.setattr(psycopg, "connect", recorder)

    conn = connect_remote(SPEC, PASSWORD)

    info = recorder.conninfo
    assert info["hostaddr"] == PUBLIC  # DNS can't change between the check and the connect
    assert info["host"] == "db.example.com"  # still used to check the certificate
    assert (info["sslmode"], info["sslrootcert"]) == ("verify-full", "system")
    assert conn.read_only is True  # every transaction BEGIN READ ONLY
    assert asked == ["db.example.com"]


def test_it_checks_the_address_again_at_every_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: public when saved, private later. The second connect is refused."""
    resolves_to(monkeypatch, PUBLIC)
    monkeypatch.setattr(psycopg, "connect", Recorder())
    connect_remote(SPEC, PASSWORD)

    resolves_to(monkeypatch, "169.254.169.254")
    with pytest.raises(UnsafeConnectionError):
        connect_remote(SPEC, PASSWORD)


@pytest.mark.parametrize("sslmode", ["prefer", "disable"])
def test_unencrypted_is_for_development_only(monkeypatch: pytest.MonkeyPatch, sslmode: str) -> None:
    monkeypatch.setattr(psycopg, "connect", Recorder())
    spec = SPEC.model_copy(update={"sslmode": sslmode})

    with pytest.raises(UnsafeConnectionError, match="encrypted"):
        connect_remote(spec, PASSWORD)


@pytest.mark.parametrize(
    ("message", "error", "says"),
    [
        ('password authentication failed for user "reader"', RemoteLoginError, "password"),
        ('database "sales" does not exist', RemoteLoginError, "no database"),
        ("server does not support SSL, but SSL was required", RemoteTlsError, "encrypted"),
        ("SSL error: certificate verify failed", RemoteTlsError, "self-signed"),
        ("connection timeout expired", RemoteUnreachableError, "Couldn't reach"),
        (
            'connection to server at "10.0.0.5", port 5432 failed: refused',
            RemoteUnreachableError,
            "",
        ),
    ],
)
def test_connect_failures_say_what_to_fix_and_nothing_more(
    monkeypatch: pytest.MonkeyPatch, message: str, error: type[Exception], says: str
) -> None:
    resolves_to(monkeypatch, PUBLIC)
    monkeypatch.setattr(psycopg, "connect", Recorder(psycopg.OperationalError(message)))

    with pytest.raises(error) as failed:
        connect_remote(SPEC, PASSWORD)

    assert says in str(failed.value)
    assert "10.0.0.5" not in str(failed.value)  # the server's text stays in our log
