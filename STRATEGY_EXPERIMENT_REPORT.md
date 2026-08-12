# WSC Simulation Challenge 2026 — Strategy Experiment Report

## 1. Executive Summary

Se evaluó una secuencia completa de experimentos de estrategia sobre la simulación marítima discreta del repositorio. El objetivo fue reducir el `ATT` (Average Transport Time) respecto al comportamiento `Original / DefaultStrategy`, sin romper la semántica operacional del sistema.

Resultado principal:

- `Original` obtuvo `ATT = 20.28` días y `PeriodStdDev = 0.87`.
- Ningún experimento custom superó a `Original`.
- El mejor resultado custom observado fue `20.37` días, alcanzado por `E1_3`, `E1_6` y `E6`.
- La brecha frente a `Original` es:
  - `+0.09` días
  - `+2.16` horas
  - `+0.44%`

La conclusión final es clara: la mejor estrategia de envío para esta versión del repositorio sigue siendo `Original / DefaultStrategy`. La iteración experimental sí produjo descubrimientos valiosos de corrección y modelado, pero no una mejora agregada del KPI principal.

## 2. Simulation and Optimization Context

La simulación modela una red logística marítima como un sistema de eventos discretos:

- `Shipments` representan carga medida en TEU.
- `Vessels` navegan rutas cíclicas (`ServiceRoutes`).
- `Ports` contienen `Berths` y colas de espera.
- `Bookings` enlazan un shipment con segmentos de ruta concretos.
- `Disruptions` alteran la disponibilidad de berths o multiplican el costo de legs.

La métrica objetivo es `ATT`, calculada en el archivo de salida `ATT_By_Statistics_Interval.csv`. El resumen relevante es la fila `OverallMean`; la dispersión temporal se reporta en `PeriodStdDev`.

El espacio de optimización del repositorio está delimitado por `response_strategies/`, donde se concentran cuatro hooks de decisión:

- `select_vessel_for_berth(...)`
- `create_alternative_service_routes(...)`
- `assign_associated_bookings(...)`
- `adjust_bookings_before_cargo_handling(...)`

La experiencia completa mostró que una mejora estática o local en una ruta no necesariamente se traduce en menor `ATT` global. El sistema tiene acoplamientos dinámicos entre colas, berth service, disponibilidad de vessels, booking chains y replanificación durante disruption.

## 3. Original / Default Strategy

La estrategia base está implementada en [`response_strategies/default_strategy.py`](response_strategies/default_strategy.py) y sirve como referencia operacional.

Comportamiento relevante:

- `select_vessel_for_berth(...)` usa una puntuación híbrida normalizada:
  - 40% tiempo de espera
  - 30% TEU transportado
  - 20% capacidad del vessel
  - penalización de 10% por carga de handling esperada
  - desempate por orden de cola
- `assign_associated_bookings(...)` construye el camino más corto por distancia entre origen y destino usando candidatos derivados de la red actual.
- `create_alternative_service_routes(...)`:
  - restaura vessels que ya no corresponden a una ruta alternativa activa,
  - detecta disruptions activas,
  - construye rutas alternativas desde legs existentes,
  - reserva un vessel elegible de la ruta origen.
- `adjust_bookings_before_cargo_handling(...)` replanifica bookings cuando la parte no completada de un shipment queda afectada por una disruption y puede fusionar la parte completada con el nuevo suffix si ambos continúan en la misma `ServiceRoute`.

Benchmark oficial:

- `ATT = 20.28`
- `PeriodStdDev = 0.87`

## 4. Experimental Method

Se trabajó con el escenario de disruptions estándar del repositorio y con los archivos generados por cada estrategia en `Output/<strategy>/`.

Metodología:

- El archivo `ATT_By_Statistics_Interval.csv` fue la fuente principal de medición.
- Cada archivo incluye 72 intervalos de 5 días, más dos filas finales de resumen:
  - `OverallMean`
  - `PeriodStdDev`
- La comparación principal se hizo sobre `OverallMean`.
- `PeriodStdDev` se usó como indicador de estabilidad temporal del comportamiento.
- Antes de estrategias más costosas se realizaron diagnósticos estáticos y comparaciones dirigidas para evitar iteraciones de simulación innecesarias.

Lección metodológica clave:

> una ruta con menor costo esperado no necesariamente produce menor `ATT` realizado en una simulación discreta con colas, berths, handling y feedback dinámico.

## 5. Complete Results Table

Los valores siguientes fueron verificados en los CSV de salida `Output/<strategy>/ATT_By_Statistics_Interval.csv`, leyendo las filas `OverallMean` y `PeriodStdDev`.

| Strategy | ATT (days) | Period StdDev | Delta vs Original (days) | Delta vs Original (%) | Verdict |
|---|---:|---:|---:|---:|---|
| Original | 20.28 | 0.87 | 0.00 | 0.00% | WINNER |
| E1 | 21.76 | 1.60 | +1.48 | +7.30% | Strong regression |
| E1_1 | 21.77 | 1.62 | +1.49 | +7.35% | Strong regression |
| E1_2 | 20.52 | 1.30 | +0.24 | +1.18% | Diagnostic / correctness |
| E1_3 | 20.37 | 1.16 | +0.09 | +0.44% | Best custom / close |
| E1_4 | 20.41 | 1.13 | +0.13 | +0.64% | Regression |
| E1_5 | 20.45 | 1.35 | +0.17 | +0.84% | Regression |
| E1_6 | 20.37 | 1.16 | +0.09 | +0.44% | Best custom / correctness fix |
| E2 | 21.87 | 0.95 | +1.59 | +7.84% | Strong regression |
| E3 | 21.75 | 1.16 | +1.47 | +7.25% | Strong regression |
| E4 | 21.77 | 1.11 | +1.49 | +7.35% | Strong regression |
| E5 | 21.61 | 1.04 | +1.33 | +6.56% | Regression |
| E6 | 20.37 | 1.16 | +0.09 | +0.44% | Best custom / correctness fix |
| E7 | 20.66 | 1.24 | +0.38 | +1.87% | Regression |
| E8 | 20.45 | 1.35 | +0.17 | +0.84% | Regression |
| E9 | 20.45 | 1.35 | +0.17 | +0.84% | Regression |

Ranking by `ATT`:

1. `Original` - `20.28`
2. `E1_3`, `E1_6`, `E6` - `20.37`
3. `E1_4` - `20.41`
4. `E5`, `E8`, `E9` - `20.45`
5. `E1_2` - `20.52`
6. `E7` - `20.66`
7. `E3` - `21.75`
8. `E1` - `21.76`
9. `E1_1`, `E4` - `21.77`
10. `E2` - `21.87`

## 6. Experiment Timeline

### E1

**Hypothesis**  
Minimizing expected sailing time on the current feasible network would improve `ATT`.

**Change**  
Initial custom routing based on expected sailing time using the generic feasible candidate graph.

**Result**  
`ATT = 21.76`, `PeriodStdDev = 1.60`

**Delta vs Original**  
`+1.48` days, `+35.52` hours, `+7.30%`

**Verdict**  
Reject.

**What we learned**  
Pure sailing-time optimization did not outperform Default. The dynamic simulation does not reward this simplification.

### E1_1

**Hypothesis**  
An early variant of the same custom routing idea might fix implementation roughness.

**Change**  
The current source does not expose a distinct branch for `E1_1`; the visible code path is effectively the same family of sailing-time routing logic.

**Result**  
`ATT = 21.77`, `PeriodStdDev = 1.62`

**Delta vs Original**  
`+1.49` days, `+35.76` hours, `+7.35%`

**Verdict**  
Reject.

**What we learned**  
The exact historical distinction from `E1` is not reconstructible with confidence from the current visible source, but the measured result is clearly worse than `Original`.

### E1_2

**Hypothesis**  
Same-route contiguous bookings should be normalized so a shipment does not artificially unload and reload on the same `ServiceRoute`.

**Change**  
Booking-chain normalization before materialization.

**Result**  
`ATT = 20.52`, `PeriodStdDev = 1.30`

**Delta vs Original**  
`+0.24` days, `+5.76` hours, `+1.18%`

**Verdict**  
Diagnostic / correctness.

**What we learned**  
This was a real semantic fix. It improved the custom family by `1.25` days relative to `E1_1`, but still did not beat `Original`.

### E1_3

**Hypothesis**  
Custom routing should use disruption feasibility compatible with Default rather than a broader or mismatched candidate graph.

**Change**  
Aligned candidate graph / disruption feasibility with Default-equivalent semantics, while retaining same-route normalization.

**Result**  
`ATT = 20.37`, `PeriodStdDev = 1.16`

**Delta vs Original**  
`+0.09` days, `+2.16` hours, `+0.44%`

**Verdict**  
Best custom / close.

**What we learned**  
This became the best custom routing baseline because it removed an important semantic mismatch, but it still did not beat `Original`.

### E1_4

**Hypothesis**  
Broad structural tie-breaking would help by preferring fewer route changes and fewer spans.

**Change**  
Added deterministic operational tie-breaking on top of the E1_3-style path search.

**Result**  
`ATT = 20.41`, `PeriodStdDev = 1.13`

**Delta vs Original**  
`+0.13` days, `+3.12` hours, `+0.64%`

**Verdict**  
Reject.

**What we learned**  
The tie-break was too broad. It changed many low-signal cases and did not improve aggregate realized delay.

### E1_5

**Hypothesis**  
`Default`-style path semantics might help if the candidate graph remains disruption-aware.

**Change**  
Used `shortest_booking_path_default_semantics(...)` on the default-equivalent candidate graph.

**Result**  
`ATT = 20.45`, `PeriodStdDev = 1.35`

**Delta vs Original**  
`+0.17` days, `+4.08` hours, `+0.84%`

**Verdict**  
Reject.

**What we learned**  
Switching path semantics without a stronger operational signal did not improve realized performance.

### E1_6

**Hypothesis**  
Dynamic topology / span lookup correctness needed to be preserved when alternative routes appear.

**Change**  
Kept the dynamic topology cache correct while preserving the E1_3 semantic baseline.

**Result**  
`ATT = 20.37`, `PeriodStdDev = 1.16`

**Delta vs Original**  
`+0.09` days, `+2.16` hours, `+0.44%`

**Verdict**  
Best custom / correctness fix.

**What we learned**  
The correctness fix was necessary, but it did not create a new aggregate improvement beyond `E1_3`.

### E2

**Hypothesis**  
Initial service wait should improve route choice by accounting for headway.

**Change**  
Added initial-wait cost, but not transfer cost.

**Result**  
`ATT = 21.87`, `PeriodStdDev = 0.95`

**Delta vs Original**  
`+1.59` days, `+38.16` hours, `+7.84%`

**Verdict**  
Strong regression.

**What we learned**  
Headway-based waiting is too coarse as a universal proxy.

### E3

**Hypothesis**  
Initial wait plus transfer wait would better approximate realized cost.

**Change**  
Enabled both initial waiting and transfer waiting in `_transition_cost`.

**Result**  
`ATT = 21.75`, `PeriodStdDev = 1.16`

**Delta vs Original**  
`+1.47` days, `+35.28` hours, `+7.25%`

**Verdict**  
Strong regression.

**What we learned**  
Even a more realistic waiting model did not beat `Original`; the extra penalty changed rankings without yielding better realized outcomes.

### E4

**Hypothesis**  
Dynamic rerouting on carried shipments could capture in-transit improvement opportunities.

**Change**  
Enabled dynamic rerouting in addition to the E3-style waiting model.

**Result**  
`ATT = 21.77`, `PeriodStdDev = 1.11`

**Delta vs Original**  
`+1.49` days, `+35.76` hours, `+7.35%`

**Verdict**  
Strong regression.

**What we learned**  
Dynamic rerouting added complexity and churn without aggregate benefit.

### E5

**Hypothesis**  
Initial next-vessel waiting at service entry would capture a real congestion effect.

**Change**  
Estimated initial waiting explicitly using route headway-style logic.

**Result**  
`ATT = 21.61`, `PeriodStdDev = 1.04`

**Delta vs Original**  
`+1.33` days, `+31.92` hours, `+6.56%`

**Verdict**  
Regression.

**What we learned**  
The signal was too weak or too noisy to justify the added wait model.

### E6

**Hypothesis**  
Capacity pressure might explain route choice better than sailing time alone.

**Change**  
Added capacity-delay estimation based on queued TEU and nominal route capacity.

**Result**  
`ATT = 20.37`, `PeriodStdDev = 1.16`

**Delta vs Original**  
`+0.09` days, `+2.16` hours, `+0.44%`

**Verdict**  
Best custom / correctness fix.

**What we learned**  
The capacity-pressure model did not beat `Original`. It matched the best routing baseline numerically, but the archived log shows it was the slowest custom run.

### E7

**Hypothesis**  
Narrow direct-service and physical-stop tie-breaking would improve equal-sailing decisions.

**Change**  
Lexicographic tie-break on:

1. minimum sailing time
2. direct single-`ServiceRoute` path preferred
3. fewer physical intermediate port calls

**Result**  
`ATT = 20.66`, `PeriodStdDev = 1.24`

**Delta vs Original**  
`+0.38` days, `+9.12` hours, `+1.87%`

**Verdict**  
Reject.

**What we learned**  
The structural rule was too coarse. It helped some static cases, but the realized system-level effect was worse.

### E8

**Hypothesis**  
The only remaining useful alternative-route lever was vessel reservation choice for dynamically created alternative routes.

**Change**  
Preserved Default alternative-route topology and changed only vessel reservation ranking:

1. immediately switchable empty vessel already at ALT start port
2. otherwise empty eligible vessel with the smallest estimated remaining travel to the ALT start port
3. tie by lowest vessel index

**Result**  
`ATT = 20.45`, `PeriodStdDev = 1.35`

**Delta vs Original**  
`+0.17` days, `+4.08` hours, `+0.84%`

**Verdict**  
Reject.

**What we learned**  
Even the narrow vessel-selection lever for alternative routes was not enough to beat Default.

### E9

**Hypothesis**  
The remaining safe opportunity was same-`ServiceRoute` booking normalization before materialization.

**Change**  
Default-equivalent raw path selection, then contiguous same-route booking normalization before building `Booking` objects.

**Result**  
`ATT = 20.45`, `PeriodStdDev = 1.35`

**Delta vs Original**  
`+0.17` days, `+4.08` hours, `+0.84%`

**Verdict**  
Reject.

**What we learned**  
A semantically safe normalization can still leave the global KPI above `Original`.

## 7. Major Correctness Discoveries

### 7.1 Same-ServiceRoute Booking Fragmentation

Se detectó que bookings contiguos en la misma `ServiceRoute` podían quedar materializados como dos objetos separados, por ejemplo:

```text
Booking A: S3 segment 1 -> 1
Booking B: S3 segment 2 -> 2
```

Aunque el shipment permanecía físicamente sobre la misma ruta, el simulador podía tratar esa frontera como una descarga y recarga operacional:

- unload
- transshipment waiting
- advance `current_booking_index`
- reload

Esto es una diferencia semántica real, no solo una heurística.

Efecto medido:

- `E1_1 = 21.77`
- `E1_2 = 20.52`

Mejora:

- `1.25` días

Conclusión:

La normalización de bookings no fue una micro-optimización; fue una corrección de semántica operacional.

### 7.2 Disruption Candidate-Graph Mismatch

Se observó que el primer motor custom de routing no siempre coincidía con la semántica de factibilidad de Default durante disruptions activas. Al alinear la factibilidad de candidatos con la semántica equivalente a Default, se obtuvo:

- `E1_2 = 20.52`
- `E1_3 = 20.37`

Conclusión:

La reducción de `0.15` días no provenía de “mejor distancia”, sino de corregir el grafo de candidatos para que la comparación fuera justa.

### 7.3 Dynamic Topology Cache

`response_strategies/routing.py` mantiene caches de topología:

- `_TOPOLOGY_CACHE`
- `_SPAN_LOOKUP_CACHE`

El sistema debe seguir reconociendo rutas alternativas creadas dinámicamente, por ejemplo `S2-ALT-1`, sin usar entradas obsoletas. La corrección asociada a este punto quedó preservada en `E1_6`, que obtuvo el mismo `ATT` agregado que `E1_3`:

- `E1_6 = 20.37`

Conclusión:

Fue una corrección necesaria de infraestructura de routing, pero no produjo mejora agregada sobre `E1_3`.

## 8. Why Minimum Sailing Time Did Not Beat Default

La razón estructural principal es la homogeneidad de velocidades de la flota. En `Input/vessel_classes.csv`, todas las clases tienen `SailingSpeedKnots = 20`.

Por tanto, fuera de los multiplicadores de disruption:

```text
expected_sailing_hours = distance / 20
```

En este repositorio, minimizar tiempo esperado de navegación es casi equivalente a minimizar distancia física.

Además, el diagnóstico estático mostró que muchas diferencias entre rutas tenían:

- aproximadamente `0` horas de ventaja real de sailing time

Conclusión:

El problema no tenía suficiente señal de optimización en la parte de navegación para superar al baseline.

## 9. Why Waiting-Time Models Did Not Work

Los experimentos `E2`, `E3`, `E4`, `E5` y `E6` intentaron incorporar esperas o congestión de forma explícita.

Observaciones:

- `E2` añadió espera inicial aproximada y empeoró respecto a `Original`.
- `E3` añadió espera de transferencia además de la inicial, y siguió peor.
- `E4` añadió rerouting dinámico, sin mejora agregada.
- `E5` modeló espera inicial con lógica tipo headway, pero siguió lejos del benchmark.
- `E6` agregó presión de capacidad y terminó empatando con `E1_3`, no con `Original`.

Interpretación:

- `headway/2` es una aproximación demasiado simple para una red con patrones irregulares.
- Los vessels y las cargas reales quedan sujetos a berth service, waiting, disruption timing y disponibilidad local.
- El costo esperado estático no predice de forma fiable el costo realizado en una simulación discreta con eventos y colas.

Sobre rendimiento:

- `Logs/E6.log` registra `Simulation Running Time: 02:41:29`

Ese dato muestra que el modelo más costoso no trajo una mejora proporcional del KPI.

## 10. Tie-Breaking and Structural Routing

`E1_4`, `E1_5` y `E7` exploraron la idea de que la estructura de la ruta elegida importa incluso cuando el costo primario es similar.

Hallazgo:

- La estructura sí importa.
- Pero un tie-break amplio o genérico cambia demasiados casos de bajo señal.

Ejemplo representativo documentado en los diagnósticos previos:

- `Shanghai -> New Jersey`

El patrón observado fue:

- `Original`: servicio directo
- `custom`: ruta con transferencia

Ese tipo de cambio no siempre es malo en abstracto, pero en la simulación completa puede introducir más fricción operacional que la ruta directa evita.

Conclusión:

La estructura importa, pero las reglas estructurales amplias no superaron al baseline.

## 11. Alternative Routes and Vessel Reassignment

El baseline crea rutas alternativas cuando una disruption activa afecta una `ServiceRoute` original. El mecanismo reserva un vessel elegible para esa ruta alternativa.

`E8` aisló precisamente ese punto:

- mantiene la topología alternativa de Default,
- cambia solo la selección del vessel reservado.

Hipótesis:

- elegir primero un vessel ya listo para entrar a la ruta alternativa debería acelerar su activación operativa.

Resultado:

- `E8 = 20.45`

Conclusión:

La asignación del vessel para la alternativa es un lever real, pero su impacto agregado no fue suficiente.

## 12. E9 / Default Normalization Experiment

`E9` implementa:

- selección de rutas igual a Default-equivalent semantics,
- normalización segura de spans contiguos en la misma `ServiceRoute`,
- materialización posterior de bookings ya consolidados.

Resultado:

- `E9 = 20.45`

Interpretación:

La corrección fue localmente razonable, pero no cambió la historia global del sistema lo suficiente para superar a `Original`.

## 13. Performance / Runtime Lessons

Los registros en `Logs/` muestran diferencias importantes de costo computacional entre estrategias:

- `E1`: `00:02:36`
- `E1_3`: `00:04:28`
- `E1_6`: `00:04:25`
- `E4`: `00:08:06`
- `E5`: `00:04:57`
- `E6`: `02:41:29`
- `E7`: `00:04:52`
- `E8`: `00:04:30`
- `E9`: `00:03:36`

Conclusión:

- Las estrategias simples y correctas son prácticas.
- El modelo más costoso no produjo una mejora proporcional.
- La calidad de optimización debe incluir costo computacional, no solo KPI final.

## 14. What Worked

Se consolidaron varios avances útiles:

- normalización de bookings contiguos en la misma ruta,
- alineación correcta de factibilidad bajo disruptions,
- preservación de la caché de topología dinámica,
- separación clara entre experimento y baseline,
- uso disciplinado de `ATT_By_Statistics_Interval.csv`,
- diagnósticos estáticos previos a simulaciones caras.

La distinción clave fue:

- `code correctness improvement` no implica necesariamente `ATT improvement`.

## 15. What Did Not Work

Se descartaron o degradaron los siguientes enfoques:

- reemplazo de Default por routing de sailing-time puro,
- espera inicial tipo `headway/2`,
- costo de transferencia esperado generalizado,
- rerouting dinámico amplio,
- tie-breaking estructural genérico,
- ETA / espera predictiva más pesada,
- presión de capacidad como señal principal,
- preferencia directa universal sobre transferencia,
- cambio de vessel reservation de rutas alternativas.

Motivo común del rechazo:

- produjeron un proxy más complejo,
- pero no redujeron el `ATT` realizado frente a `Original`.

## 16. Final Strategy Ranking

Ranking final por `ATT`:

1. `Original` = `20.28`
2. `E1_3` / `E1_6` / `E6` = `20.37`
3. `E1_4` = `20.41`
4. `E5` / `E8` / `E9` = `20.45`
5. `E1_2` = `20.52`
6. `E7` = `20.66`
7. `E3` = `21.75`
8. `E1` = `21.76`
9. `E1_1` / `E4` = `21.77`
10. `E2` = `21.87`

La parte importante no es solo el orden, sino el hecho de que ninguna variante custom superó al baseline.

## 17. Final Verdict

La recomendación final es:

```text
The Original / Default strategy remains the recommended submission strategy.
```

Razón:

- `Original = 20.28`
- mejor custom = `20.37`
- diferencia = `+0.09` días
- equivalente a `+2.16` horas
- equivalente a aproximadamente `+0.44%`

No hay justificación de ingeniería para reemplazar una estrategia mejor medida por una más compleja sin ventaja observable.

## 18. Recommended Submission Configuration

El repositorio no define un preset explícito llamado `ORIGINAL` en `response_strategies/strategy_parameters.py`.

Para reproducir `Original / DefaultStrategy`, el comportamiento requerido es:

- `select_vessel_for_berth -> None`
- `create_alternative_service_routes -> None`
- `assign_associated_bookings -> None`
- `adjust_bookings_before_cargo_handling -> None`

Eso fuerza el fallback a `DefaultStrategy` en los puntos de decisión relevantes.

Los resultados oficiales de `Original` están en:

- `Output/Original/ATT_By_Statistics_Interval.csv`

## 19. Future Work

Si se continúa la optimización, las líneas más defendibles serían:

- descomposición más fina del delay realizado por shipment,
- instrumentación de espera en origen / transshipment / berth,
- análisis multi-seed para robustez,
- optimización contra demora realizada en lugar de proxies estáticos,
- búsqueda sistemática de parámetros dentro de un espacio muy acotado,
- experimentos de rutas alternativas solo si hay evidencia repetida de mejora.

No parece defendible volver a invertir en:

- routing ETA pesado,
- tie-breaks genéricos amplios,
- rerouting dinámico generalizado,
- modelos de congestión sin señal empírica fuerte.

## 20. Reproducibility

Archivos y puntos relevantes:

- selector de estrategia: [`response_strategies/strategy_parameters.py`](response_strategies/strategy_parameters.py)
- hooks principales: [`response_strategies/user_strategy.py`](response_strategies/user_strategy.py)
- routing base: [`response_strategies/default_strategy.py`](response_strategies/default_strategy.py)
- motor de rutas: [`response_strategies/routing.py`](response_strategies/routing.py)
- wait / capacity models: [`response_strategies/expected_time.py`](response_strategies/expected_time.py)
- rerouting dinámico: [`response_strategies/dynamic_rerouting.py`](response_strategies/dynamic_rerouting.py)
- resultados por estrategia: `Output/<strategy>/ATT_By_Statistics_Interval.csv`
- logs de ejecución: `Logs/<strategy>.log`

Para comparar resultados:

- usar la fila `OverallMean` del CSV de cada estrategia,
- usar `PeriodStdDev` como medida de estabilidad,
- evitar confundir esas filas con los intervalos de 5 días que aparecen antes en el mismo archivo.

### Nota sobre reconstrucción exacta

La semántica visible de `E1_1` no está separada de forma clara en el código actual respecto de `E1`; por eso esa diferencia histórica no puede reconstruirse con total confianza a partir de la fuente presente. El resultado medido sí está verificado en `Output/`.
