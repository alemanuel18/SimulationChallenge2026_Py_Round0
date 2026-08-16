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
