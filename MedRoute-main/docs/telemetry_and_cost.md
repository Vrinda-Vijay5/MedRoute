# Telemetry and route-cost contract

## Collection and freshness

The controller polls port counters, port descriptions, flow counters and echo
RTT every 2 seconds. Ryu topology LLDP provides link observations. A link sample
is fresh for 10 seconds; the oldest contributing timestamp determines its age.
Missing or stale contributors prevent measured MedRoute evaluation rather than
being replaced with dummy values.

| Value | Unit | Provenance | Interpretation |
|---|---|---|---|
| latency | ms | LLDP elapsed time minus half both switch echo RTTs | link estimate |
| jitter | ms | EMA of absolute corrected-latency change | derived link variation |
| loss | % | source TX packet-rate minus paired destination RX rate | directional estimate |
| carried throughput | bit/s | min(source TX, destination RX byte rates) | traffic carried, not capacity |
| utilization | % | source TX rate / capacity | derived load |
| residual capacity | bit/s | capacity - source TX rate | estimate, not an active bandwidth probe |
| active-flow demand | bit/s | ingress MedRoute rule byte delta / time | per-flow carried demand estimate |
| receiver goodput | bit/s | iperf3 receiver UDP summary | experiment outcome |

Counter windows more than 3 seconds apart are rejected for paired calculations.
Negative deltas are treated as counter resets and require a new interval. Zero
delta is valid idle-link data. Configured testbed capacity takes precedence over
OVS port-description speed; every demo/matrix runner sets the capacity equal to
its Mininet inter-switch `bw` configuration.

## Path aggregation and normalization

- latency: sum;
- jitter: root-sum-square;
- loss: composed delivery probability;
- utilization: maximum link value;
- carried throughput, residual capacity and capacity: bottleneck minimum.

Normalization references are 100 ms latency, 50 ms jitter, 5% loss, 100%
utilization and a throughput adequacy ratio of one. Values are clipped to
`[0,1]`. The throughput term is `requested_or_measured_demand / residual`.
This deliberately does not interpret high carried throughput as high available
capacity.

Profile weights are defined in `traffic_classifier.py` and sum to one. They are
configuration/policy, not learned or clinically validated parameters. Unit
tests independently vary latency, jitter, loss, utilization and throughput
adequacy to confirm that each can change route ranking.

## Failure behavior

During warm-up or staleness the controller retains the current valid route, or
uses a deterministic shortest path if none exists. It logs and stores the
fallback reason. The safety gate does not run with an invented network state.
