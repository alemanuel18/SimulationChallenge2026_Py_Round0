# EDA and experiment datasets

Install the project dependencies and run from the repository root:

```powershell
pip install -r requirements.txt
python analysis/eda_pipeline.py --experiment-id disruption_default_seed_2026
```

The pipeline writes:

- `Analysis/normalized/demand_od.csv`: annual demand in long OD format.
- `Analysis/normalized/*_long.csv`: each simulation matrix in long format.
- `Analysis/normalized/od_analysis.csv`: demand, backlog, in-transit and completion metrics joined by OD.
- `Analysis/normalized/port_waiting.csv` and `route_utilization.csv`.
- `Analysis/figures/*.png`: ATT, demand, backlog and utilization charts.
- `Analysis/experimental_summary.csv`: one comparable KPI row per experiment/scenario.

Use a unique `--experiment-id` after each simulation. Reusing an ID replaces its rows instead of duplicating them.

## ML event data

`config/simulation_config.py` enables event logging by default. Each simulation creates:

```text
ML_Data/<run_id>/decisions.csv
ML_Data/<run_id>/shipment_outcomes.csv
```

Set reproducible identifiers and seeds in PowerShell:

```powershell
$env:SIMULATION_RUN_ID = "disruption_default_seed_2026"
$env:SIMULATION_SEED = "2026"
python main.py
```

Join `decisions.csv.entity_id` to `shipment_outcomes.csv.shipment_id` only for shipment decisions (`initial_booking` and `in_transit_replanning`). For `berth_selection`, `entity_id` is a vessel ID and its candidate set is stored in `action_details_json`.

Rows with a negative `measurement_day` belong to warm-up. Keep them for state initialization or exclude them from model training and evaluation. Outcome columns must be labels only; never use completion time or transport time as input features. Incomplete shipments have `is_censored=1`, a blank `transport_time_hours`, and their age at simulation end in `observed_duration_hours`.
