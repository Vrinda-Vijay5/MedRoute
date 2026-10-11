# Braess model and limits of the current Mininet experiment

## Analytical model

The reference is the classical four-node network `S-A-T` / `S-B-T` with a
candidate connector `A-B`. Each outer route has one load-sensitive edge and
one fixed-cost edge. The connector has cost `epsilon`. Total nonatomic demand
is `D`, and each load-sensitive edge has cost

```text
g(x) = x / C
```

where `x` is the rate on that edge and `C` is a reference capacity. Costs here
are dimensionless model units; they are not milliseconds or MedRoute's
weighted QoS score.

With the connector absent, symmetric Wardrop equilibrium divides demand in
half. Each used route costs

```text
before = g(D/2) + L = D/(2C) + L
```

With the connector present, the all-connector route costs `2g(D)+epsilon`.
A unilateral infinitesimal deviation to an outer route costs `g(D)+L`, so the
all-connector state is an equilibrium when

```text
D/C + epsilon <= L
```

That state is worse than the no-connector equilibrium when

```text
2D/C + epsilon > D/(2C) + L
```

Thus a Braess interval exists when

```text
D/C + epsilon <= L < 3D/(2C) + epsilon
```

For a concrete analytic example, take `D=C=100 Mbit/s`, `epsilon=0`, and
`L=1` model unit. The no-connector cost is `1.5`; the connector equilibrium
cost is `2.0`, a derived increase of `0.5` model unit (33.3%). The theoretical
inequality is not an experimental result.

## Mapping and why the existing controller cannot claim this model

The five existing imaging senders offer `D=5*20=100 Mbit/s`, so the example's
reference `C=100 Mbit/s` is arithmetically traceable to declared demand. A
Mininet `bw=100` link could represent that capacity. A fixed `L` would need to
be a separable additive link cost, and each congestible edge would need to
exhibit the specified `x/C` cost as load changes. The current emulator does
not enforce that law: `TCLink` shapes bandwidth and its finite qdisc creates
load-dependent delay/loss according to Linux queue behavior, not the model's
linear function. Setting a propagation delay would add a constant; it would
not make delay proportional to traffic rate. The controller also computes
route cost from class-weighted normalized metrics and uses the maximum edge
utilization as a path metric, rather than summing separable `g(x)` edge costs.
It routes individual flows from measured state; it does not solve or enforce a
Wardrop equilibrium.

The dedicated trial currently retains its reproducible 52 Mbit/s link setting,
12 ms outer-link delay, 0.5 ms short-link delay, 2 Mbit/s protected ECG stream,
and five 20 Mbit/s imaging streams. The capacity is sent consistently to the
controller and written into raw evidence. It is a previously selected
experimental configuration, not the `C=100` analytical example. Changing it
to 100 Mbit/s alone would not fix the missing cost-law/equilibrium mapping;
changing delay or rate until an inequality appears would be result tuning.

Consequently, the present dedicated run is a real measured candidate-link
stress comparison, not a faithful realization of the classical equilibrium
model. Its primary empirical criterion remains the actual selected-path ECG
probe RTT:

```text
after_unprotected_latency > before_latency
```

Only then is latency degradation demonstrated. MedRoute avoidance is a
separate empirical claim and additionally requires
`after_medroute_latency < after_unprotected_latency`, with the matching
controller log showing the risky candidate rejected and a safe path selected.
The validator's projected `BRAESS_RISK` is evidence of its safety model
decision; it is not evidence that the later experiment measured a paradox.

## What would be needed for a theory-matched follow-up

Do not infer this from the existing results. A separate design would need an
explicit, reproducible load-dependent edge-cost mechanism calibrated before
the experiment, a routing/traffic-assignment process whose equilibrium
assumptions are stated, and measurements that verify its edge cost versus
offered load. That is a larger algorithm/testbed change. Until then, report
the mathematical model as a reference and the current experiment only as an
empirical stress test with its observed outcome, whether positive or negative.
