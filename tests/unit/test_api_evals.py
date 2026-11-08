"""GET /evals over the real committed file and small files of our own. No DB, no LLM."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from querymind.api.evals import EvalsData
from querymind.api.main import create_app

SNAPSHOT = Path("evals/snapshot.json")
NO_FILE = Path("does/not/exist.json")


def client(snapshot: Path = SNAPSHOT, bird: Path = NO_FILE) -> TestClient:
    app = create_app(pipeline=object(), evals=EvalsData(snapshot, bird))  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def bird_file(path: Path, *, leave_out: str | None = None, evidence: str = "") -> Path:
    """A downloaded-questions file with every BIRD id in the snapshot, in BIRD's own shape."""
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    rows = [
        {
            "question_id": int(question["id"].removeprefix("bird_")),
            "db_id": "x",
            "question": f"text of {question['id']}",
            "evidence": evidence,
            "SQL": f"SELECT '{question['id']}'",
            "difficulty": "simple",
        }
        for question in snapshot["questions"]
        if question["study"] == "bird" and question["id"] != leave_out
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_it_serves_the_committed_results_with_no_database() -> None:
    with client() as api:
        response = api.get("/evals")

    assert response.status_code == 200
    body = response.json()
    assert len(body["runs"]) == 22
    assert {study["name"] for study in body["ablations"]} == {"golden", "bird"}
    assert body["gate"]["failure_proven"] is True  # the red test, 2026-11-08 (PLAN.md 4.7)


def test_without_the_download_bird_has_no_text_and_says_so() -> None:
    with client() as api:
        body = api.get("/evals").json()

    bird = [question for question in body["questions"] if question["study"] == "bird"]
    assert body["bird_text_available"] is False
    assert all(question["question"] is None for question in bird)
    assert body["bird"]["licence"] == "CC BY-SA 4.0"


def test_with_the_download_bird_gets_its_text(tmp_path: Path) -> None:
    with client(bird=bird_file(tmp_path / "bird.json")) as api:
        body = api.get("/evals").json()

    questions = {question["id"]: question for question in body["questions"]}
    bird = next(q for q in questions.values() if q["study"] == "bird")
    assert body["bird_text_available"] is True
    assert bird["question"] == f"text of {bird['id']}"
    assert bird["reference_sql"] == f"SELECT '{bird['id']}'"
    # Our own questions keep their own text.
    golden = next(q for q in questions.values() if q["study"] == "golden")
    assert not golden["question"].startswith("text of")


def test_bird_shows_the_hint_the_agent_was_given(tmp_path: Path) -> None:
    with client(bird=bird_file(tmp_path / "bird.json", evidence=" a hint ")) as api:
        body = api.get("/evals").json()

    bird = next(q for q in body["questions"] if q["study"] == "bird")
    assert bird["question"] == f"text of {bird['id']}\nHint: a hint"


def test_a_download_missing_a_question_is_not_half_used(tmp_path: Path) -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    one = next(q["id"] for q in snapshot["questions"] if q["study"] == "bird")

    with client(bird=bird_file(tmp_path / "bird.json", leave_out=one)) as api:
        body = api.get("/evals").json()

    assert body["bird_text_available"] is False
    assert all(q["question"] is None for q in body["questions"] if q["study"] == "bird")


def test_a_broken_download_is_ignored_not_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "bird.json"
    broken.write_text("not json", encoding="utf-8")

    with client(bird=broken) as api:
        response = api.get("/evals")

    assert response.status_code == 200
    assert response.json()["bird_text_available"] is False


def test_a_missing_results_file_is_a_clean_503() -> None:
    with client(snapshot=NO_FILE) as api:
        response = api.get("/evals")

    assert response.status_code == 503
    assert response.json()["error"] == "evals_unavailable"
    assert "does/not" not in response.text  # no server path in the reply


def test_a_broken_results_file_is_a_clean_503(tmp_path: Path) -> None:
    broken = tmp_path / "snapshot.json"
    broken.write_text('{"format": 1}', encoding="utf-8")

    with client(snapshot=broken) as api:
        response = api.get("/evals")

    assert response.status_code == 503
    assert response.json()["error"] == "evals_unavailable"


def test_a_results_file_in_another_format_is_a_clean_503(tmp_path: Path) -> None:
    other = tmp_path / "snapshot.json"
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    other.write_text(json.dumps({**data, "format": 99}), encoding="utf-8")

    with client(snapshot=other) as api:
        assert api.get("/evals").status_code == 503


def test_a_file_that_appears_later_is_found_without_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    with client(snapshot=path) as api:
        assert api.get("/evals").status_code == 503
        path.write_bytes(SNAPSHOT.read_bytes())
        assert api.get("/evals").status_code == 200
