"""Central configuration constants for the simulation."""

import os

# Number of days to run before collecting statistics. This lets the system
# reach a more realistic starting state before KPI measurement begins.
WARM_UP_DAYS = int(os.environ.get("SIM_WARM_UP_DAYS", "140"))

# Number of measured simulation days after warm-up. Output CSVs and dashboard
# charts cover this period only, not the warm-up period.
SIMULATION_DAYS = int(os.environ.get("SIMULATION_DAYS", "360"))

# Length of each statistics/output interval in measured simulation days.
# For example, 5 means KPI rows are written for days 1-5, 6-10, and so on.
STATISTICS_INTERVAL_DAYS = int(os.environ.get("SIM_STATISTICS_INTERVAL_DAYS", "5"))

# Multiplier applied to port handling time when port congestion is modeled.
# Larger values create longer vessel service times at congested ports.
PORT_CONGESTION_MULTIPLIER = 3

# Enables disruption response strategy logic. When true, UserStrategy is tried
# first during active disruptions; if it returns no valid decision, the model
# falls back to DefaultStrategy.
ENABLE_STRATEGY = os.environ.get("SIM_ENABLE_STRATEGY", "True").lower() in {
    "true", "1", "t"
}
