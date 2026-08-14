"""Normalize simulation matrices and generate reproducible EDA artifacts.

Run from the project root:
    python analysis/eda_pipeline.py --experiment-id disruption_default_seed_2026
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


MATRIX_OUTPUTS = {
    "Average_Origin_Waiting_TEU_By_OD.csv": "average_origin_waiting_teu",
    "Average_In_Transit_TEU_By_OD.csv": "average_in_transit_teu",
    "Cumulative_Completed_TEU_By_OD.csv": "cumulative_completed_teu",
}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False),
        errors="coerce",
    )


def normalize_matrix(path: Path, value_name: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    origin_column = frame.columns[0]
    long = frame.melt(id_vars=origin_column, var_name="destination", value_name=value_name)
    long = long.rename(columns={origin_column: "origin"})
    long[value_name] = _numeric(long[value_name]).fillna(0)
    return long[long["origin"].str.upper().ne("TOTAL")].reset_index(drop=True)


def load_att(path: Path, scenario: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["PeriodIndex"] = pd.to_numeric(frame["PeriodIndex"], errors="coerce")
    frame = frame.dropna(subset=["PeriodIndex"]).copy()
    for column in ("StartDay", "EndDay", "AverageTransportTime"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["scenario"] = scenario
    return frame


def build_normalized_tables(input_dir: Path, output_dir: Path, normalized_dir: Path) -> dict[str, pd.DataFrame]:
    normalized_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, pd.DataFrame] = {}

    demand = normalize_matrix(input_dir / "demand_matrix.csv", "annual_teu")
    demand.to_csv(normalized_dir / "demand_od.csv", index=False)
    tables["demand"] = demand

    od = demand.copy()
    for filename, value_name in MATRIX_OUTPUTS.items():
        table = normalize_matrix(output_dir / filename, value_name)
        table.to_csv(normalized_dir / f"{Path(filename).stem}_long.csv", index=False)
        od = od.merge(table, on=["origin", "destination"], how="left")
    value_columns = ["average_origin_waiting_teu", "average_in_transit_teu", "cumulative_completed_teu"]
    od[value_columns] = od[value_columns].fillna(0)
    od["completion_ratio"] = np.where(od["annual_teu"] > 0, od["cumulative_completed_teu"] / od["annual_teu"], np.nan)
    od["waiting_ratio"] = np.where(od["annual_teu"] > 0, od["average_origin_waiting_teu"] / od["annual_teu"], np.nan)
    od["in_transit_ratio"] = np.where(od["annual_teu"] > 0, od["average_in_transit_teu"] / od["annual_teu"], np.nan)
    od.to_csv(normalized_dir / "od_analysis.csv", index=False)
    tables["od"] = od

    ports = pd.read_csv(output_dir / "Port_Waiting_Statistics.csv")
    ports = ports[ports["Port"].str.upper().ne("TOTAL")].copy()
    for column in ports.columns[1:]:
        ports[column] = _numeric(ports[column]).fillna(0)
    ports.to_csv(normalized_dir / "port_waiting.csv", index=False)
    tables["ports"] = ports

    routes = pd.read_csv(output_dir / "Service_Route_Utilization.csv")
    routes = routes[routes["Route"].str.upper().ne("TOTAL")].copy()
    for column in ("Avg Capacity TEU", "Avg Carried TEU", "Utilization"):
        routes[column] = _numeric(routes[column]).fillna(0)
    routes.to_csv(normalized_dir / "route_utilization.csv", index=False)
    tables["routes"] = routes
    return tables


def generate_figures(tables: dict[str, pd.DataFrame], output_dir: Path, figures_dir: Path) -> pd.DataFrame:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is required for the figures. Run: pip install -r requirements.txt"
        ) from exc
    figures_dir.mkdir(parents=True, exist_ok=True)
    current = load_att(output_dir / "ATT_By_Statistics_Interval.csv", "current")
    att_frames = [current]
    baseline_path = output_dir / "Baseline_ATT_By_Statistics_Interval.csv"
    if baseline_path.exists():
        att_frames.append(load_att(baseline_path, "baseline"))
    att = pd.concat(att_frames, ignore_index=True)

    fig, ax = plt.subplots(figsize=(11, 5))
    for scenario, values in att.groupby("scenario"):
        ax.plot(values["EndDay"], values["AverageTransportTime"], label=scenario)
    ax.axvspan(60, 80, color="orange", alpha=.18, label="Kaohsiung disruption")
    ax.axvspan(120, 139, color="red", alpha=.12, label="Cartagena disruption")
    ax.set(title="Average Transport Time by period", xlabel="Measurement day", ylabel="ATT (days)")
    ax.legend()
    fig.tight_layout(); fig.savefig(figures_dir / "att_timeline.png", dpi=160); plt.close(fig)

    pivot = tables["demand"].pivot(index="origin", columns="destination", values="annual_teu")
    fig, ax = plt.subplots(figsize=(12, 9))
    image = ax.imshow(pivot.fillna(0), aspect="auto", cmap="Blues")
    ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(pivot.index)), pivot.index, fontsize=7)
    ax.set_title("Annual OD demand (TEU)"); fig.colorbar(image, ax=ax, label="TEU")
    fig.tight_layout(); fig.savefig(figures_dir / "demand_heatmap.png", dpi=160); plt.close(fig)

    ports = tables["ports"].nlargest(12, "Total Waiting TEU").sort_values("Total Waiting TEU")
    fig, ax = plt.subplots(figsize=(9, 6)); ax.barh(ports["Port"], ports["Total Waiting TEU"])
    ax.set(title="Ports with greatest average cargo backlog", xlabel="Waiting TEU")
    fig.tight_layout(); fig.savefig(figures_dir / "port_backlog.png", dpi=160); plt.close(fig)

    routes = tables["routes"].sort_values("Utilization")
    fig, ax = plt.subplots(figsize=(9, 5)); ax.barh(routes["Route"], routes["Utilization"])
    ax.set(title="Service route utilization", xlabel="Utilization (%)")
    fig.tight_layout(); fig.savefig(figures_dir / "route_utilization.png", dpi=160); plt.close(fig)

    od = tables["od"]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(od["annual_teu"], od["average_origin_waiting_teu"], alpha=.55, s=18)
    ax.set(title="OD demand versus origin backlog", xlabel="Annual demand (TEU)", ylabel="Average waiting TEU")
    fig.tight_layout(); fig.savefig(figures_dir / "od_demand_vs_backlog.png", dpi=160); plt.close(fig)
    return att


def update_experiment_table(
    experiment_id: str, att: pd.DataFrame, tables: dict[str, pd.DataFrame], analysis_dir: Path,
) -> Path:
    rows = []
    for scenario, values in att.groupby("scenario"):
        rows.append({
            "experiment_id": experiment_id if scenario == "current" else f"{experiment_id}_baseline",
            "scenario": scenario,
            "period_count": len(values),
            "att_mean_days": values["AverageTransportTime"].mean(),
            "att_std_days": values["AverageTransportTime"].std(),
            "att_min_days": values["AverageTransportTime"].min(),
            "att_max_days": values["AverageTransportTime"].max(),
            "completed_teu": tables["od"]["cumulative_completed_teu"].sum() if scenario == "current" else np.nan,
            "average_origin_waiting_teu": tables["od"]["average_origin_waiting_teu"].sum() if scenario == "current" else np.nan,
            "average_in_transit_teu": tables["od"]["average_in_transit_teu"].sum() if scenario == "current" else np.nan,
        })
    new_rows = pd.DataFrame(rows)
    path = analysis_dir / "experimental_summary.csv"
    if path.exists():
        existing = pd.read_csv(path)
        existing = existing[~existing["experiment_id"].isin(new_rows["experiment_id"])]
        new_rows = pd.concat([existing, new_rows], ignore_index=True)
    new_rows.to_csv(path, index=False)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("Input"))
    parser.add_argument("--output-dir", type=Path, default=Path("Output"))
    parser.add_argument("--analysis-dir", type=Path, default=Path("Analysis"))
    parser.add_argument("--experiment-id", default="current_run")
    args = parser.parse_args()
    normalized = args.analysis_dir / "normalized"
    tables = build_normalized_tables(args.input_dir, args.output_dir, normalized)
    att = generate_figures(tables, args.output_dir, args.analysis_dir / "figures")
    summary = update_experiment_table(args.experiment_id, att, tables, args.analysis_dir)
    print(f"Normalized tables: {normalized.resolve()}")
    print(f"Figures: {(args.analysis_dir / 'figures').resolve()}")
    print(f"Experiment table: {summary.resolve()}")


if __name__ == "__main__":
    main()
