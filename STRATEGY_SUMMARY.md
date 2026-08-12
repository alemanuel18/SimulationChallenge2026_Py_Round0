# Strategy Summary

Resumen compacto de los experimentos de estrategia del repositorio, validado contra [`STRATEGY_EXPERIMENT_REPORT.md`](STRATEGY_EXPERIMENT_REPORT.md).

## Comparación de pruebas

| Prueba | Estrategia utilizada | Idea principal | ATT |
|---|---|---|---:|
| Original | DefaultStrategy | Shortest path por distancia + mecanismos Default de disruptions | 20.28 |
| E1 | Minimum Sailing Time | Cambiar distancia por tiempo esperado de navegación | 21.76 |
| E1_1 | E1 / control | Variante de control de E1; buscaba aislar mejor el cambio de routing | 21.77 |
| E1_2 | E1 + Booking normalization | Fusionar bookings consecutivos de la misma ServiceRoute para evitar transbordos artificiales | 20.52 |
| E1_3 | E1_2 + disruption feasibility | Hacer que el grafo custom respete las mismas restricciones de disruption que Default | 20.37 |
| E1_4 | Structural tie-break | En empates, favorecer menos cambios de ruta y menos spans | 20.41 |
| E1_5 | Default-like tie-break | Reproducir mejor la semántica y el desempate de caminos de Default | 20.45 |
| E1_6 | E1_3 + dynamic cache fix | Corregir la cache para detectar rutas alternativas creadas dinámicamente | 20.37 |
| E2 | Sailing + initial wait | Añadir espera inicial estimada según frecuencia/headway del servicio | 21.87 |
| E3 | Sailing + initial + transfer wait | Añadir también el costo esperado de esperar durante transbordos | 21.75 |
| E4 | E3 + dynamic rerouting | Replanificar shipments durante disruptions cuando otra ruta parecía suficientemente mejor | 21.77 |
| E5 | Improved initial waiting | Intentar representar mejor la espera para abordar el primer servicio | 21.61 |
| E6 | Capacity/state-aware | Incorporar información dinámica adicional de presión/capacidad; fue extremadamente costoso | 20.37 |
| E7 | Direct-service tie-break | En empate de sailing time, preferir servicio directo y luego menos paradas físicas | 20.66 |
| E8 | Better ALT vessel selection | Mantener Default pero escoger mejor qué vessel reservar para una ruta alternativa | 20.45 |
| E9 | Default + booking normalization | Mantener selección de rutas de Default y fusionar bookings contiguos de la misma ruta | 20.45 |

## Qué buscaba cada familia

### E1 a E1_6: routing por tiempo y correcciones semánticas

La familia `E1` intentó reemplazar la distancia por tiempo esperado de navegación. La idea era razonable, pero el reporte confirma que la flota usa velocidad homogénea de 20 knots, así que `distance / 20` hace que tiempo y distancia sean casi equivalentes salvo por disruptions.

Lo más importante fue `E1_2`. Se detectó que el router podía dejar bookings contiguos de la misma `ServiceRoute` como objetos separados, lo que el simulador podía interpretar como descarga, espera y recarga artificial. La normalización de esos spans produjo la mayor mejora de esta familia:

- `E1_1 = 21.77`
- `E1_2 = 20.52`

Después, `E1_3` alineó la factibilidad de disruptions con la semántica de `DefaultStrategy`, y bajó a `20.37`. `E1_6` corrigió la cache dinámica de topología sin cambiar ese resultado agregado.

### E2 a E6: intentar modelar mejor la espera

Estas pruebas trataron de aproximar mejor el costo total sumando espera inicial, espera en transbordos, rerouting dinámico y señales de capacidad.

El patrón fue consistente:

- `E2` empeoró.
- `E3` siguió peor.
- `E4` añadió complejidad sin beneficio.
- `E5` tampoco superó al baseline.
- `E6` empató con el mejor custom, pero con un costo computacional muy alto.

La conclusión del reporte es que los estimadores estáticos de espera, como `headway/2`, no predicen bien el costo realizado en una simulación con colas, berths, handling y disruptions.

### E7: desempate estructural

`E7` probó reglas más estrictas para desempatar:

1. menor sailing time
2. preferir ruta directa
3. luego menos paradas físicas

La idea tenía sentido operacional, pero en la simulación completa alteró demasiado el equilibrio global y terminó con peor `ATT`.

### E8: mejor selección de vessel para rutas alternativas

`E8` mantuvo la topología alternativa del `DefaultStrategy` y modificó solo qué vessel se reserva para la ruta alternativa. El criterio priorizaba un vessel vacío y cercano al punto de inicio de la alternativa.

Fue una corrección local razonable, pero no suficiente para superar al baseline.

### E9: normalización segura sobre Default

`E9` volvió a una selección equivalente a `Default` y solo aplicó la normalización de bookings contiguos de la misma `ServiceRoute`.

El resultado fue el mismo que `E8`:

- `E9 = 20.45`

## Conclusión

La referencia sigue siendo `Original / DefaultStrategy`:

- `Original = 20.28`
- mejor custom = `20.37`
- diferencia = `+0.09` días
- equivalente a `+2.16` horas
- equivalente a `+0.44%`

La lectura final del reporte es simple: hubo mejoras de corrección y de modelado, pero ninguna estrategia custom superó al baseline.
