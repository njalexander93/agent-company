"""Share bounded failures across lifecycle and filesystem operations."""


class WorkspaceError(Exception):
    """Represent a bounded workspace failure without embedding task content."""

    def __init__(
        self, code: str, action: str = "Inspect the assigned workspace; preserve its bytes."
    ) -> None:
        """Store the public diagnostic code and safe recovery instruction.

        Args:
            code: Public bounded diagnostic code.
            action: Safe recovery instruction to attach to the diagnostic.
        """
        # Initialize the bounded public diagnostic and recovery instruction.
        self.code, self.action = code, action
        super().__init__(code)


def require(condition: object, code: str = "INVALID_REQUEST", action: str | None = None) -> None:
    """Stop an operation when its required boundary condition is false.

    Args:
        condition: Required predicate for continuing the operation.
        code: Public bounded diagnostic code.
        action: Safe recovery instruction to attach to the diagnostic.

    Raises:
        WorkspaceError: If the condition does not hold.
    """
    # Reject the failed precondition with its public diagnostic.
    if not condition:
        raise WorkspaceError(
            code, action or "Inspect the request and retry with explicit identities."
        )
