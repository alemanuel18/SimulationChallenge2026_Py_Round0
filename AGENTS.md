# Repository Guidelines

## Project Structure & Module Organization
- `main.py` is the main entry point for running the simulation and generating reports.
- `config/`, `scenario_builders/`, `simulation_model/`, `maritime_data_context/`, and `response_strategies/` contain the core Python model and decision logic.
- `dashboard/` holds the web UI (`index.html`, `app.js`, `styles.css`) plus a local server in `serve_gui.py`.
- `Input/` stores source CSV data; `Output/` and `Logs/` store generated simulation artifacts.
- `o2despy/` is the bundled simulation library and has its own tests under `o2despy/tests/`.

## Build, Test, and Development Commands
- `pip install -r requirements.txt` installs the project plus the local editable `o2despy` package.
- `python main.py` runs the full simulation and writes results to `Output/` and `Logs/`.
- `python dashboard/serve_gui.py` serves the dashboard using the latest generated output.
- `pytest` runs the test suite for `o2despy`; use this after changing shared simulation utilities.

## Coding Style & Naming Conventions
- Use Python 3 style with 4-space indentation and `snake_case` for modules, functions, and variables.
- Use `PascalCase` for classes and keep file names descriptive, e.g. `vessel_generator.py`.
- Keep changes compatible with the formatter/lint settings defined in `o2despy/pyproject.toml` (`black`, `isort`, `ruff`).
- Avoid committing generated cache files such as `__pycache__/` or `*.pyc`.

## Testing Guidelines
- Tests follow `pytest` conventions: files named `test_*.py`, functions named `test_*`.
- Existing package tests live in `o2despy/tests/`; add new tests there when changing reusable simulation code.
- Prefer focused tests for scenario logic, strategy decisions, and file-output helpers.

## Commit & Pull Request Guidelines
- Commit messages in the history are short and descriptive, often in Spanish and in present tense, e.g. `Se añaden los resultados del caso E3`.
- Keep commits scoped to one logical change: code, scenario data, or generated results.
- Pull requests should explain the simulation impact, list changed inputs/outputs, and include screenshots for dashboard updates.
- If you update generated `Logs/` or `Output/` files intentionally, say why in the PR description.

## Configuration Tips
- Strategy logic lives in `response_strategies/user_strategy.py`.
- Simulation parameters are centralized in `config/simulation_config.py`.
