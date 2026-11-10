"""Users' own databases on a real PostgreSQL (5.9): the read-only check against real roles, the
read-only session, and the connection store.

Each test builds its own throwaway roles on the Docker database (as the admin) and drops them
afterwards. Docker is on 127.0.0.1 without TLS, so these run in development mode
(allow_private=True, sslmode=prefer): the SSRF guard itself is tested in unit tests.
"""

from collections.abc import Callable, Iterator
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg import errors, sql
from pydantic import SecretStr

from querymind.config import Settings
from querymind.db.errors import WritableLoginError
from querymind.db.remote import ConnectionSpec, check_read_only, connect_remote, read_only_problems
from querymind.secrets.cipher import SecretCipher
from querymind.secrets.errors import SecretNotFoundError
from querymind.secrets.store import LocalSecretStore
from querymind.store.connections import ConnectionStore
from querymind.store.conversations import Owner
from querymind.store.errors import ConnectionNameTakenError, ConnectionNotFoundError

pytestmark = pytest.mark.integration

PASSWORD = SecretStr("a-throwaway-test-password-123")
type MakeRole = Callable[..., ConnectionSpec]


@pytest.fixture
def admin(settings: Settings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.admin_conninfo(), autocommit=True) as conn:
        yield conn


@pytest.fixture
def make_role(settings: Settings, admin: psycopg.Connection) -> Iterator[MakeRole]:
    """Make a login that can read `customers`, plus whatever grants the test adds. Dropped after."""
    made: list[str] = []
    schemas: list[str] = []

    def make(*grants: str, attributes: str = "", schema: bool = False) -> ConnectionSpec:
        role = f"qm_test_{uuid4().hex[:10]}"
        made.append(role)
        ident = sql.Identifier(role)
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} " + attributes).format(
                ident, sql.Literal(PASSWORD.get_secret_value())
            )
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(settings.postgres_db), ident
            )
        )
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        admin.execute(sql.SQL("GRANT SELECT ON public.customers TO {}").format(ident))
        for grant in grants:
            admin.execute(sql.SQL(grant).format(role=ident))
        if schema:  # PostgreSQL 14's default, copied on a schema of its own (not on public)
            name = f"{role}_schema"
            schemas.append(name)
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
            admin.execute(
                sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(sql.Identifier(name), ident)
            )
        return ConnectionSpec(
            host="127.0.0.1",
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=role,
            sslmode="prefer",
        )

    yield make
    for name in schemas:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
    for role in made:
        admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def problems_of(spec: ConnectionSpec) -> list[str]:
    with connect_remote(spec, PASSWORD, allow_private=True) as conn:
        return read_only_problems(conn)


def test_a_login_that_can_only_read_passes(make_role: MakeRole) -> None:
    spec = make_role()

    assert problems_of(spec) == []
    with connect_remote(spec, PASSWORD, allow_private=True) as conn:
        check_read_only(conn)  # no error


@pytest.mark.parametrize(
    ("grant", "says"),
    [
        ("GRANT INSERT ON public.orders TO {role}", "public.orders"),
        ("GRANT DELETE ON public.orders TO {role}", "public.orders"),
        ("GRANT TRUNCATE ON public.products TO {role}", "public.products"),
        # One column is enough to change data: has_table_privilege alone would miss it.
        ("GRANT UPDATE (unit_price) ON public.products TO {role}", "public.products"),
        ("GRANT TRIGGER ON public.orders TO {role}", "public.orders"),
    ],
)
def test_any_write_grant_is_refused(make_role: MakeRole, grant: str, says: str) -> None:
    spec = make_role(grant)

    [problem] = problems_of(spec)

    assert "can change these tables" in problem and says in problem


@pytest.mark.parametrize(
    ("attributes", "says"),
    [("CREATEDB", "CREATEDB"), ("CREATEROLE", "CREATEROLE"), ("BYPASSRLS", "BYPASSRLS")],
)
def test_role_powers_are_refused(make_role: MakeRole, attributes: str, says: str) -> None:
    spec = make_role(attributes=attributes)

    assert any(says in problem for problem in problems_of(spec))


def test_membership_in_a_writer_role_is_refused(make_role: MakeRole) -> None:
    """pg_write_all_data writes to every table, whatever the table grants say."""
    spec = make_role("GRANT pg_write_all_data TO {role}")

    problems = problems_of(spec)

    assert any("pg_write_all_data" in problem for problem in problems)


def test_create_on_a_schema_is_refused_with_the_revoke_to_run(make_role: MakeRole) -> None:
    spec = make_role(schema=True)

    [problem] = problems_of(spec)

    assert f"can create tables in schema {spec.user}_schema" in problem
    assert f'REVOKE CREATE ON SCHEMA "{spec.user}_schema" FROM PUBLIC, "{spec.user}";' in problem


def test_the_refusal_lists_every_problem(make_role: MakeRole) -> None:
    spec = make_role("GRANT INSERT ON public.orders TO {role}", attributes="CREATEDB")

    with (
        connect_remote(spec, PASSWORD, allow_private=True) as conn,
        pytest.raises(WritableLoginError) as refused,
    ):
        check_read_only(conn)

    assert len(refused.value.problems) == 2
    assert PASSWORD.get_secret_value() not in str(refused.value)


def test_the_session_itself_refuses_writes_even_for_a_writer(make_role: MakeRole) -> None:
    """The third layer: if a writable login ever got through, its connection still can't write."""
    spec = make_role("GRANT INSERT, DELETE ON public.region TO {role}")

    with (
        connect_remote(spec, PASSWORD, allow_private=True) as conn,
        pytest.raises(errors.ReadOnlySqlTransaction),
    ):
        conn.execute("DELETE FROM public.region WHERE false")


# --- The connection store ------------------------------------------------------------------------


@pytest.fixture
def secrets(settings: Settings) -> LocalSecretStore:
    key = SecretStr(Fernet.generate_key().decode())
    return LocalSecretStore(settings, cipher=SecretCipher([key]))


@pytest.fixture
def store(settings: Settings, secrets: LocalSecretStore) -> ConnectionStore:
    return ConnectionStore(settings, secrets)


SPEC = ConnectionSpec(host="db.example.com", port=5432, dbname="sales", user="reader")


def test_a_saved_database_reads_back_without_its_password(
    store: ConnectionStore, owner: Owner
) -> None:
    saved = store.save(owner.workspace_id, "Sales", SPEC, PASSWORD)

    [listed] = store.list(owner.workspace_id)
    assert listed == saved == store.get(owner.workspace_id, saved.id)
    assert listed.spec() == SPEC
    assert PASSWORD.get_secret_value() not in listed.model_dump_json()
    assert store.password(owner.workspace_id, listed).get_secret_value() == (
        PASSWORD.get_secret_value()
    )


def test_names_are_unique_per_workspace_and_leave_no_orphan(
    store: ConnectionStore, secrets: LocalSecretStore, owner: Owner, other_owner: Owner
) -> None:
    store.save(owner.workspace_id, "Sales", SPEC, PASSWORD)

    with pytest.raises(ConnectionNameTakenError):
        store.save(owner.workspace_id, "Sales", SPEC, PASSWORD)
    store.save(other_owner.workspace_id, "Sales", SPEC, PASSWORD)  # another workspace: fine

    assert len(store.list(owner.workspace_id)) == 1


def test_another_workspace_cant_see_test_or_delete_it(
    store: ConnectionStore, owner: Owner, other_owner: Owner
) -> None:
    saved = store.save(owner.workspace_id, "Sales", SPEC, PASSWORD)

    assert store.list(other_owner.workspace_id) == []
    with pytest.raises(ConnectionNotFoundError):
        store.get(other_owner.workspace_id, saved.id)
    with pytest.raises(ConnectionNotFoundError):
        store.mark_checked(other_owner.workspace_id, saved.id)
    with pytest.raises(ConnectionNotFoundError):
        store.delete(other_owner.workspace_id, saved.id)


def test_deleting_removes_the_password_too(
    store: ConnectionStore, secrets: LocalSecretStore, owner: Owner
) -> None:
    saved = store.save(owner.workspace_id, "Sales", SPEC, PASSWORD)

    store.delete(owner.workspace_id, saved.id)

    assert store.list(owner.workspace_id) == []
    with pytest.raises(SecretNotFoundError):
        secrets.reveal(owner.workspace_id, saved.secret_id)
