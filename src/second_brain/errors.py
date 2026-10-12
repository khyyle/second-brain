"""Base class for failures this pipeline expects and can explain"""


class SecondBrainError(Exception):
    """
    A failure with a plain sentence the user can act on.

    Raise a subclass of this when correct code can still hit the failure (a bad file,
    Ollama down, an exhausted account) and the raising code can say what went
    wrong.

    Unexpected failures should keep built-in exceptions so the CLI reports them
    as crashes, and thus should not inherit this class.

    Parameters
    ----------
    reason: str
        One sentence, shown as-is by the CLI and recorded in the run record.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
