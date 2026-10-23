"""Load the BIRD Mini-Dev benchmark: 11 databases, each in its own Postgres database, + questions.

Run (after data/seed_northwind.py, which creates the read-only role):
    uv run python data/seed_bird.py                          # all 11 (first run downloads ~800 MB)
    uv run python data/seed_bird.py --only formula_1 toxicology

What it does:
  1. Downloads the BIRD package (a zip, ~800 MB) and the questions file (~280 KB) into
     data/raw/bird/, once, and checks both against pinned SHA-256 hashes.
  2. Reads the PostgreSQL dump inside the zip (1 GB of SQL, never unpacked to disk) and sends
     each table to its own database: formula_1's 13 tables go to `bird_formula_1`, and so on.
     BIRD's protocol shows a model only the database a question is about, and with one database
     each our introspection and validator work unchanged (one `public` schema, no name clashes).
  3. Lets the read-only role connect to each database and SELECT its tables (nothing else).

The dump holds all 75 tables in one schema; dev_tables.json (also in the zip) says which BIRD
database each table belongs to. Only these statements are run: CREATE TABLE, COPY (the data),
primary/foreign keys and indexes. Sequences, owners and session settings are skipped (they only
matter for inserting rows, and the agent never inserts). Anything else stops the script: we run
this SQL as superuser, so an unexpected statement is never run.

Safe to re-run: each database is dropped and recreated. Takes a few minutes and ~2 GB of disk.
The data is CC BY-SA 4.0 (https://bird-bench.github.io), downloaded here, never committed.
"""

import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import IO

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from seed_northwind import RAW_DIR, SeedError, fetch_pinned

from querymind.config import ConfigError, Settings, get_settings

logger = logging.getLogger("seed_bird")

BIRD_DIR = RAW_DIR / "bird"

# The "Complete Package" linked from https://huggingface.co/datasets/birdsql/bird_mini_dev.
ZIP_PAGE = "https://drive.google.com/file/d/13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG/view"
ZIP_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG&export=download&confirm=t"
)
ZIP_FILE = "minidev_0703.zip"
ZIP_SHA256 = "aeb211c0e39010bbdae3838bb5e8bd27dc446ed77495b1709f85ccc9bf67f2be"
DUMP_MEMBER = "minidev/MINIDEV_postgresql/BIRD_dev.sql"
TABLES_MEMBER = "minidev/MINIDEV/dev_tables.json"

# The questions come from Hugging Face, pinned to a commit: it's newer than the zip's copy and
# fixes one gold query (879 sorted lap speeds as text). The zip has the databases.
QUESTIONS_URL = (
    "https://huggingface.co/datasets/birdsql/bird_mini_dev/resolve/"
    "f65faf4ae3b638c1fa6df1d3370c8d92c8366301/data/mini_dev_pg-00000-of-00001.json"
)
QUESTIONS_FILE = "mini_dev_postgresql.json"
QUESTIONS_SHA256 = "7fa740ef9225389cff6c34432120e8325d0ca3008d73db1ae38731234bc10da7"

# A table name in the dump: public.name or public."quoted name" ("" inside = one quote).
_NAME = r'public\.("(?:[^"]|"")+"|[a-z_][a-z0-9_]*)'
# The statements we run, each with the table it belongs to.
_RUN = (
    re.compile(rf"^CREATE TABLE {_NAME} \("),
    re.compile(rf"^ALTER TABLE ONLY {_NAME}\s+ADD CONSTRAINT "),
    re.compile(rf"^CREATE (?:UNIQUE )?INDEX \S+ ON {_NAME} "),
)
_COPY = re.compile(rf"^COPY {_NAME} \(.*\) FROM stdin;$")
_REFERENCES = re.compile(rf"REFERENCES {_NAME}")
# The statements we skip on purpose (see the module docstring).
_SKIP = (
    re.compile(r"^SET \w+ = "),
    re.compile(r"^SELECT pg_catalog\.set_config\('search_path', '', false\);$"),
    re.compile(rf"^ALTER TABLE {_NAME} OWNER TO \w+;$"),
    re.compile(rf"^(CREATE|ALTER) SEQUENCE {_NAME}"),
    re.compile(rf"^ALTER TABLE ONLY {_NAME} ALTER COLUMN \w+ SET DEFAULT nextval\("),
    re.compile(r"^SELECT pg_catalog\.setval\("),
)
COPY_END = b"\\.\n"  # the line that ends a COPY block's data
COPY_BATCH = 1 << 20  # bytes of COPY data sent to Postgres at a time (1 MB)


def database_name(db_id: str) -> str:
    """Same rule as evals/bird.py: formula_1 → bird_formula_1."""
    return f"bird_{db_id}"


def fetch_zip() -> Path:
    """The BIRD zip in data/raw/bird/, downloaded once, hash checked every time (streamed:
    the file is too big to hold in memory)."""
    path = BIRD_DIR / ZIP_FILE
    if not path.exists():
        BIRD_DIR.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        logger.info("Downloading the BIRD package (~800 MB, once) from Google Drive")
        try:
            with urllib.request.urlopen(ZIP_URL, timeout=60) as response, part.open("wb") as out:
                shutil.copyfileobj(response, out, length=1 << 20)
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise SeedError(
                f"Download failed ({exc}). Download it by hand from {ZIP_PAGE} "
                f"and save it as {path}"
            ) from exc
        part.rename(path)

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != ZIP_SHA256:
        path.unlink()  # a partial or changed download is fetched again next run
        raise SeedError(
            f"{ZIP_FILE} hash mismatch (got {digest.hexdigest()}). "
            "The file changed or the download was corrupted. Not running it."
        )
    return path


def table_owners(archive: zipfile.ZipFile) -> dict[str, str]:
    """Table name (as Postgres stores it: lowercase) → the BIRD database it belongs to."""
    owners: dict[str, str] = {}
    for database in json.loads(archive.read(TABLES_MEMBER)):
        for table in database["table_names_original"]:
            name = table.lower()  # the dump created them unquoted, so Postgres folded them
            if name in owners:
                raise SeedError(f"Table {name} is in both {owners[name]} and {database['db_id']}")
            owners[name] = database["db_id"]
    return owners


def unquote(name: str) -> str:
    if name.startswith('"'):
        return name[1:-1].replace('""', '"')
    return name


def statements(dump: IO[bytes]) -> Iterator[tuple[str, Iterator[bytes] | None]]:
    """Split a pg_dump file into statements. Yields (statement, None), or for a COPY,
    (its first line, an iterator over its data lines), which must be read before going on.

    pg_dump's plain format is regular: comments are lines starting with "--", a statement ends
    with ";" at the end of a line, and COPY data ends with a line holding only "\\.".
    That's enough here: this dump has no functions (whose bodies could contain ";").
    """
    lines: list[str] = []

    def copy_data() -> Iterator[bytes]:
        for line in dump:
            if line == COPY_END:
                return
            yield line
        raise SeedError("The dump ended inside a COPY block.")

    for raw in dump:
        line = raw.decode("utf-8")
        if not lines and (line.startswith("--") or not line.strip()):
            continue
        lines.append(line)
        if line.startswith("COPY ") and not lines[1:]:
            yield line.rstrip("\n"), copy_data()
            lines = []
        elif line.rstrip().endswith(";"):
            yield "".join(lines).strip(), None
            lines = []
    if lines:
        raise SeedError("The dump ended in the middle of a statement.")


def route(statement: str, owners: dict[str, str]) -> str | None:
    """The BIRD database a statement belongs to, or None for one we skip on purpose.

    Raises:
        SeedError: a statement we don't expect, or a foreign key across two databases.
    """
    for pattern in _RUN:
        if match := pattern.match(statement):
            owner = _owner(unquote(match.group(1)), owners)
            for ref in _REFERENCES.findall(statement):
                if _owner(unquote(ref), owners) != owner:
                    raise SeedError(f"Foreign key across databases: {statement[:120]}")
            return owner
    if any(pattern.match(statement) for pattern in _SKIP):
        return None
    raise SeedError(f"Unexpected statement in the BIRD dump, not running it: {statement[:120]}")


def _owner(table: str, owners: dict[str, str]) -> str:
    if table not in owners:
        raise SeedError(f"Table {table} isn't in any BIRD database (dev_tables.json)")
    return owners[table]


def recreate_databases(settings: Settings, db_ids: list[str]) -> None:
    """Drop and create one empty database per BIRD database.

    CREATE/DROP DATABASE can't run inside a transaction, hence autocommit. WITH (FORCE) closes
    any open connection to the old database first (e.g. an eval still running).
    """
    with psycopg.connect(settings.admin_conninfo(), autocommit=True) as conn:
        for db_id in db_ids:
            name = sql.Identifier(database_name(db_id))
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(name))


def load(
    dump: IO[bytes], owners: dict[str, str], conns: dict[str, psycopg.Connection]
) -> dict[str, int]:
    """Run each statement in its database's connection (only those in `conns`).

    Returns the rows copied into each table.
    """
    rows: dict[str, int] = {}
    for statement, data in statements(dump):
        if data is None:
            owner = route(statement, owners)
            if owner in conns:
                conns[owner].execute(statement)  # one of the _RUN statements
            continue
        match = _COPY.match(statement)
        if not match:
            raise SeedError(f"Unexpected COPY statement: {statement[:120]}")
        table = unquote(match.group(1))
        owner = _owner(table, owners)
        if owner not in conns:
            for _ in data:  # not loading this database: skip its rows
                pass
            continue
        count = 0
        buffer = bytearray()
        with conns[owner].cursor().copy(statement) as copy:
            for line in data:
                buffer += line
                count += 1
                if len(buffer) >= COPY_BATCH:  # send ~1 MB at a time, not line by line
                    copy.write(buffer)
                    buffer.clear()
            copy.write(buffer)
        rows[table] = count
    return rows


def grant_read(conn: psycopg.Connection, *, database: str, user: str) -> None:
    """The read-only role may connect to this database and SELECT its tables, nothing more.

    Its other limits (read-only sessions, statement timeout) belong to the role itself, set by
    seed_northwind.py, so they apply in every database.
    """
    role = sql.Identifier(user)
    for statement in (
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), role),
        sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role),
    ):
        conn.execute(statement)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the BIRD Mini-Dev databases.")
    parser.add_argument("--only", nargs="+", metavar="DB_ID", help="load only these databases")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    started = time.perf_counter()
    try:
        settings = get_settings()
        BIRD_DIR.mkdir(parents=True, exist_ok=True)
        fetch_pinned(QUESTIONS_URL, f"bird/{QUESTIONS_FILE}", QUESTIONS_SHA256)
        user = settings.analyst_ro_user
        with zipfile.ZipFile(fetch_zip()) as archive:
            owners = table_owners(archive)
            db_ids = sorted(set(owners.values()))
            unknown = sorted(set(args.only or ()) - set(db_ids))
            if unknown:
                raise SeedError(f"No BIRD database {unknown}; there are: {', '.join(db_ids)}")
            db_ids = sorted(args.only) if args.only else db_ids
            with psycopg.connect(settings.admin_conninfo()) as conn:
                role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
                if not role.fetchone():
                    raise SeedError(f"Role {user!r} doesn't exist: run seed_northwind.py first.")
            recreate_databases(settings, db_ids)
            logger.info("Loading %d databases from the 1 GB dump...", len(db_ids))
            # One connection (= one transaction) per database: each commits only if all of it
            # loaded. ExitStack closes them all, however we leave this block.
            with ExitStack() as stack, archive.open(DUMP_MEMBER) as dump:
                conns = {
                    db_id: stack.enter_context(
                        psycopg.connect(_admin_conninfo(settings, database_name(db_id)))
                    )
                    for db_id in db_ids
                }
                rows = load(dump, owners, conns)
                for db_id, conn in conns.items():
                    grant_read(conn, database=database_name(db_id), user=user)
                    conn.commit()
                    conn.autocommit = True  # ANALYZE: fresh statistics, so queries plan well
                    conn.execute("ANALYZE")
    except (ConfigError, SeedError, psycopg.Error, OSError, KeyError, ValueError) as exc:
        logger.error("Seeding BIRD failed: %s", exc)
        return 1

    for db_id in db_ids:
        tables = sorted(t for t, owner in owners.items() if owner == db_id)
        total = sum(rows.get(t, 0) for t in tables)
        logger.info("%s: %d tables, %d rows", database_name(db_id), len(tables), total)
    logger.info(
        "Loaded %d BIRD databases in %.0f s; %r can read them.",
        len(db_ids),
        time.perf_counter() - started,
        user,
    )
    return 0


def _admin_conninfo(settings: Settings, database: str) -> str:
    """Admin connection to one BIRD database. The dump is UTF-8 and COPY sends its bytes as
    they are, so the session says so (whatever the machine's own encoding is)."""
    conninfo = settings.model_copy(update={"postgres_db": database}).admin_conninfo()
    return make_conninfo(conninfo, client_encoding="UTF8")


if __name__ == "__main__":
    sys.exit(main())
