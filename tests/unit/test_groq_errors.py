"""Which Groq errors mean "bad answer" (don't retry) rather than "provider down" (retry)."""

import groq
import httpx
import pytest

from querymind.llm.groq_provider import is_output_error

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def status_error(status: int, body: object) -> groq.APIStatusError:
    """Build the error the Groq SDK raises for an HTTP error response with this JSON body."""
    response = httpx.Response(status, request=REQUEST)
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
