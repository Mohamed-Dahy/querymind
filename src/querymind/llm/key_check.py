"""Check a user's key before it is saved (5.8): one tiny structured call.

    check_key(provider, entry)   # returns, or raises KeyCheckError with a message for the user

One call tests everything the agent will need: the key is accepted, the model exists, and it can
return our structured output (the SQL step needs it). It costs a few dozen tokens on the user's
own key. The message names the likely fix and never quotes the provider's reply, which can hold
parts of the key or account details.
"""

from pydantic import BaseModel, Field

from querymind.llm.base import LLMProvider
from querymind.llm.catalog import CatalogEntry
from querymind.llm.errors import LLMAuthError, LLMOutputError, LLMProviderError, LLMRateLimitError

PROMPT = "What is 2 + 3? Put the number in `answer`."

# The key check waits less than a question does, and doesn't retry: the user is waiting on a form.
CHECK_TIMEOUT_SECONDS = 20
CHECK_MAX_RETRIES = 0


class KeyCheckError(Exception):
    """The key, the model or the provider failed the check. The message is safe to show."""


class KeyCheckAnswer(BaseModel):
    answer: int = Field(description="The number")


def check_key(provider: LLMProvider, entry: CatalogEntry) -> None:
    """One structured call with the new key.

    Raises:
        KeyCheckError: refused key, over its limit, no structured output, or no answer at all.
    """
    try:
        provider.generate_structured(PROMPT, KeyCheckAnswer)
    except LLMAuthError:
        raise KeyCheckError(
            f"{entry.label} refused this key. Check it was copied whole and is still active."
        ) from None
    except LLMRateLimitError:
        raise KeyCheckError(
            f"{entry.label} says this key is over its limit or out of credit. "
            "Try again later, or check your plan."
        ) from None
    except LLMOutputError:
        raise KeyCheckError(
            "This model can't return structured answers, which QueryMind needs. Pick another model."
        ) from None
    except LLMProviderError:
        raise KeyCheckError(
            f"{entry.label} didn't accept the request. Check the model name "
            f"(for example {entry.example_model}), or try again if {entry.label} is down."
        ) from None
