"""Simple experiment switches for response-strategy experiments.

Select one experiment explicitly and derive the individual flags from it.
This keeps E1/E1_1/E1_2/E1_3/E1_4/E1_5/E2/E3/E4 configuration consistent without touching routing
behavior.
"""

EXPERIMENT = "E1_5"
MIN_REROUTE_SAVING_HOURS = 24.0
E1_4_COST_TOLERANCE_HOURS = 1e-11

ENABLE_INITIAL_WAIT = False
ENABLE_TRANSFER_COST = False
ENABLE_DYNAMIC_REROUTING = False

_VALID_EXPERIMENTS = {"E1", "E1_1", "E1_2", "E1_3", "E1_4", "E1_5", "E2", "E3", "E4"}


def configure_experiment(experiment: str | None = None) -> None:
    """Derive the routing flags from one explicit experiment selector."""
    global EXPERIMENT
    global ENABLE_INITIAL_WAIT
    global ENABLE_TRANSFER_COST
    global ENABLE_DYNAMIC_REROUTING

    if experiment is not None:
        EXPERIMENT = experiment

    if EXPERIMENT not in _VALID_EXPERIMENTS:
        raise ValueError(
            f"Unsupported EXPERIMENT={EXPERIMENT!r}. Expected one of: "
            + ", ".join(sorted(_VALID_EXPERIMENTS))
        )

    if EXPERIMENT == "E1":
        ENABLE_INITIAL_WAIT = False
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E1_1":
        ENABLE_INITIAL_WAIT = False
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E1_2":
        ENABLE_INITIAL_WAIT = False
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E1_3":
        ENABLE_INITIAL_WAIT = False
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E1_4":
        ENABLE_INITIAL_WAIT = False
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E1_5":
        ENABLE_INITIAL_WAIT = False
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E2":
        ENABLE_INITIAL_WAIT = True
        ENABLE_TRANSFER_COST = False
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E3":
        ENABLE_INITIAL_WAIT = True
        ENABLE_TRANSFER_COST = True
        ENABLE_DYNAMIC_REROUTING = False
    elif EXPERIMENT == "E4":
        ENABLE_INITIAL_WAIT = True
        ENABLE_TRANSFER_COST = True
        ENABLE_DYNAMIC_REROUTING = True


def validate_wait_configuration() -> None:
    """Reject invalid experiment settings early."""
    if EXPERIMENT not in _VALID_EXPERIMENTS:
        raise ValueError(
            f"Unsupported EXPERIMENT={EXPERIMENT!r}. Expected one of: "
            + ", ".join(sorted(_VALID_EXPERIMENTS))
        )
    if ENABLE_TRANSFER_COST and not ENABLE_INITIAL_WAIT:
        raise ValueError(
            "ENABLE_TRANSFER_COST requires ENABLE_INITIAL_WAIT so transfer waits "
            "have a baseline expected wait to charge."
        )
    if ENABLE_DYNAMIC_REROUTING and not (ENABLE_INITIAL_WAIT and ENABLE_TRANSFER_COST):
        raise ValueError(
            "ENABLE_DYNAMIC_REROUTING requires E3 routing semantics "
            "(ENABLE_INITIAL_WAIT and ENABLE_TRANSFER_COST both enabled)."
        )


configure_experiment()
