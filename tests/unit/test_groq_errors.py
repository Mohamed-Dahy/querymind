"""Which Groq errors mean "bad answer" (don't retry), "too many requests", or "provider down"."""

import groq
import httpx
import pytest

from querymind.llm.groq_provider import is_output_error, is_rate_limit, retry_after_seconds

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def status_error(
    status: int, body: object, headers: dict[str, str] | None = None
) -> groq.APIStatusError:
    """Build the error the Groq SDK raises for an HTTP error response with this JSON body."""
    response = httpx.Response(status, request=REQUEST, headers=headers)
    kinds: dict[int, type[groq.APIStatusError]] = {
        400: groq.BadRequestError,
        429: groq.RateLimitError,
        500: groq.InternalServerError,
    }
    return kinds[status](f"Error code: {status} - {body}", response=response, body=body)


def groq_body(code: str) -> dict[str, object]:
    return {"error": {"message": "...", "type": "invalid_request_error", "code": code}}


@pytest.mark.parametrize("code", ["json_validate_failed", "tool_use_failed"])
def test_rejected_answers_are_output_errors(code: str) -> None:
    # The model answered in plain text (e.g. "I can't help with that") instead of the schema.
    assert is_output_error(status_error(400, groq_body(code)))


@pytest.mark.parametrize(
    "error",
    [
        status_error(400, groq_body("context_length_exceeded")),  # a different 400
        status_error(429, groq_body("rate_limit_exceeded")),
        status_error(500, groq_body("json_validate_failed")),  # only a 400 is the answer's fault
        status_error(400, "not json"),  # the body couldn't be parsed
        groq.APIConnectionError(request=REQUEST),
    ],
)
def test_other_errors_stay_provider_errors(error: Exception) -> None:
    assert not is_output_error(error)


def test_a_429_is_a_rate_limit_and_nothing_else_is() -> None:
    assert is_rate_limit(status_error(429, groq_body("rate_limit_exceeded")))
    assert not is_rate_limit(status_error(500, groq_body("rate_limit_exceeded")))
    assert not is_rate_limit(groq.APIConnectionError(request=REQUEST))


@pytest.mark.parametrize(
    ("headers", "seconds"),
    [
        ({"retry-after": "17"}, 17.0),
        ({"retry-after": "1.5"}, 1.5),
        ({}, None),
        (
            {"retry-after": "soon"},
            None,
        ),  # an HTTP date: no countdown
        ({"retry-after": "0"}, None),
        ({"retry-after": "inf"}, None),
    ],
)
def test_the_wait_comes_from_the_retry_after_header(
    headers: dict[str, str], seconds: float | None
) -> None:
    error = status_error(429, groq_body("rate_limit_exceeded"), headers)
    assert retry_after_seconds(error) == seconds


def test_other_errors_have_no_wait() -> None:
    assert retry_after_seconds(groq.APIConnectionError(request=REQUEST)) is None
