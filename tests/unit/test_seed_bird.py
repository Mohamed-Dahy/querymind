"""data/seed_bird.py's dump reader: splitting pg_dump SQL and routing it to the right database.

No download and no database: a small fake dump in the same format as BIRD's.
"""

import io
import json
import zipfile

import pytest
from seed_bird import TABLES_MEMBER, route, statements, table_owners, unquote
from seed_northwind import SeedError

OWNERS = {"account": "financial", "order": "financial", "drivers": "formula_1"}

DUMP = b"""--
-- PostgreSQL database dump
--

SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);

CREATE TABLE public.account (
    account_id bigint NOT NULL,
    frequency text
);


ALTER TABLE public.account OWNER TO xiaolongli;

COPY public.account (account_id, frequency) FROM stdin;
1\tmonthly; really
2\t\\N
\\.


ALTER TABLE ONLY public.account
    ADD CONSTRAINT account_pkey PRIMARY KEY (account_id);
"""


def test_splits_a_dump_into_statements_and_copy_data() -> None:
    parsed = []
    for statement, data in statements(io.BytesIO(DUMP)):
        rows = list(data) if data is not None else None  # COPY data must be read right away
        parsed.append((statement.split("\n")[0], rows))
    assert parsed == [
        ("SET statement_timeout = 0;", None),
        ("SELECT pg_catalog.set_config('search_path', '', false);", None),
        ("CREATE TABLE public.account (", None),
        ("ALTER TABLE public.account OWNER TO xiaolongli;", None),
        # A ";" inside the data doesn't end anything: data lines end only at "\.".
        (
            "COPY public.account (account_id, frequency) FROM stdin;",
            [b"1\tmonthly; really\n", b"2\t\\N\n"],
        ),
        ("ALTER TABLE ONLY public.account", None),
    ]


def test_a_cut_off_dump_is_an_error() -> None:
    with pytest.raises(SeedError, match="middle of a statement"):
        list(statements(io.BytesIO(b"CREATE TABLE public.account (\n    id bigint\n")))


@pytest.mark.parametrize(
    ("statement", "database"),
    [
        ("CREATE TABLE public.account (\n    id bigint\n);", "financial"),
        ('CREATE TABLE public."order" (\n    order_id bigint\n);', "financial"),
        (
            "ALTER TABLE ONLY public.drivers\n    ADD CONSTRAINT d_pkey PRIMARY KEY (driverid);",
            "formula_1",
        ),
        ("CREATE UNIQUE INDEX i ON public.drivers USING btree (url);", "formula_1"),
        (
            'ALTER TABLE ONLY public."order"\n    ADD CONSTRAINT fk FOREIGN KEY (account_id) '
            "REFERENCES public.account(account_id);",
            "financial",
        ),
        # Skipped on purpose: only needed to insert rows, or session settings.
        ("SET client_encoding = 'UTF8';", None),
        ("ALTER TABLE public.account OWNER TO xiaolongli;", None),
        ("CREATE SEQUENCE public.drivers_driverid_seq\n    START WITH 1;", None),
        ("SELECT pg_catalog.setval('public.drivers_driverid_seq', 841, true);", None),
    ],
)
def test_routes_each_statement_to_its_database(statement: str, database: str | None) -> None:
    assert route(statement, OWNERS) == database


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        # We run this SQL as superuser: anything we didn't expect is never run.
        ("DROP DATABASE querymind;", "Unexpected statement"),
        ("CREATE FUNCTION public.f() RETURNS int AS 'select 1' LANGUAGE sql;", "Unexpected"),
        ("GRANT ALL ON public.account TO PUBLIC;", "Unexpected"),
        ("CREATE TABLE public.mystery (\n    id bigint\n);", "isn't in any BIRD database"),
        (
            "ALTER TABLE ONLY public.account\n    ADD CONSTRAINT fk FOREIGN KEY (id) "
            "REFERENCES public.drivers(driverid);",
            "across databases",
        ),
    ],
)
def test_unexpected_statements_stop_the_seed(statement: str, error: str) -> None:
    with pytest.raises(SeedError, match=error):
        route(statement, OWNERS)


def test_unquote() -> None:
    assert unquote("account") == "account"
    assert unquote('"order"') == "order"
    assert unquote('"say ""hi"""') == 'say "hi"'


def zip_with_tables(databases: list[dict[str, object]]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(TABLES_MEMBER, json.dumps(databases))
    return zipfile.ZipFile(buffer)


def test_table_owners_use_postgres_lowercase_names() -> None:
    archive = zip_with_tables(
        [{"db_id": "formula_1", "table_names_original": ["drivers", "lapTimes"]}]
    )
    assert table_owners(archive) == {"drivers": "formula_1", "laptimes": "formula_1"}


def test_a_table_in_two_databases_is_an_error() -> None:
    archive = zip_with_tables(
        [
            {"db_id": "a", "table_names_original": ["users"]},
            {"db_id": "b", "table_names_original": ["Users"]},
        ]
    )
    with pytest.raises(SeedError, match="both a and b"):
        table_owners(archive)
