"""Classified failures from a model provider."""

from __future__ import annotations

from enum import Enum

_BAD_REQUEST_STATUS = 400
_UNAVAILABLE_MIN_STATUS = 500
_REJECTED_MESSAGE_LIMIT = 200

# Anthropic's documented way to tell a tier's monthly spend cap apart from a
# rate limit is by checking if the following string is in the error body
# as both are reported as a 429 rate_limit_error
_SPEND_CAP_ERROR_CODE = "enforced_spend_limit_reached"


class ModelErrorKind(Enum):
    """Which class of provider failure a caller should branch on."""

    ACCOUNT = "account"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    TOO_LARGE = "too_large"
    REJECTED = "rejected"


class ModelError(Exception):
    """
    A provider failure reported as a kind + a sentence the user can act on.

    Parameters
    ----------
    kind: ModelErrorKind
        Class of failure.
    reason: str
        Short plain-language sentence. ``str(error)`` returns this sentence.
    """

    def __init__(self, kind: ModelErrorKind, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


_OUT_OF_CREDITS = (ModelErrorKind.ACCOUNT, "Out of API credits")
_SOURCE_TOO_LARGE = (ModelErrorKind.TOO_LARGE, "The source is too large for the model")
_SPEND_CAP_REACHED = (ModelErrorKind.ACCOUNT, "Reached your API account's monthly spend cap")

_STATUS_FAILURES: dict[int, tuple[ModelErrorKind, str]] = {
    401: (ModelErrorKind.ACCOUNT, "The API key was rejected"),
    402: _OUT_OF_CREDITS,
    403: (ModelErrorKind.ACCOUNT, "The API key doesn't have access to this model"),
    404: (ModelErrorKind.ACCOUNT, "This model isn't available to your account"),
    408: (ModelErrorKind.NETWORK, "The model provider timed out"),
    413: _SOURCE_TOO_LARGE,
    429: (ModelErrorKind.RATE_LIMIT, "The model provider is rate limiting requests"),
}

# Anthropic reports several unrelated conditions as 400 invalid_request_error,
# so only the message tells them apart. An empty prepaid balance and a spend
# limit the user set both arrive this way rather than as 402 or 429.
_BAD_REQUEST_FAILURES: tuple[tuple[str, tuple[ModelErrorKind, str]], ...] = (
    ("credit balance", _OUT_OF_CREDITS),
    (
        "specified api usage limits",
        (ModelErrorKind.ACCOUNT, "Reached the API spend limit set on your account"),
    ),
    (
        "specified workspace api usage limits",
        (ModelErrorKind.ACCOUNT, "Reached the API spend limit set on your workspace"),
    ),
    ("prompt is too long", _SOURCE_TOO_LARGE),
)


def classify_status(
    status_code: int | None, message: str, error_code: str | None = None
) -> ModelError:
    """
    Map an HTTP status and provider message to one provider failure.

    Parameters
    ----------
    status_code: int | None
        HTTP status from the provider, or ``None`` when there was no
        response (connection failure or timeout).
    message: str
        Provider error text. Used to tell apart 400 responses and for the
        fallback reason.
    error_code: str | None
        Machine-readable code from the error body's ``details``, when the
        provider sends one.

    Returns
    -------
    ModelError
        The classified failure.

    Notes
    -----
    Classification uses the status code, error code, and response text.
    Provider SDKs do not have one exception type per status (there is no
    type for HTTP 402), and more than one provider is reached through the
    same client.
    """
    if status_code is None:
        return ModelError(ModelErrorKind.NETWORK, "Couldn't reach the model provider")
    if error_code == _SPEND_CAP_ERROR_CODE:
        return ModelError(*_SPEND_CAP_REACHED)
    if status_code == _BAD_REQUEST_STATUS:
        lowered = message.lower()
        for marker, failure in _BAD_REQUEST_FAILURES:
            if marker in lowered:
                return ModelError(*failure)
    if status_code in _STATUS_FAILURES:
        return ModelError(*_STATUS_FAILURES[status_code])
    if status_code >= _UNAVAILABLE_MIN_STATUS:
        return ModelError(ModelErrorKind.NETWORK, "The model provider is unavailable")

    trimmed = message.strip()[:_REJECTED_MESSAGE_LIMIT]
    return ModelError(
        ModelErrorKind.REJECTED,
        f"The model provider rejected the request: {trimmed}",
    )
