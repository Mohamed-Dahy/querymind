"""Connecting to a user's own PostgreSQL safely (5.9, ADR 012).

    spec = ConnectionSpec(host="db.example.com", port=5432, dbname="sales", user="reader")
    with connect_remote(spec, password, allow_private=False) as conn:
        check_read_only(conn)   # refuses a login that can change anything

Two dangers, two guards:

**SSRF (server-side request forgery).** The host is typed by a user, but *our server* connects to
it. Without a guard, "host = 169.254.169.254" (the cloud's metadata service, which hands out the
server's credentials), "127.0.0.1" or "10.0.0.5" would make our server talk to things inside our
own network. So `check_address` resolves the name and refuses unless **every** address is public;
only database ports are allowed; and the connection then goes to the exact IP that was checked
(libpq's `hostaddr`), so the DNS answer can't change between the check and the connect (DNS
rebinding). It runs again at every connect.

**A login that can write.** The agent's SQL is validated to be a single SELECT, and every query
runs in a READ ONLY transaction (db/executor.py). A third layer: `check_read_only` refuses a login
that could change anything at all (role powers, membership in a powerful role, write grants on any
table or even one column, CREATE on the database or a schema). Read-only by design, not by luck.
"""

import ipaddress
import logging
import re
import socket
from typing import Literal

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from querymind.db.errors import (
    RemoteLoginError,
    RemoteTlsError,
    RemoteUnreachableError,
    UnsafeConnectionError,
    WritableLoginError,
)

logger = logging.getLogger(__name__)

# PostgreSQL's usual ports, and the two common poolers (PgBouncer 6432, Supabase 6543). Any other
# port is refused: the check must not become a way to probe SSH, Redis, internal web servers...
ALLOWED_PORTS = frozenset(range(5432, 5440)) | {6432, 6543}

# verify-full: encrypted, and the certificate must belong to this host (default: blocks a server
# pretending to be yours). require: encrypted, certificate not checked (self-signed hosts).
# prefer / disable may send the password unencrypted: development only (ALLOW_PRIVATE_DATABASES).
SslMode = Literal["verify-full", "require", "prefer", "disable"]
DEV_ONLY_SSL_MODES = frozenset({"prefer", "disable"})

# Names libpq takes as-is. No "=", spaces, quotes, "/" or ":": a database name like
# "host=10.0.0.5" or "postgresql://..." could otherwise be read by libpq as connection settings.
NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.$-]{0,62}$"
# A DNS name or an IP literal (IPv6 has colons). Brackets, "@", "/" and spaces are refused.
HOST_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$"

# IPv6 addresses that carry an IPv4 address inside (NAT64, RFC 6052): "64:ff9b::7f00:1" reaches
# 127.0.0.1 through a NAT64 gateway.
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")

CONNECT_TIMEOUT_SECONDS = 10
CHECK_TIMEOUT = "10s"  # the privilege scan on a database with many tables
SHOW_AT_MOST = 5  # offending names listed in a refusal, then "and N more"

UNREACHABLE = (
    "Couldn't reach the database at this host and port. Check them, and that the server accepts "
    "connections from the internet."
)
NOT_PUBLIC = (
    "This address isn't allowed: QueryMind only connects to databases on the public internet "
    "(not private, local or cloud-internal addresses)."
)


class ConnectionSpec(BaseModel):
    """Where a user's database is and how to log in (the password is kept apart, encrypted)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(pattern=HOST_PATTERN, examples=["db.example.com"])
    port: int = Field(default=5432, ge=1, le=65535)
    dbname: str = Field(pattern=NAME_PATTERN, examples=["sales"])
    user: str = Field(pattern=NAME_PATTERN, examples=["querymind_reader"])
    sslmode: SslMode = "verify-full"


def is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a globally routable address, including any IPv4 hidden inside an IPv6 one."""
    if isinstance(address, ipaddress.IPv6Address):
        hidden = [address.ipv4_mapped, address.sixtofour]  # ::ffff:10.0.0.1, 2002:0a00:0001::
        if address.teredo is not None:
            hidden.append(address.teredo[1])
        if address in _NAT64:
            hidden.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
        if any(inner is not None and not is_public(inner) for inner in hidden):
            return False
    # is_global is False for loopback, private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local
    # (169.254/16 with the metadata service, fe80::/10), shared 100.64/10, 0.0.0.0, documentation…
    return address.is_global and not address.is_multicast


def check_address(host: str, port: int, *, allow_private: bool = False) -> str:
    """The IP to connect to, after checking the port and every address the host resolves to.

    Raises:
        UnsafeConnectionError: a non-database port, or any resolved address isn't public.
        RemoteUnreachableError: the name doesn't resolve.
    """
    if port not in ALLOWED_PORTS:
        raise UnsafeConnectionError(
            "Only PostgreSQL ports are allowed: 5432 to 5439, or a pooler's 6432 / 6543."
        )
    if not re.fullmatch(HOST_PATTERN, host):
        raise UnsafeConnectionError("The host must be a plain name or IP address.")
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise RemoteUnreachableError(UNREACHABLE) from None
    # "fe80::1%eth0": the zone after "%" is not part of the address.
    addresses = {ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in found}
    if not addresses:
        raise RemoteUnreachableError(UNREACHABLE)
    # Every address, not only the first: a name with one public and one private address could
    # otherwise be checked on one and connected on the other.
    if not allow_private and not all(is_public(address) for address in addresses):
        logger.warning("connection refused: not public", extra={"host": host, "port": port})
        raise UnsafeConnectionError(NOT_PUBLIC)
    # IPv4 first, then the lowest: the same host always gets the same answer.
    return str(min(addresses, key=lambda address: (address.version, int(address))))


def connect_remote(
    spec: ConnectionSpec, password: SecretStr, *, allow_private: bool = False
) -> psycopg.Connection:
    """Open a connection to a user's database, checked against SSRF on every call.

    The connection is `read_only`: each transaction starts as BEGIN READ ONLY. The caller must
    close it (use `with`).

    Raises:
        UnsafeConnectionError: the address, port or TLS mode isn't allowed.
        RemoteUnreachableError / RemoteLoginError / RemoteTlsError: it couldn't connect.
    """
    if spec.sslmode in DEV_ONLY_SSL_MODES and not allow_private:
        raise UnsafeConnectionError(
            "The connection must be encrypted: choose verify-full, or require if your host uses "
            "a self-signed certificate."
        )
    address = check_address(spec.host, spec.port, allow_private=allow_private)
    params: dict[str, object] = {
        "host": spec.host,  # still used for the TLS certificate check (verify-full)
        "hostaddr": address,  # where libpq really connects: the address checked above
        "port": spec.port,
        "dbname": spec.dbname,
        "user": spec.user,
        "password": password.get_secret_value(),
        "sslmode": spec.sslmode,
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "application_name": "querymind",
    }
    if spec.sslmode == "verify-full":
        params["sslrootcert"] = "system"  # the operating system's trusted CAs (libpq 16+)
    try:
        conn = psycopg.connect(make_conninfo(**params))  # type: ignore[arg-type]
    except psycopg.OperationalError as exc:
        raise _connect_error(exc, spec) from None
    conn.read_only = True  # BEGIN READ ONLY for every transaction, pooler or not
    return conn


def _connect_error(exc: psycopg.OperationalError, spec: ConnectionSpec) -> Exception:
    """Our error for a failed connect. The server's own text goes to the log, not the user."""
    text = str(exc).lower()
    logger.warning(
        "remote connection failed",
        extra={"host": spec.host, "port": spec.port, "sqlstate": exc.sqlstate, "detail": str(exc)},
    )
    if exc.sqlstate in {"28P01", "28000"} or "password authentication failed" in text:
        return RemoteLoginError("The database refused this user name or password.")
    if exc.sqlstate == "3D000" or ("database" in text and "does not exist" in text):
        return RemoteLoginError("The server has no database with this name.")
    if "does not support ssl" in text:
        return RemoteTlsError("This server doesn't accept encrypted connections.")
    if "certificate" in text:
        return RemoteTlsError(
            "The server's certificate couldn't be verified. If your host uses a self-signed "
            "certificate, choose 'require' instead of 'verify-full'."
        )
    return RemoteUnreachableError(UNREACHABLE)


# --- The read-only check ------------------------------------------------------------------------

_ROLE_POWERS = """
    SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication
    FROM pg_roles WHERE rolname = current_user
"""

# Roles this login is a member of (directly or not) that carry a power. A member can SET ROLE to
# them even when it doesn't inherit their privileges, so membership alone is refused. to_regrole:
# NULL where a built-in role doesn't exist yet (pg_write_all_data is PostgreSQL 14+).
_POWERFUL_MEMBERSHIPS = """
    SELECT r.rolname FROM pg_roles AS r
    WHERE r.rolname <> current_user
      AND pg_has_role(current_user, r.oid, 'MEMBER')
      AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication
           OR r.oid IN (to_regrole('pg_write_all_data'),
                        to_regrole('pg_write_server_files'),
                        to_regrole('pg_read_server_files'),
                        to_regrole('pg_execute_server_program')))
    ORDER BY 1
"""

# Tables, views and foreign tables it could change. has_any_column_privilege: true for a
# table-level grant *and* for a grant on one column (GRANT UPDATE (price) ON products), which
# has_table_privilege alone misses.
_WRITABLE_TABLES = """
    SELECT format('%%I.%%I', n.nspname, c.relname), count(*) OVER ()
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND n.nspname NOT LIKE 'pg\\_%%' AND n.nspname <> 'information_schema'
      AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
               has_any_column_privilege(c.oid, 'INSERT')
               OR has_any_column_privilege(c.oid, 'UPDATE')
               OR has_table_privilege(c.oid, 'DELETE') OR has_table_privilege(c.oid, 'TRUNCATE')
               OR has_table_privilege(c.oid, 'TRIGGER')
           ELSE false END
    ORDER BY 1
    LIMIT %(limit)s
"""

_WRITABLE_SEQUENCES = """
    SELECT format('%%I.%%I', n.nspname, c.relname), count(*) OVER ()
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relkind = 'S' AND n.nspname NOT LIKE 'pg\\_%%' AND n.nspname <> 'information_schema'
      -- CASE: Postgres may test conditions in any order; this one must only see sequences.
      AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege(c.oid, 'UPDATE') ELSE false END
    ORDER BY 1
    LIMIT %(limit)s
"""

# Schemas it may create objects in. On PostgreSQL 14 and older, everyone may create in `public`
# by default (granted to PUBLIC): the most common reason a "reader" login is refused here.
_CREATABLE_SCHEMAS = """
    SELECT nspname FROM pg_namespace
    WHERE nspname NOT LIKE 'pg\\_%%' AND nspname <> 'information_schema'
      AND has_schema_privilege(oid, 'CREATE')
    ORDER BY 1
"""


def _listed(names: list[str], total: int) -> str:
    shown = ", ".join(names[:SHOW_AT_MOST])
    more = total - min(len(names), SHOW_AT_MOST)
    return f"{shown} (and {more} more)" if more > 0 else shown


def read_only_problems(conn: psycopg.Connection) -> list[str]:
    """Everything that lets this login change the database, in plain words (empty = read-only).

    Each problem says how to fix it. Runs in one READ ONLY transaction with a timeout.
    """
    problems: list[str] = []
    with conn.transaction():
        conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(CHECK_TIMEOUT)))
        user = conn.execute("SELECT current_user").fetchone()
        quoted_user = sql.Identifier(str(user[0]) if user else "").as_string(conn)

        powers = conn.execute(_ROLE_POWERS).fetchone()
        if powers is not None:
            for has, name in zip(
                powers,
                ("SUPERUSER", "CREATEROLE", "CREATEDB", "BYPASSRLS", "REPLICATION"),
                strict=True,
            ):
                if has:
                    problems.append(f"The login has {name}. Use a login without it.")

        members = [row[0] for row in conn.execute(_POWERFUL_MEMBERSHIPS).fetchall()]
        if members:
            problems.append(
                f"The login is a member of powerful roles ({_listed(members, len(members))}). "
                "Use a login that isn't."
            )

        for query, what in ((_WRITABLE_TABLES, "tables"), (_WRITABLE_SEQUENCES, "sequences")):
            rows = conn.execute(query, {"limit": SHOW_AT_MOST + 1}).fetchall()
            if rows:
                names = [row[0] for row in rows]
                problems.append(
                    f"The login can change these {what}: {_listed(names, int(rows[0][1]))}. "
                    "Grant it SELECT only."
                )

        can_create_db = conn.execute(
            "SELECT has_database_privilege(current_database(), 'CREATE')"
        ).fetchone()
        if can_create_db and can_create_db[0]:
            database = conn.execute("SELECT current_database()").fetchone()
            name = sql.Identifier(str(database[0]) if database else "").as_string(conn)
            problems.append(
                "The login can create schemas in this database. Run: "
                f"REVOKE CREATE ON DATABASE {name} FROM PUBLIC, {quoted_user};"
            )

        for (schema,) in conn.execute(_CREATABLE_SCHEMAS).fetchall():
            name = sql.Identifier(str(schema)).as_string(conn)
            problems.append(
                f"The login can create tables in schema {schema}. Run: "
                f"REVOKE CREATE ON SCHEMA {name} FROM PUBLIC, {quoted_user};"
            )
    return problems


def check_read_only(conn: psycopg.Connection) -> None:
    """Refuse a login that can change anything.

    Raises:
        WritableLoginError: with every problem found, each with its fix.
    """
    problems = read_only_problems(conn)
    if problems:
        raise WritableLoginError(
            "This login can change your database, so QueryMind won't use it. " + " ".join(problems),
            problems=problems,
        )
