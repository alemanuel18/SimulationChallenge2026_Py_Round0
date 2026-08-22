# WSC Simulation Challenge 2026 - Simulación Marítima (Python)

Este repositorio contiene la base de código en Python para participar en el **WSC Simulation Challenge 2026**. El programa modela una red de transporte marítimo global de contenedores (TEUs), simulando el tránsito de barcos, reservas de carga, operaciones portuarias y congestión bajo diferentes escenarios (incluyendo eventos de disrupción).

El simulador está construido sobre **O2DESPy**, una biblioteca en Python para Simulación de Eventos Discretos Orientada a Objetos (Object-Oriented Discrete-Event Simulation).

---

## Estructura del Proyecto

* **`main.py`**: Punto de entrada principal. Ejecuta la simulación, imprime estadísticas en tiempo real, guarda reportes y levanta el servidor web del dashboard.
* **`config/`**: Configuración central del simulador (`simulation_config.py`), incluyendo días de simulación, período de calentamiento (*warm-up*) y multiplicadores de congestión.
* **`scenario_builders/`**: Constructores de escenarios. Permite alternar entre el escenario base estable (`baseline_stable_scenario.py`) y escenarios con disrupción (`disruption_scenario.py`).
* **`response_strategies/`**: Aquí es donde los participantes implementan sus estrategias de decisión.
  * `user_strategy.py`: Archivo principal donde debes programar tus estrategias personalizadas.
  * `default_strategy.py`: Estrategia por defecto que sirve como *fallback* si tu estrategia no toma una decisión.
  * `strategy_parameters.py`: Contenedor centralizado para pesos, penalizaciones e hiperparámetros.
  * `bayesian_tuner.py`: Optimizador bayesiano de parámetros usando Optuna.
  * `parameter_tuner.py`: Sintonizador por búsqueda binaria de características.
* **`simulation_model/`**: El núcleo de la lógica y clases del modelo de simulación.
* **`maritime_data_context/`**: Clases y estructuras de datos que representan el contexto del negocio marítimo (barcos, puertos, rutas, reservas, etc.).
* **`dashboard/`**: Aplicación web frontend (HTML/CSS/JS) y servidor ligero de desarrollo (`serve_gui.py`) para visualizar interactivamente las estadísticas de salida.
* **`Input/`**: Archivos CSV con los datos de entrada (puertos, rutas de servicio, matriz de demanda, etc.).
* **`Output/`**: Directorio donde se escriben los resultados en formato CSV (KPIs, utilización de rutas, tiempos de transporte).
* **`Logs/`**: Directorio donde se almacenan las bitácoras detalladas del progreso de cada ejecución.
* **`o2despy/`**: Subproyecto local con la librería base de simulación O2DES en Python.

---

## Requisitos Previos

* Python **>= 3.8** (o compatible con las librerías indicadas).
* Entorno de terminal (Linux/macOS o Windows con soporte Bash/PowerShell).

---

## Instalación y Configuración

Se recomienda el uso de un entorno virtual de Python (`venv`) para evitar conflictos de dependencias.

1. **Clonar el repositorio** e ingresar al directorio del proyecto:

    ```bash
    git clone https://github.com/alemanuel18/SimulationChallenge2026_Py_Round0.git
    ```

    ```bash
    cd SimulationChallenge2026_Py_Round0
    ```

2. **Crear y activar un entorno virtual**:
    * **En Linux/macOS:**

        ```bash
        python -m venv .venv
        source .venv/bin/activate
        ```

    * **En Windows (PowerShell):**

        ```powershell
        python -m venv .venv
        .venv\Scripts\Activate.ps1
        ```
    * **Desactivar el entorno virtual:**
        Para salir/desactivar el entorno virtual en cualquier sistema, ejecuta:
        ```bash
        deactivate
        ```

3. **Instalar dependencias**:
    El archivo `requirements.txt` incluye la instalación en modo editable de la librería local `o2despy` (`-e ./o2despy`), además de dependencias externas como `pandas`, `numpy`, `loguru`, `pytest` y `optuna`:

    ```bash
    pip install -r requirements.txt
    ```

---

## Cómo Ejecutar el Programa

### 1. Correr la Simulación

Para iniciar la simulación completa, ejecuta:

```bash
python main.py
```

Al hacerlo:
* Se cargará el escenario configurado (por defecto, el escenario con disrupción).
* Se realizará la fase de calentamiento (*warm-up* de 140 días por defecto) para llevar la red a un estado inicial realista.
* Se ejecutará la simulación de medición (360 días por defecto), mostrando estadísticas consolidadas en consola cada cierto intervalo.
* Al finalizar, escribirá los archivos de resultados en la carpeta `Output/` y guardará la bitácora de eventos en `Logs/`.
* Finalmente, **iniciará de manera automática el servidor web del dashboard** y abrirá tu navegador predeterminado en `http://127.0.0.1:8000/dashboard/`.

### 2. Levantar el Dashboard de Forma Manual

Si deseas abrir el visualizador sin volver a correr la simulación (utilizando los últimos archivos guardados en `Output/`):

```bash
python dashboard/serve_gui.py
```

Abre tu navegador en: [http://127.0.0.1:8000/dashboard/](http://127.0.0.1:8000/dashboard/)

---

## Personalización de Estrategias (Desafío)

El objetivo del desafío es mejorar la eficiencia de la red (por ejemplo, reducir el Average Transport Time de la carga) ante las disrupciones. Para ello, debes modificar el archivo:
👉 **`response_strategies/user_strategy.py`**

Ahí puedes implementar tu propia lógica para:
* `select_vessel_for_berth`: Decidir qué barco entra al muelle primero en puertos congestionados.
* `create_alternative_service_routes`: Crear rutas alternativas aprovechando los barcos y tramos existentes.
* `assign_associated_bookings`: Definir la cadena de reservas inicial para un contenedor.
* `adjust_bookings_before_cargo_handling`: Re-planificar reservas de cargamento en tránsito cuando ocurre una disrupción.

Puedes activar o desactivar tus estrategias en el archivo de configuración `config/simulation_config.py` modificando la variable `ENABLE_STRATEGY`.

---

## Optimización Automática de Parámetros (Tuning)

Para encontrar los mejores hiperparámetros (pesos de atraque, penalizaciones de congestión, ventanas de booking y switches estructurales) sin probar combinaciones manualmente, el proyecto incluye herramientas de optimización automatizada en `response_strategies/`.

### 1. Optimizador Bayesiano Inteligente (`bayesian_tuner.py`)

Utiliza **Optuna** con el estimador TPE multivariado (*Tree-structured Parzen Estimator*). Cuenta con:
* **Warm-Start automático:** Importa el historial de pruebas previas para no empezar de cero.
* **Persistencia en SQLite:** Guarda el estado en `tuning_runs/bayesian_optuna/study.db`, permitiendo pausar con `Ctrl+C` y reanudar en cualquier momento.
* **Afinación continua:** Ajusta finamente pesos continuos sobre la base de las mejores características encontradas.

#### Ejecutar de forma indefinida (Recomendado):
El script corre continuamente por defecto buscando mejores parámetros hasta que lo detengas:

```bash
# Ejecución interactiva en terminal
python response_strategies/bayesian_tuner.py --scope continuous
```

#### Ejecutar en segundo plano (para dejarlo corriendo toda la noche):
```bash
nohup .venv/bin/python response_strategies/bayesian_tuner.py --scope continuous > bayesian_tuning.log 2>&1 &
```

#### Modos de Búsqueda disponibles (`--scope`):
* `--scope continuous` *(por defecto)*: Fija las características estructurales ganadoras (Trial 11) y sintoniza los pesos numéricos continuos.
* `--scope berth-focus`: Enfocado en los pesos de prioridad de atraque personalizado (`berth_*`) y booking.
* `--scope routing-focus`: Enfocado en penalizaciones de almacenamiento, congestión y ventanas de reserva.
* `--scope all`: Optimiza de forma conjunta tanto las variables binarias como las continuas.

#### Opciones de control:
```bash
# Limitar a un número específico de pruebas (ej. 20)
python response_strategies/bayesian_tuner.py --n-trials 20

# Limitar por tiempo en segundos (ej. 8 horas = 28800 segundos)
python response_strategies/bayesian_tuner.py --timeout 28800
```

#### Monitoreo del progreso:
* **Ver registro de pruebas en tiempo real:**
  ```bash
  tail -f response_strategies/tuning_runs/bayesian_optuna/trials.csv
  ```
* **Consultar la mejor configuración alcanzada:**
  ```bash
  cat response_strategies/tuning_runs/bayesian_optuna/best.json
  ```

---

### 2. Sintonizador Binario de Características (`parameter_tuner.py`)

Permite explorar combinaciones binarias puras ($0.0$ / $1.0$) de los 7 módulos de estrategia:

```bash
python response_strategies/parameter_tuner.py
```

---

## Pruebas Unitarias

Para validar el correcto funcionamiento de las utilidades de simulación (`o2despy`), puedes ejecutar las pruebas mediante `pytest`:

```bash
pytest
```
