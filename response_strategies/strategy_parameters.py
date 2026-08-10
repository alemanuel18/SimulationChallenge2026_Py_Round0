"""Simple experiment switches for response-strategy experiments.

Keep these flags lightweight so E1/E2/E3 can be toggled without touching the
simulator or the default strategy implementation.
"""

ENABLE_INITIAL_WAIT = True
ENABLE_TRANSFER_COST = True
ENABLE_DYNAMIC_REROUTING = False


def validate_wait_configuration() -> None:
    """Reject ambiguous wait-setting combinations early."""
    if ENABLE_TRANSFER_COST and not ENABLE_INITIAL_WAIT:
        raise ValueError(
            "ENABLE_TRANSFER_COST requires ENABLE_INITIAL_WAIT so transfer waits "
            "have a baseline expected wait to charge."
        )
