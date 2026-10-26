"""Write the API's OpenAPI schema to web/openapi.json (the web UI generates its types from it).

    uv run python scripts/export_openapi.py
    npm --prefix web run gen:api          # then: OpenAPI → web/src/api/schema.d.ts

No server, database, LLM or .env needed: `create_app()` builds the pipeline only when the server
starts, and the schema comes from the routes and Pydantic models alone. The output is stable (sorted
keys, LF line endings), so CI can regenerate it and fail when the committed copy is out of date.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from querymind.api.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def openapi_schema() -> dict[str, Any]:
    """The OpenAPI document FastAPI serves at /openapi.json, built without starting the server."""
    return create_app().openapi()


def render(schema: dict[str, Any]) -> str:
    """The schema as JSON text that only changes when the API changes."""
    # ensure_ascii=False keeps characters like "—" readable instead of "—".
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write it")
    args = parser.parse_args()

    # utf-8 + "\n" on every OS: Windows would otherwise write cp1252 and CRLF, and the
    # file would differ from the one CI generates on Linux.
    args.output.write_text(render(openapi_schema()), encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
