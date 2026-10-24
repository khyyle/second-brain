"""Base class for failures this pipeline expects and can explain"""


class SecondBrainError(Exception):
    """
    A failure with a plain sentence the user can act on.

    Raise a subclass of this when correct code can still hit the failure (a bad file,
    Ollama down, an exhausted account) and the raising code can say what went
    wrong.

    Unexpected failures should keep built-in exceptions so API/worker handlers log
    the traceback and return a generic failure, and thus should not inherit this class.

    Parameters
    ----------
    reason: str
        One sentence, shown by clients and retained in the durable job result/error.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
