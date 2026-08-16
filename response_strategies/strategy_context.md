# Strategy Context

This document captures the problem framing, the initial strategy, the experimental results, and the reasoning that led to the current modular response-strategy design.

## Problem statement

The simulation models a maritime container transport network with fixed vessels, fixed service routes, fixed ports, stochastic shipment generation, berth constraints, transshipment behavior, and time-limited disruptions.

The real objective is not simply to move ships faster. The goal is to reduce the total time shipments spend in the system, which feeds `AverageTransportTime` and therefore `OverallMean`.

That total time includes:

- waiting at origin ports,
- waiting for berth access,
- sailing time,
- transshipment waiting,
- cargo handling delay,
- and queue spillover caused by congestion or disruptions.

A faster sailing option is not automatically better if it creates a longer queue at the destination port or adds extra transshipment delay.

## Initial idea

The original proposal combined three ideas:

1. Use critical-path style logic to choose better routes.
2. Reduce effective vessel pressure by avoiding unnecessary deployment pressure.
3. Use alternative routes as a pressure valve during peaks, either to accelerate or delay arrivals so vessels reach ports when berths are likely to be free.

The intuition was correct: if a vessel arrives too early to a congested port, faster arrival can be worse than controlled timing.

## What the simulation actually allows

The strategy layer does not control vessel creation. The fleet is fixed by the scenario.

The useful control points are:

- berth selection at congested ports,
- initial booking assignment,
- alternative route creation,
- booking replanning before cargo handling.

That means the practical problem is not to create fewer vessels, but to control how demand is assigned and redirected so the network does not build unnecessary queues.

## Results from the first experiment

Several aggressive variants were tested.

What happened:

- A global expected-time rerouting policy made the full result worse.
- A local period minimum reached about `18.38` days, but that was only a single period, not the whole run.
- The full experiment ended with `OverallMean = 25.2396`.

Reference point:

- The project CSV reference had `OverallMean = 20.28`.

So the aggressive strategy improved some periods, but the global average got worse.

## What likely went wrong

The first strategy was too interventionist.

It likely failed because it:

- rerouted too many shipments,
- replanned too often,
- changed the default routing even when the network was not under real pressure,
- and added extra transshipment or queue effects that outweighed the local gains.

In other words, it optimized local travel decisions but did not protect the network against backlog growth.

## Decision for the new version

The next version must be conservative and selective.

The new direction is:

1. Keep default routing unless there is a clear operational reason to intervene.
2. Treat critical-path logic as a detector of opportunities, not as a reason to reroute everything.
3. Prioritize berth decisions using:
   - waiting pressure,
   - carried TEU,
   - unloading relevance,
   - downstream congestion,
   - and handling workload.
4. Use alternative routes only during real peak pressure or disruption windows.
5. Replan carried shipments only when expected savings are clearly positive.
6. Avoid adding transshipment complexity just to reduce sailing distance.

## Priority of unloading

One important improvement to keep in mind is explicit unloading priority.

It is not enough to choose the vessel with the most waiting time. The strategy should also consider whether unloading that vessel will:

- release blocked TEU,
- reduce storage pressure at the port,
- unblock transshipment chains,
- and free berth capacity for the next arrivals.

That makes unloading a real operational lever, not just a side effect.

## Current modular design

The current implementation is split so it can be tuned later without rewriting logic:

- `response_strategies/user_strategy.py` is the entry point.
- `response_strategies/optimized_strategy.py` contains the decision policy.
- `response_strategies/routing_utils.py` contains routing, disruption, and replanning helpers.
- `response_strategies/strategy_parameters.py` centralizes weights, thresholds, and feature switches.

The idea is to make future parameter sweeps easy through environment variables while keeping the default behavior safe.

## Practical takeaway

The strategy should not try to solve everything at once.

The best next version should focus on:

- berth priority as the main lever,
- unloading priority as an explicit factor,
- selective alternative routes,
- and replanning only when the net time saving is clear.

That is the version most likely to reduce the real average, rather than only lowering a few local periods.

## Current correction

The current implemented correction keeps routing and replanning disabled by default and activates only a conservative berth-priority override.

The custom berth priority now:

- starts from the default berth decision,
- scores only vessels in the congested berth queue,
- gives priority to TEU that will be discharged at the current port,
- separates final-destination discharge from transshipment discharge,
- considers the age of unloading cargo,
- penalizes high handling workload,
- and only overrides the default if the score gain and unloading gain are both clear.

This avoids the previous failure mode where the strategy changed too many routing decisions and accumulated backlog later in the run.

Partial validation after this correction:

- first 120 measured days with the corrected strategy: `19.2608`,
- first 120 measured days in the reference CSV: `19.9196`.

This is not a full-run proof yet, but it shows that the corrected conservative berth-priority strategy improves the early measurement window instead of immediately degrading the system.

## Port-aware correction

The next observed output pattern showed that the most pressured ports were not all failing in the same way:

- high waiting cargo: `Shanghai`, `Singapore`, `Busan`,
- high waiting vessels: `Kaohsiung`, `Jakarta`, `Ho Chi Minh City`, `Busan`.

The strategy now applies different berth-priority behavior by port group:

- cargo ports prioritize vessels that can load more TEU out of the port,
- vessel-queue ports prioritize quick turnaround to drain berth queues,
- `Busan` uses a hybrid rule because it appears in both pressure groups,
- other ports keep the conservative unloading-focused rule.

Partial validation after this port-aware correction:

- first 120 measured days: `19.2583`,
- previous corrected first 120 measured days: `19.2608`,
- reference CSV first 120 measured days: `19.9196`.

This still needs a full 360-day measurement, but it confirms that the port-aware rule does not damage the early window and is slightly better in the partial test.

Full-run feedback after the port-aware test showed the opposite pattern:

- the first 19 periods stayed acceptable,
- from period 20 onward the strategy increased the average,
- the full `OverallMean` worsened to `20.89`.

The current mitigation limits custom berth priority to the first 95 measured days and then falls back to the default strategy for the rest of the run. This keeps the part of the strategy that did not show immediate damage while avoiding the period where it starts amplifying backlog.

Follow-up result:

- limiting the custom berth priority to the early window did not produce a useful improvement,
- the strategy still failed to reduce the full-run average,
- and the port-aware berth-priority approach should not be treated as the main path forward.

Current conclusion:

- berth-priority-only interventions are too weak or too late to fix the main bottleneck,
- targeting only visible waiting ports can shift congestion instead of reducing system-wide transport time,
- and the next version should move away from berth priority as the central lever.

The next promising direction is to attack demand assignment before backlog forms. That means testing a stricter, low-risk booking policy focused on:

- reducing future load into known bottleneck ports,
- avoiding unnecessary transshipment into `Shanghai`, `Singapore`, and `Busan`,
- avoiding flows that feed vessel-waiting bottlenecks like `Kaohsiung`, `Jakarta`, and `Ho Chi Minh City`,
- and only rerouting OD pairs whose default path crosses those pressure points during the problematic middle window.
