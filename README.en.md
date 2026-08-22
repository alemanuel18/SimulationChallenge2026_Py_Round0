# WSC Simulation Challenge 2026 - Maritime Simulation (Python)

This repository contains the Python codebase to participate in the **WSC Simulation Challenge 2026**. The program models a global container shipping network (TEUs), simulating ship transits, cargo bookings, port operations, and congestion under various scenarios (including disruption events).

The simulator is built on **O2DESPy**, an Object-Oriented Discrete-Event Simulation library in Python.

---

## Project Structure

* **`main.py`**: Principal entry point. Runs the simulation, prints real-time statistics, saves reports, and starts the development dashboard web server.
* **`config/`**: Central simulator configuration (`simulation_config.py`), including simulation days, warm-up period, and congestion multipliers.
* **`scenario_builders/`**: Scenario builders. Allows switching between the stable baseline scenario (`baseline_stable_scenario.py`) and disruption scenarios (`disruption_scenario.py`).
* **`response_strategies/`**: Where participants implement their decision-making strategies.
  * `user_strategy.py`: Main file where you should program your custom strategies.
  * `default_strategy.py`: Default strategy that serves as a fallback if your custom strategy does not make a decision.
  * `strategy_parameters.py`: Centralized container for tunable weights, penalties, and flags.
  * `bayesian_tuner.py`: Bayesian optimization tuner script using Optuna.
  * `parameter_tuner.py`: Binary feature search script.
* **`simulation_model/`**: Core logic and classes of the simulation model.
* **`maritime_data_context/`**: Data structures representing the maritime business context (vessels, ports, routes, bookings, etc.).
* **`dashboard/`**: Frontend web application (HTML/CSS/JS) and lightweight development server (`serve_gui.py`) to interactively visualize output statistics.
* **`Input/`**: Input CSV data files (ports, service routes, demand matrix, etc.).
* **`Output/`**: Directory where CSV results are written (KPIs, route utilization, transport times).
* **`Logs/`**: Directory where detailed event logs are stored.
* **`o2despy/`**: Local subproject containing the base O2DES simulation library.

---

## Prerequisites

* Python **>= 3.8** (or compatible with the listed libraries).
* Terminal environment (Linux/macOS or Windows with Bash/PowerShell support).

---

## Installation and Configuration

We recommend using a Python virtual environment (`venv`) to avoid dependency conflicts.

1. **Clone the repository** and navigate to the project directory:

    ```bash
    git clone https://github.com/alemanuel18/SimulationChallenge2026_Py_Round0.git
    ```

    ```bash
    cd SimulationChallenge2026_Py_Round0
    ```

2. **Create and activate a virtual environment**:
    * **On Linux/macOS:**

        ```bash
        python -m venv .venv
        source .venv/bin/activate
        ```

    * **On Windows (PowerShell):**

        ```powershell
        python -m venv .venv
        .venv\Scripts\Activate.ps1
        ```
    * **Deactivate the virtual environment:**
        To exit/deactivate the virtual environment on any system, run:
        ```bash
        deactivate
        ```

3. **Install dependencies**:
    The `requirements.txt` file installs the local library `o2despy` in editable mode (`-e ./o2despy`), along with external dependencies such as `pandas`, `numpy`, `loguru`, `pytest`, and `optuna`:

    ```bash
    pip install -r requirements.txt
    ```

---

## How to Run the Program

### 1. Run the Simulation

To start the full simulation, run:

```bash
python main.py
```

When run:
* The configured scenario (disruption scenario by default) is loaded.
* A warm-up phase (140 days by default) is executed to bring the network to a realistic initial state.
* The measurement simulation (360 days by default) is run, showing consolidated stats on the console at regular intervals.
* Upon completion, output files are written to `Output/` and log files to `Logs/`.
* Finally, it **automatically starts the dashboard web server** and opens your default browser at `http://127.0.0.1:8000/dashboard/`.

### 2. Run the Dashboard Manually

If you wish to open the visualizer without re-running the simulation (using the last saved files in `Output/`):

```bash
python dashboard/serve_gui.py
```

Open your browser at: [http://127.0.0.1:8000/dashboard/](http://127.0.0.1:8000/dashboard/)

---

## Customizing Strategies (The Challenge)

The goal of the challenge is to improve network efficiency (e.g., reduce Average Transport Time of cargo) under disruptions. To do so, modify:
👉 **`response_strategies/user_strategy.py`**

There you can implement your custom logic to:
* `select_vessel_for_berth`: Decide which vessel enters the berth first at congested ports.
* `create_alternative_service_routes`: Create alternative routes utilizing existing vessels and segments.
* `assign_associated_bookings`: Define the initial chain of bookings for a container.
* `adjust_bookings_before_cargo_handling`: Re-plan transit bookings when a disruption occurs.

You can enable or disable your strategies in `config/simulation_config.py` by modifying the `ENABLE_STRATEGY` variable.

---

## Automatic Parameter Tuning

To find the best hyperparameters (berth priority weights, congestion penalties, booking windows, and structural switches) without manual trial and error, the project includes automated optimization tools in `response_strategies/`.

### 1. Intelligent Bayesian Optimizer (`bayesian_tuner.py`)

Uses **Optuna** with the multivariate TPE (*Tree-structured Parzen Estimator*) sampler. It features:
* **Automatic Warm-Start:** Imports historical trial logs to avoid starting from scratch.
* **SQLite Persistence:** Saves the study state in `tuning_runs/bayesian_optuna/study.db`, allowing you to safely pause with `Ctrl+C` and resume at any time.
* **Continuous Tuning:** Fine-tunes continuous numerical parameters on top of the best structural features found.

#### Run indefinitely (Recommended):
The script runs continuously by default, searching for better parameters until stopped:

```bash
# Interactive run in terminal
python response_strategies/bayesian_tuner.py --scope continuous
```

#### Run in background (to leave it running overnight):
```bash
nohup .venv/bin/python response_strategies/bayesian_tuner.py --scope continuous > bayesian_tuning.log 2>&1 &
```

#### Available Search Scopes (`--scope`):
* `--scope continuous` *(default)*: Fixes the winning structural flags (Trial 11) and tunes the continuous numerical weights and penalties.
* `--scope berth-focus`: Focuses on custom berth priority weights (`berth_*`) and booking parameters.
* `--scope routing-focus`: Focuses on storage/congestion penalties and booking headway windows.
* `--scope all`: Jointly optimizes both binary switches and continuous weights.

#### Execution options:
```bash
# Limit to a specific number of trials (e.g., 20)
python response_strategies/bayesian_tuner.py --n-trials 20

# Limit by duration in seconds (e.g., 8 hours = 28800 seconds)
python response_strategies/bayesian_tuner.py --timeout 28800
```

#### Monitor progress:
* **Monitor trial output in real-time:**
  ```bash
  tail -f response_strategies/tuning_runs/bayesian_optuna/trials.csv
  ```
* **View the best configuration found so far:**
  ```bash
  cat response_strategies/tuning_runs/bayesian_optuna/best.json
  ```

---

### 2. Binary Feature Search (`parameter_tuner.py`)

Allows searching pure binary combinations ($0.0$ / $1.0$) of the 7 strategy modules:

```bash
python response_strategies/parameter_tuner.py
```

---

## Unit Tests

To validate that the simulation utility library (`o2despy`) is working correctly, you can run the test suite using `pytest`:

```bash
pytest
```
