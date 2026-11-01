"""Record real POST /ask replies as fixtures for the web UI's tests (run once, then commit them).

    uv run python scripts/record_fixtures.py                   # Gemini: keeps the Groq budget free
    uv run python scripts/record_fixtures.py --provider groq
    uv run python scripts/record_fixtures.py --only monthly single   # just these (others untouched)

Needs what a real question needs: the database (`docker compose up -d --wait`), the schema index
and the provider's API key. 6 questions, about 18K tokens (all of them).

Each question says which kind of reply it must get. If the LLM answers with another kind (no
question back for an ambiguous question, say), the script fails loudly and that fixture keeps its
old content. The replies go through the real FastAPI app in-process (routes, schemas, error
handlers), so a fixture has exactly the shape the browser gets over HTTP. The questions avoid
personal data (no contact names, phones or addresses): the fixtures are committed.

Every question goes through POST /ask/stream, and writes two files: <name>.json (the final reply)
and streams/<name>.json (the events before it), which mock mode replays (UI U2.3).

`repair` isn't left to chance: a real LLM repairs only now and then. It runs the real pipeline and
database with a scripted LLM (a real Postgres error, then good SQL), so it costs no LLM quota.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.api.main import create_app
from querymind.config import ConfigError, Settings, get_settings
from querymind.llm import create_embedding_provider, create_llm_provider
from querymind.llm.fake import FakeLLMProvider, FakeResponse

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "web" / "src" / "mocks" / "recorded"


@dataclass(frozen=True)
class Recording:
    name: str  # saved as <name>.json
    question: str
    kind: Literal["answer", "clarification", "refusal"]  # the reply it must get
    follows: str | None = None  # the recording whose conversation this one continues
    script: tuple[FakeResponse, ...] = field(default=())  # scripted LLM replies; empty = real LLM


# GROUP BY the wrong column: Postgres really rejects it, so the repair is a real one.
REPAIR_SCRIPT: tuple[FakeResponse, ...] = (
    SqlDraft(
        sql="SELECT country, COUNT(*) AS customer_count FROM customers GROUP BY region",
        explanation="Counts the customers in each country.",
        tables_used=["customers"],
    ),
    SqlDraft(
        sql=(
            "SELECT country, COUNT(*) AS customer_count FROM customers "
            "GROUP BY country ORDER BY customer_count DESC"
        ),
        explanation="Counts the customers in each country, biggest first.",
        tables_used=["customers"],
    ),
    "The USA has the most customers, with 13.",
)

RECORDINGS = (
    Recording("answer", "What is the total sales amount per category?", "answer"),
    Recording("followup", "Now only for 1997.", "answer", follows="answer"),
    # Ambiguous on purpose (evals/golden/clarification.yaml c02): by units, orders or revenue?
    Recording("clarification", "Which products are the most popular?", "clarification"),
    Recording("refusal", "Delete all orders from 1996.", "refusal"),
    # U1.5's charts: a time column (a line chart), and a single number (no chart).
    Recording("monthly", "What were the total sales per month in 1997?", "answer"),
    Recording("single", "How many customers are there?", "answer"),
    # U2.4's repair loop.
    Recording(
        "repair", "How many customers are there in each country?", "answer", script=REPAIR_SCRIPT
    ),
)
NAMES = tuple(recording.name for recording in RECORDINGS)


def select(names: list[str]) -> tuple[Recording, ...]:
    """The named recordings plus every recording they follow up on, in RECORDINGS order.

    A follow-up needs its parent's conversation, so the parent is asked too; main() writes only the
    named ones, so the parent's committed file stays as it is.
    """
    by_name = {recording.name: recording for recording in RECORDINGS}
    needed: set[str] = set()
    for name in names:
        current: str | None = name
        while current is not None:
            needed.add(current)
            current = by_name[current].follows
    return tuple(recording for recording in RECORDINGS if recording.name in needed)


@dataclass(frozen=True)
class Recorded:
    reply: dict[str, Any]  # the final event's reply: what POST /ask would send
    events: list[dict[str, Any]]  # the events before it


def read_stream(text: str) -> list[dict[str, Any]]:
    """The events of a Server-Sent Events body. Keep-alive pings are comments, starting with ":"."""
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def record(
    client: TestClient, recordings: tuple[Recording, ...] = RECORDINGS
) -> tuple[dict[str, Recorded], list[str]]:
    """Ask each question; return the replies of the right kind, and a problem for every other."""
    recorded: dict[str, Recorded] = {}
    problems: list[str] = []
    for recording in recordings:
        body: dict[str, str] = {"question": recording.question}
        if recording.follows is not None:
            earlier = recorded.get(recording.follows)
            if earlier is None:
                problems.append(f"{recording.name}: skipped, {recording.follows!r} failed")
                continue
            body["conversation_id"] = earlier.reply["conversation_id"]

        response = client.post("/ask/stream", json=body)
        if response.status_code != 200:
            problems.append(f"{recording.name}: HTTP {response.status_code} {response.text}")
            continue
        *events, last = read_stream(response.text)
        if last["type"] == "error":
            error = last["error"]
            problems.append(f"{recording.name}: {error['error']}: {error['message']}")
        elif last["reply"].get("kind") != recording.kind:
            problems.append(
                f"{recording.name}: wanted kind {recording.kind!r}, "
                f"got {last['reply'].get('kind')!r}"
            )
        else:
            recorded[recording.name] = Recorded(reply=last["reply"], events=events)
    return recorded, problems


def write_json(path: Path, content: object) -> None:
    """UTF-8 with LF line endings on every OS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(content, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def write_fixtures(recorded: dict[str, Recorded], output_dir: Path) -> list[Path]:
    """For each recording: <name>.json (the reply) and streams/<name>.json (the events)."""
    paths = []
    for name, item in recorded.items():
        for path, content in (
            (output_dir / f"{name}.json", item.reply),
            (output_dir / "streams" / f"{name}.json", item.events),
        ):
            write_json(path, content)
            paths.append(path)
    return paths


def record_with(
    settings: Settings, recordings: tuple[Recording, ...], responses: tuple[FakeResponse, ...] = ()
) -> tuple[dict[str, Recorded], list[str]]:
    """Record through the real app, with the real pipeline; a script replaces only the LLM."""
    pipeline = Pipeline(
        llm=FakeLLMProvider(responses) if responses else create_llm_provider(settings),
        settings=settings,
        embeddings=create_embedding_provider(settings),
    )
    # raise_server_exceptions=False: a bug comes back as the API's 500 body, like over HTTP.
    with TestClient(create_app(pipeline=pipeline), raise_server_exceptions=False) as client:
        return record(client, recordings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["groq", "gemini"], default="gemini")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write them")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=NAMES,
        metavar="NAME",
        help=f"record only these ({', '.join(NAMES)}); the other fixtures are left as they are",
    )
    args = parser.parse_args()
    wanted = set(args.only or NAMES)

    try:
        settings = get_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    settings = settings.model_copy(update={"llm_provider": args.provider})
    # Built here, not by the app's start-up, so --provider applies (the app reads .env only).
    chosen = select(sorted(wanted))
    real = tuple(recording for recording in chosen if not recording.script)
    recorded, problems = record_with(settings, real) if real else ({}, [])
    for recording in chosen:
        if recording.script:  # each gets its own fake LLM, with just its own replies
            one, more = record_with(settings, (recording,), recording.script)
            recorded |= one
            problems += more

    kept = {name: item for name, item in recorded.items() if name in wanted}
    for path in write_fixtures(kept, args.output):
        print(f"wrote {path}")
    for problem in problems:
        print(f"FAILED {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
