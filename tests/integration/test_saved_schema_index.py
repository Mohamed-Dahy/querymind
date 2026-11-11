"""A saved database's schema index on the real database (5.10b).

Its rows live in our app database, one set per saved database: syncing or searching one never
touches another's, and deleting the database deletes them. Done when: a database above
RETRIEVAL_MIN_TABLES sends only the tables a question needs, shown here on a 40-table schema
(searched by Postgres itself) and end to end on the 25-table Docker database saved as a user's own.
"""

from uuid import UUID

import psycopg
import pytest

from querymind.agent.registry import PipelineRegistry, database_settings
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.schema import estimate_tokens
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.dictionary import DataDictionary, DictionaryTable
from querymind.semantic.documents import SchemaDoc, build_documents
from querymind.semantic.index import keyword_search, sync_connection_documents
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever
from querymind.store.connections import ConnectionStore
from querymind.store.conversations import Owner
from querymind.store.onboarding import OnboardingStore
from tests.integration.test_onboarding_db import connections as connections  # fixtures
from tests.integration.test_onboarding_db import onboarding as onboarding
from tests.integration.test_onboarding_db import saved_id
from tests.integration.test_remote_databases import MakeRole
from tests.integration.test_remote_databases import admin as admin
from tests.integration.test_remote_databases import make_role as make_role
from tests.unit.test_saved_index import BIG_SCHEMA

pytestmark = pytest.mark.integration


def doc(name: str, text: str) -> SchemaDoc:
    return SchemaDoc(id=f"table:{name}", kind="table", name=name, table=name, text=text)


def rows_of(admin: psycopg.Connection, connection_id: UUID) -> int:
    row = admin.execute(
        "SELECT count(*) FROM querymind_app.connection_schema_docs WHERE connection_id = %s",
        (connection_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_syncing_or_searching_one_database_never_touches_another(
    settings: Settings,
    connections: ConnectionStore,
    owner: Owner,
    other_owner: Owner,
    admin: psycopg.Connection,
) -> None:
    mine, theirs = saved_id(connections, owner), saved_id(connections, other_owner)
    # The same table names in both: only connection_id tells their rows apart.
    my_docs = [doc("invoices", "Table invoices: bills"), doc("refunds", "Table refunds")]
    their_docs = [doc("invoices", "Table invoices: shipments of parcels")]

    with connect_app(settings) as conn:
        assert sync_connection_documents(conn, mine, my_docs) == (2, 0)
        assert sync_connection_documents(conn, theirs, their_docs) == (1, 0)
        assert sync_connection_documents(conn, mine, my_docs) == (0, 0)  # unchanged: no writes
        # Mine shrinks: only my "gone" rows are deleted.
        assert sync_connection_documents(conn, mine, my_docs[:1]) == (0, 1)
        parcels = keyword_search(conn, "parcels", limit=5, connection_id=mine)
        their_parcels = keyword_search(conn, "parcels", limit=5, connection_id=theirs)

    assert (rows_of(admin, mine), rows_of(admin, theirs)) == (1, 1)
    assert parcels == []  # their description is never found from my database
    assert [hit.table for hit in their_parcels] == ["invoices"]


def test_deleting_the_database_deletes_its_index(
    settings: Settings, connections: ConnectionStore, owner: Owner, admin: psycopg.Connection
) -> None:
    connection = saved_id(connections, owner)
    with connect_app(settings) as conn:
        sync_connection_documents(conn, connection, [doc("invoices", "Table invoices")])

    connections.delete(owner.workspace_id, connection)

    assert rows_of(admin, connection) == 0


@pytest.mark.parametrize(
    ("question", "needed"),
    [
        ("Which carrier delivered the most shipments?", {"shipments", "carriers"}),
        ("Total salaries per department", {"salaries", "departments"}),
        ("How many support tickets are still open?", {"support_tickets"}),
        ("Show the invoice lines of last month's invoices", {"invoice_lines", "invoices"}),
    ],
)
def test_done_when_a_40_table_database_sends_only_the_tables_a_question_needs(
    settings: Settings, connections: ConnectionStore, owner: Owner, question: str, needed: set[str]
) -> None:
    connection = saved_id(connections, owner)
    own = database_settings(settings)
    catalog = SchemaCatalog(BIG_SCHEMA, [])
    with connect_app(settings) as conn:
        sync_connection_documents(conn, connection, build_documents(BIG_SCHEMA, [], {}))
    retriever = SchemaRetriever(
        catalog=catalog,
        embeddings=None,
        connect=connect_app,
        settings=own,
        connection_id=connection,
    )

    selection = retriever.select(question)

    assert selection.info.mode == "retrieved", selection.info.reason
    assert needed <= set(selection.info.tables)
    assert len(selection.info.tables) <= own.retrieval_max_tables  # of 40
    assert selection.info.schema_tokens < estimate_tokens(catalog.full_text) / 3


def test_done_when_the_docker_database_saved_as_a_users_own_sends_only_what_is_needed(
    settings: Settings,
    connections: ConnectionStore,
    onboarding: OnboardingStore,
    owner: Owner,
    make_role: MakeRole,
    admin: psycopg.Connection,
) -> None:
    spec = make_role("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")  # all 25 tables
    connection = saved_id(connections, owner, spec)
    onboarding.queue(connection)
    described = DataDictionary(
        database="Sales",
        tables={
            "customers": DictionaryTable(description="DRAFTED: companies that buy our food."),
            "customer": DictionaryTable(description="DRAFTED: people who buy music tracks."),
        },
    )
    onboarding.finish(connection, lambda stored: described, generated_by="fake", skipped=0)
    registry = PipelineRegistry(
        demo=None,  # type: ignore[arg-type]
        connections=connections,
        settings=settings,
        allow_private=True,
        dictionaries=onboarding,
    )
    asker = FakeLLMProvider(
        [SqlDraft(sql="SELECT count(*) AS n FROM customers", explanation="Count.", tables_used=[])]
    )

    pipeline = registry.for_database(owner.workspace_id, connection)
    reply = pipeline.ask("How many food customers are there?", llm=asker, summarize_answer=False)

    assert isinstance(reply, AskResult) and reply.result.rows == [[91]]
    shown = reply.schema_selection
    assert shown is not None and (shown.mode, shown.search) == ("retrieved", "keyword")
    assert shown.total_tables == 25 and "customers" in shown.tables
    assert len(shown.tables) <= settings.retrieval_max_tables
    prompt = asker.calls[0].prompt
    assert "DRAFTED: companies that buy our food." in prompt
    assert "playlist_track" not in prompt  # a music table the question doesn't need
    assert rows_of(admin, connection) == 25
