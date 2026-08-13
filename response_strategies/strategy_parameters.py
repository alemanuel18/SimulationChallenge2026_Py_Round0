import os

EXPERIMENT = os.environ.get("SIM_EXPERIMENT", "E7")
MIN_REROUTE_SAVING_HOURS = float(os.environ.get("SIM_MIN_REROUTE_SAVING_HOURS", "24.0"))
E1_4_COST_TOLERANCE_HOURS = float(os.environ.get("SIM_E1_4_COST_TOLERANCE_HOURS", "1e-11"))
SAILING_TIE_TOLERANCE_HOURS = float(os.environ.get("SIM_SAILING_TIE_TOLERANCE_HOURS", "1e-9"))

ENABLE_INITIAL_WAIT = os.environ.get("SIM_ENABLE_INITIAL_WAIT", "False").lower() in ("true", "1", "t")
ENABLE_TRANSFER_COST = os.environ.get("SIM_ENABLE_TRANSFER_COST", "False").lower() in ("true", "1", "t")
ENABLE_DYNAMIC_REROUTING = os.environ.get("SIM_ENABLE_DYNAMIC_REROUTING", "False").lower() in ("true", "1", "t")

INITIAL_WAIT_WEIGHT = float(os.environ.get("SIM_INITIAL_WAIT_WEIGHT", "1.0"))
TRANSFER_WAIT_WEIGHT = float(os.environ.get("SIM_TRANSFER_WAIT_WEIGHT", "1.0"))
ENABLE_ALTERNATIVE_ROUTES = os.environ.get("SIM_ENABLE_ALTERNATIVE_ROUTES", "False").lower() in ("true", "1", "t")
E10_MIN_EFFECTIVE_SAVING_RATIO = float(
    os.environ.get("SIM_E10_MIN_EFFECTIVE_SAVING_RATIO", "0.02")
)
E10_MAX_EXTRA_TRANSSHIPMENTS = int(
    os.environ.get("SIM_E10_MAX_EXTRA_TRANSSHIPMENTS", "1")
)
E10_QCR_ALLOWED_INCREASE = float(
    os.environ.get("SIM_E10_QCR_ALLOWED_INCREASE", "0.25")
)
E10_QCR_HIGH_PRESSURE = float(
    os.environ.get("SIM_E10_QCR_HIGH_PRESSURE", "1.00")
)

_VALID_EXPERIMENTS = {"E1", "E1_1", "E1_2", "E1_3", "E1_4", "E1_5", "E1_6", "E2", "E3", "E4", "E5", "E6", "E7", "E10_CHALLENGER", "CUSTOM"}


def configure_experiment(experiment: str | None = None) -> None:
    """Derive the routing flags from one explicit experiment selector."""
    global EXPERIMENT
    global ENABLE_INITIAL_WAIT
    global ENABLE_TRANSFER_COST
    global ENABLE_DYNAMIC_REROUTING
    global MIN_REROUTE_SAVING_HOURS
    global INITIAL_WAIT_WEIGHT
    global TRANSFER_WAIT_WEIGHT
    global ENABLE_ALTERNATIVE_ROUTES

    if experiment is not None:
        EXPERIMENT = experiment

    if EXPERIMENT not in _VALID_EXPERIMENTS:
        raise ValueError(
            f"Unsupported EXPERIMENT={EXPERIMENT!r}. Expected one of: "
            + ", ".join(sorted(_VALID_EXPERIMENTS))
        )

    if EXPERIMENT == "CUSTOM":
        # Leave flags as configured via environment or apply_config_dict
        return

    if EXPERIMENT in {"E1", "E1_1", "E1_2", "E1_3", "E1_4", "E1_5", "E1_6", "E5", "E6", "E7", "E10_CHALLENGER"}:
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


def apply_config_dict(config: dict) -> None:
    """Override parameter values dynamically using a key-value dictionary."""
    global EXPERIMENT
    global MIN_REROUTE_SAVING_HOURS
    global ENABLE_INITIAL_WAIT
    global ENABLE_TRANSFER_COST
    global ENABLE_DYNAMIC_REROUTING
    global INITIAL_WAIT_WEIGHT
    global TRANSFER_WAIT_WEIGHT
    global ENABLE_ALTERNATIVE_ROUTES
    global E10_MIN_EFFECTIVE_SAVING_RATIO
    global E10_MAX_EXTRA_TRANSSHIPMENTS
    global E10_QCR_ALLOWED_INCREASE
    global E10_QCR_HIGH_PRESSURE

    if "EXPERIMENT" in config:
        EXPERIMENT = str(config["EXPERIMENT"])
    if "MIN_REROUTE_SAVING_HOURS" in config:
        MIN_REROUTE_SAVING_HOURS = float(config["MIN_REROUTE_SAVING_HOURS"])
    if "ENABLE_INITIAL_WAIT" in config:
        ENABLE_INITIAL_WAIT = bool(config["ENABLE_INITIAL_WAIT"])
    if "ENABLE_TRANSFER_COST" in config:
        ENABLE_TRANSFER_COST = bool(config["ENABLE_TRANSFER_COST"])
    if "ENABLE_DYNAMIC_REROUTING" in config:
        ENABLE_DYNAMIC_REROUTING = bool(config["ENABLE_DYNAMIC_REROUTING"])
    if "INITIAL_WAIT_WEIGHT" in config:
        INITIAL_WAIT_WEIGHT = float(config["INITIAL_WAIT_WEIGHT"])
    if "TRANSFER_WAIT_WEIGHT" in config:
        TRANSFER_WAIT_WEIGHT = float(config["TRANSFER_WAIT_WEIGHT"])
    if "ENABLE_ALTERNATIVE_ROUTES" in config:
        ENABLE_ALTERNATIVE_ROUTES = bool(config["ENABLE_ALTERNATIVE_ROUTES"])
    if "E10_MIN_EFFECTIVE_SAVING_RATIO" in config:
        E10_MIN_EFFECTIVE_SAVING_RATIO = float(config["E10_MIN_EFFECTIVE_SAVING_RATIO"])
    if "E10_MAX_EXTRA_TRANSSHIPMENTS" in config:
        E10_MAX_EXTRA_TRANSSHIPMENTS = int(config["E10_MAX_EXTRA_TRANSSHIPMENTS"])
    if "E10_QCR_ALLOWED_INCREASE" in config:
        E10_QCR_ALLOWED_INCREASE = float(config["E10_QCR_ALLOWED_INCREASE"])
    if "E10_QCR_HIGH_PRESSURE" in config:
        E10_QCR_HIGH_PRESSURE = float(config["E10_QCR_HIGH_PRESSURE"])


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
