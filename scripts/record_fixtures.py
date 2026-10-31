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
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.api.main import create_app
from querymind.config import ConfigError, get_settings
from querymind.llm import create_embedding_provider, create_llm_provider

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "web" / "src" / "mocks" / "recorded"


@dataclass(frozen=True)
class Recording:
    name: str  # saved as <name>.json
    question: str
    kind: Literal["answer", "clarification", "refusal"]  # the reply it must get
    follows: str | None = None  # the recording whose conversation this one continues


RECORDINGS = (
    Recording("answer", "What is the total sales amount per category?", "answer"),
    Recording("followup", "Now only for 1997.", "answer", follows="answer"),
    # Ambiguous on purpose (evals/golden/clarification.yaml c02): by units, orders or revenue?
    Recording("clarification", "Which products are the most popular?", "clarification"),
    Recording("refusal", "Delete all orders from 1996.", "refusal"),
    # U1.5's charts: a time column (a line chart), and a single number (no chart).
    Recording("monthly", "What were the total sales per month in 1997?", "answer"),
    Recording("single", "How many customers are there?", "answer"),
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


def record(
    client: TestClient, recordings: tuple[Recording, ...] = RECORDINGS
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Ask each question; return the replies of the right kind, and a problem for every other."""
    replies: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for recording in recordings:
        body: dict[str, str] = {"question": recording.question}
        if recording.follows is not None:
            earlier = replies.get(recording.follows)
            if earlier is None:
                problems.append(f"{recording.name}: skipped, {recording.follows!r} failed")
                continue
            body["conversation_id"] = earlier["conversation_id"]

        response = client.post("/ask", json=body)
        reply: dict[str, Any] = response.json()
        if response.status_code != 200:
            problems.append(
                f"{recording.name}: HTTP {response.status_code} "
                f"{reply.get('error')}: {reply.get('message')}"
            )
        elif reply.get("kind") != recording.kind:
            problems.append(
                f"{recording.name}: wanted kind {recording.kind!r}, got {reply.get('kind')!r}"
            )
        else:
            replies[recording.name] = reply
    return replies, problems


def write_fixtures(replies: dict[str, dict[str, Any]], output_dir: Path) -> list[Path]:
    """One <name>.json per reply, in UTF-8 with LF line endings on every OS."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, reply in replies.items():
        path = output_dir / f"{name}.json"
        text = json.dumps(reply, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


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
    pipeline = Pipeline(
        llm=create_llm_provider(settings),
        settings=settings,
        embeddings=create_embedding_provider(settings),
    )
    # raise_server_exceptions=False: a bug comes back as the API's 500 body, like over HTTP.
    with TestClient(create_app(pipeline=pipeline), raise_server_exceptions=False) as client:
        replies, problems = record(client, select(sorted(wanted)))

    kept = {name: reply for name, reply in replies.items() if name in wanted}
    for path in write_fixtures(kept, args.output):
        print(f"wrote {path}")
    for problem in problems:
        print(f"FAILED {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
