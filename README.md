# MedRoute

**A Braess-Paradox-Aware SDN Framework for Prioritized Routing of
Life-Critical Healthcare Traffic**

MedRoute is a B.Tech Computer Networks project that compares ordinary
fewest-hop routing with measured, healthcare-aware Software Defined
Networking. The controller discovers an OpenFlow topology, measures link
state, evaluates candidate paths with normalized QoS cost, checks proposed
changes for Braess-type degradation, and installs the best safe path.

The revised scope deliberately excludes AI, machine learning, prediction, and
game theory. Those items appeared in an older Review-1 plan but are not part of
the implementation requested by faculty. All important routing behavior is
deterministic and SDN-based.

## Current evidence status

The pure-Python implementation has a component test suite covering healthcare,
monitoring, QoS, routing, Braess fallback/resumption, and persistence. Mininet,
Open vSwitch, Ryu, and `iperf3` require Linux. See [RESULTS.md](RESULTS.md) and
the original-state record in [AUDIT.md](AUDIT.md).

## Problem statement

Fewest-hop routing treats an ECG alert and a medical image transfer alike. It
also assumes an attractive path remains beneficial after new traffic moves to
it. In a congested network, that decision can damage an existing critical flow
or increase aggregate cost, a Braess-type outcome. MedRoute gives an SDN
controller enough measured context to reject such a route before deployment.

## Architecture

```text
Synthetic/CSV HealthcareDataSource  (future: wearable adapter)
                    |
                    v
      traffic type + transparent criticality
                    |
                    v
       Ryu controller / topology discovery
                    |
       OpenFlow counters + LLDP + echo timing
                    |
                    v
 latency, jitter, loss, utilization, throughput, bandwidth
                    |
                    v
  normalization -> candidate paths -> weighted QoS cost
                    |
                    v
     projected before/after Braess safety validation
                    |
                    v
 best safe path -> OpenFlow 1.3 rules -> continued monitoring
                    |
                    v
        SQLite evidence + raw ping/iperf JSON + plots
```

The routing algorithm receives generic healthcare metadata. It does not read a
CSV directly, so a future live adapter can implement `HealthcareDataSource`
without modifying monitoring, cost, Braess, or OpenFlow code. No patient data
is required or stored.

## Implemented modules

| Module | Responsibility |
|---|---|
| `healthcare_data.py` | Common data-source interface, deterministic synthetic source, and non-sensitive CSV adapter. |
| `traffic_classifier.py` | Six traffic profiles, UDP port mapping, priority, rationale, and QoS weights. |
| `network_monitor.py` | OpenFlow counter deltas and corrected LLDP timing; no random measurements. |
| `qos_metrics.py` | Path aggregation, fixed-reference normalization, and weighted cost. |
| `routing_engine.py` | Fewest-hop baseline, QoS mode, full MedRoute mode, candidates, hysteresis, cooldown. |
| `braess_validator.py` | Deterministic measured-state projection and safety decision. |
| `medroute_controller.py` | Ryu/OpenFlow integration, loop-safe ARP/access-port discovery, periodic monitoring, rule installation, reevaluation. |
| `storage.py` | Lightweight SQLite evidence store. |
| `hospital_topology.py` | Four-switch diamond plus healthcare and background hosts. |
| `braess_topology.py` | Separate candidate-central-link topology for empirical Braess trials. |
| `experiment_runner.py` | Real ping/iperf measurement and raw/SQLite persistence. |
| `analysis/generate_plots.py` | Plots only existing experiment rows; refuses empty data. |

## Healthcare criticality

The 1–5 scale remains simple enough to explain in a viva. Criticality changes
the QoS weights, the protected-flow degradation limit, and the OpenFlow rule
priority. OpenFlow priority resolves matching rules; this project does not yet
claim queue-level bandwidth guarantees.

| Type / UDP port | Criticality | Latency | Jitter | Loss | Utilization | Reason |
|---|---:|---:|---:|---:|---:|---|
| ECG / 5001 | 5 | 0.35 | 0.30 | 0.25 | 0.10 | A continuous life-critical waveform loses value when samples arrive late or disappear. |
| ICU / 5002 | 5 | 0.35 | 0.25 | 0.30 | 0.10 | Bedside alarms require low delay and very reliable delivery. |
| Telemedicine / 5003 | 4 | 0.35 | 0.35 | 0.15 | 0.15 | Interactive audio/video is especially sensitive to delay variation. |
| EHR / 5004 | 4 | 0.20 | 0.10 | 0.35 | 0.35 | Clinical records require reliable delivery and can tolerate modest jitter. |
| Imaging / 5005 | 3 | 0.15 | 0.05 | 0.20 | 0.60 | Bulk images benefit primarily from available capacity. |
| CCTV / 5006 | 1 | 0.20 | 0.25 | 0.15 | 0.40 | Routine surveillance yields capacity to clinical traffic. |

Every row sums to 1.0. These documented application profiles are configuration,
not learned parameters.

## Network measurements

The controller polls every 2 seconds and treats samples older than 10 seconds
as stale. It reevaluates managed flows every 6 seconds.

| Metric | Unit | Source and calculation |
|---|---|---|
| Link latency | ms | Ryu LLDP observation minus half of each endpoint switch's OpenFlow echo RTT. This is a link estimate, not an application RTT. |
| Jitter | ms | Exponentially smoothed absolute change between consecutive corrected link-latency samples. |
| Packet loss | % | Positive difference between the source-port transmitted-packet rate and paired destination-port received-packet rate, divided by transmitted rate. |
| Throughput | bit/s | Minimum of paired source TX and destination RX byte-counter rates. |
| Utilization | % | Source TX bit rate divided by configured/observed link capacity. |
| Available bandwidth | bit/s | Link capacity minus source TX bit rate. |
| End-to-end experiment latency | ms | Average RTT parsed from Linux `ping`. |
| End-to-end experiment jitter/loss/throughput | ms, %, bit/s | Receiver values from `iperf3 -J` UDP output. |

Delay and latency refer to the same link-delay estimate in the controller and
are counted once. OpenFlow reply timing alone is not presented as data-plane
latency. Counter-based directional loss can be noisy when replies are not
perfectly synchronized; repeated experiment-level `iperf3` results provide the
end-to-end check.

Until all four required measurements exist for every edge in a candidate path,
QoS/MedRoute mode retains the current route or uses the fewest-hop baseline.
It never fills missing metrics with random values.

Full MedRoute mode also requires a complete, fresh metric sample for every
discovered switch-to-switch topology edge before it enters network-wide Braess
validation. During startup, OpenFlow rate calculation naturally needs two
counter observations, both endpoint echo RTTs must arrive, and two corrected
LLDP observations are needed before jitter is measurable. The controller does
not wait for an arbitrary fixed duration: it logs `TELEMETRY WARMUP/FALLBACK`,
retains the installed path or chooses the deterministic shortest path, and
checks readiness again on subsequent packets and periodic reevaluation. Once
all topology-edge samples are complete, logs change to
`MEDROUTE QOS/BRAESS DECISION` and normal measured evaluation resumes.

The monitor logs readiness when it changes and at most once every 10 seconds
while unchanged. A warm-up line names each directed edge and its missing or
stale contributor, for example `source_port_rate_missing_second_sample`,
`source_echo_rtt_missing`, or `corrected_lldp_latency_missing`. A successful
transition is explicit:

```text
TELEMETRY STATUS READY complete=8/8; measured QoS/Braess routing enabled
MEDROUTE QOS/BRAESS DECISION ...
```

For Ryu 4.34, the controller obtains the LLDP send timestamp by matching the
DPID and port number against the topology service's existing `PortDataState`
keys. It does not construct a `ryu.topology.switches.Port` from identifiers;
that class requires the OpenFlow protocol and full OFPPort objects.

## Path metrics and normalization

For path \(P\) with links \(e\):

```text
latency(P)    = sum latency(e)
jitter(P)     = sqrt(sum jitter(e)^2)
loss(P)       = 100 * [1 - product(1 - loss(e)/100)]
utilization(P)= max utilization(e)                 (bottleneck)
throughput(P) = min positive measured throughput(e)
bandwidth(P)  = min available bandwidth(e)
```

Fixed engineering reference limits make costs comparable between candidates
and between experiment times:

```text
N_latency     = clip(latency_ms / 100 ms, 0, 1)
N_jitter      = clip(jitter_ms / 50 ms, 0, 1)
N_loss        = clip(packet_loss_percent / 5 percent, 0, 1)
N_utilization = clip(utilization_percent / 100 percent, 0, 1)
```

Fixed limits avoid a weakness of candidate min-max normalization: adding or
removing an unrelated candidate would otherwise change every existing cost.
Clipping keeps one severe unit from becoming numerically unbounded.

## Exact QoS cost

For traffic class \(T\):

```text
Cost(P,T) = w_latency(T)     * N_latency(P)
          + w_jitter(T)      * N_jitter(P)
          + w_loss(T)        * N_loss(P)
          + w_utilization(T) * N_utilization(P)
```

The controller logs and stores each path, raw aggregate metrics, normalized
values, weights, and final cost. `RoutingEngine.format_evaluation` produces a
faculty-readable line directly from the evaluation object.

## Candidate routing and stability

- `baseline`: NetworkX fewest-hop path. It remains available for comparison.
- `qos`: lowest complete measured cost for the healthcare profile.
- `medroute`: lowest-cost candidate that passes Braess validation.
- Up to six loop-free paths are generated in increasing hop order.
- A new route must improve cost by at least 5%.
- A 10-second cooldown prevents repeated changes. Monitoring continues during
  the cooldown.

Pseudocode:

```text
learn source and destination attachment switches
generate loop-free candidate paths
if baseline: select fewest-hop path
for candidate:
    require fresh measured link metrics
    aggregate and normalize path metrics
    calculate traffic-profile cost
    if medroute: project candidate state and validate safety
select minimum-cost safe candidate
apply 5% hysteresis and 10-second cooldown
install OpenFlow rule on every switch in selected path
store evaluations and decision
repeat when monitoring interval indicates a material alternative
```

## Braess-aware pre-deployment validation

This project uses “Braess risk” in a precise operational sense: an apparently
attractive route is unsafe when moving the flow to it is projected to damage a
protected flow or aggregate measured QoS beyond documented limits.

For flow rate \(r\) and link capacity \(C_e\), the validator first removes the
flow load from edges unique to its old path and adds it to edges unique to the
candidate:

```text
u'_e = clip(u_e + 100*r/C_e on new-only edges
                  - 100*r/C_e on old-only edges, 0, 100)
```

It retains the measured base delay and uses a standard utilization-sensitive
queueing approximation for the proposed state:

```text
d'_e = d_e * (1 - u_e/100) / max(0.01, 1 - min(u'_e,99)/100)
j'_e = j_e * max(1, the same queueing ratio)
l'_e = max(measured_loss_e, 0.25 * max(0, u'_e - 80))
```

The approximation is deterministic and inspectable. It is a pre-deployment
estimate, not a claim that predicted values are measurements. The testbed
experiment must compare them with subsequent ping/iperf values.

A candidate is `BRAESS_RISK` when any condition holds:

1. projected link utilization exceeds 95%;
2. an active criticality-5 flow cost rises more than 5%, or a criticality-4
   flow cost rises more than 10%;
3. the route benefits the changing flow while equal-weight aggregate link QoS
   cost rises more than 8%.

Otherwise it is `SAFE`. If all measured candidates are unsafe, MedRoute keeps
the current route. Initial routes still receive capacity and protected-flow
checks. The separate `braess_topology.py` uses two symmetric outer branches and
a low-delay central link. Its inter-switch links are shaped at 100 Mbit/s, and
the dedicated script supplies that configured capacity to the monitor instead
of accepting an unrelated virtual-interface speed advertised by OVS. Whether a
run demonstrates degradation must still be decided from stored measurements.

## Storage schema

`results/medroute.db` is SQLite and is ignored by Git because it is generated
evidence. The schema contains:

- `flows`: ID, first/last seen, type, criticality, endpoints, expected rate;
- `network_metrics`: timestamp, directed link, all measured metrics and source;
- `path_evaluations`: path, raw/normalized metrics, weights, cost, Braess result;
- `routing_decisions`: selected/previous paths, mode, reason, changed flag;
- `experiments`: scenario/mode/type and real end-to-end outcomes.

Raw `ping` text and full `iperf3` JSON are kept under `results/raw`. This lets a
reviewer audit every plotted value.

## Clean setup on this Windows laptop

Native Windows cannot run Mininet namespaces or the Open vSwitch kernel setup.
Use WSL2 with Ubuntu, or the official Mininet VM. In an Administrator
PowerShell terminal:

```powershell
wsl --install -d Ubuntu-22.04
```

Restart if Windows requests it. Then open Ubuntu, move or clone this repository
inside the Linux filesystem (for example `~/MedRoute`), and run:

```bash
cd ~/MedRoute
bash scripts/setup_ubuntu.sh
```

The script installs Mininet, Open vSwitch, `iperf3`, compiler prerequisites,
creates `.venv` with system packages visible, installs Python dependencies,
runs unit tests, checks Ryu, and performs Mininet's `pingall` smoke test.

Ryu 4.34 is retained because this is an existing Ryu project. On Python 3.10,
Eventlet 0.30.2 fails while modifying the immutable built-in `TimeoutError`,
while Eventlet 0.33.3 no longer exports the private
`eventlet.wsgi.ALREADY_HANDLED` symbol imported by the Ryu 4.34 release.
`scripts/patch_ryu_434_eventlet.py` applies the narrow compatibility block from
Ryu's upstream `wsgi.py`: modern Eventlet uses `getattr(..., None)`. The script
patches only an exact Ryu 4.34 source block, is safe to rerun, preserves a
backup, and refuses an unknown Ryu version or file layout. The reproducible
Python 3.10 combination is Eventlet 0.33.3 plus dnspython 2.2.1.

Upstream now marks Ryu unmaintained. If `ryu-manager --version` still fails
after the patch, stop and record the complete traceback rather than continuing
to the Mininet demo or changing more dependency versions at random.

The working native-Windows interpreter can run dependency-light tests with:

```powershell
& 'C:\Users\VICTUS\AppData\Local\Programs\Python\Python311\python.exe' -m unittest discover -s tests -v
```

The local `.venv` exists, but dependency download was blocked in this session.

## Faculty demo

After Linux setup, the shortest complete sequence is:

```bash
cd ~/MedRoute
source .venv/bin/activate
MEDROUTE_MODE=medroute bash scripts/run_demo.sh congestion
```

The script starts Ryu with link discovery and opens the Mininet CLI. At the
`mininet>` prompt:

```text
pingall
h2 iperf3 -s -1 -p 5001 &
h1 iperf3 -c 10.0.0.2 -u -b 2M -t 15 -p 5001
bg2 iperf3 -s -1 -p 6001 &
bg1 iperf3 -c 10.0.0.4 -u -b 85M -t 30 -p 6001 &
h2 iperf3 -s -1 -p 5001 &
h1 iperf3 -c 10.0.0.2 -u -b 2M -t 15 -p 5001
sh ovs-ofctl -O OpenFlow13 dump-flows s1
exit
```

The controller log `results/controller-demo.log` should show switch/link
discovery, traffic type and criticality, candidate raw and normalized metrics,
cost, Braess `SAFE`/`BRAESS_RISK`, decision reason, and installed ports. Query
stored evidence with:

```bash
sqlite3 results/medroute.db '.tables'
sqlite3 -header -column results/medroute.db \
  'select timestamp,flow_id,path_json,qos_cost,braess_status,safe from path_evaluations order by id desc limit 10;'
```

## Experimental evaluation

Run the complete baseline/QoS/MedRoute matrix only after the smoke test:

```bash
bash scripts/run_experiment_matrix.sh
```

It executes normal, congestion, loss, delay, and jitter scenarios for all
three modes. Each failed command stops the matrix. It then generates plots from
stored rows. Repeat trials (at least 10 per cell for a report), preserve raw
files, report mean/median and dispersion, and add the Braess before/after trial
only after validating the candidate-link topology on the actual laptop.

The current runner uses ECG foreground traffic. Repeat with ICU,
TELEMEDICINE, EHR, IMAGING, and CCTV as needed by changing `--traffic-type`.
Do not compare results produced with different background rates or topology
parameters without recording those differences.

Run the separate before/after/safety-gate sequence with:

```bash
bash scripts/run_braess_experiment.sh
```

It collects three independent loaded trials: candidate link down with QoS,
candidate link up with QoS, and candidate link up with full MedRoute. Each
trial establishes a continuous ECG flow first, then starts five simultaneous
20 Mbit/s IMAGING flows. Together the imaging flows fit one outer branch; the
central link appears attractive because it joins the low-delay edges, but using
it also places their aggregate on the ECG branch. Inspect the printed rows, raw
JSON, and matching controller logs. Report a Braess-type
degradation only if the unprotected after trial is actually worse than the
before trial; report successful avoidance only if the MedRoute log records the
harmful candidate as `BRAESS_RISK` and the measured protected outcome improves.

## Testing

```bash
python -m compileall -q -f .
python -m unittest discover -s tests -v
```

Tests cover classification, priorities, data adapters, topology-independent
candidate generation, metric aggregation, normalization, weighted cost,
Braess safe/risk comparison, safe path selection, missing-metric fallback,
hysteresis, OpenFlow counter math, Ryu LLDP timestamp lookup, zero-rate link
readiness, topology/metric key consistency, stale-sample rejection, warm-up to
measured-routing transition, and persistence. Ryu event dispatch and actual
switch rule behavior require the Linux integration run.

## Limitations

- No Mininet experiment has yet run on this laptop; there are no defensible
  performance improvements to quote.
- Ryu 4.34 is unmaintained and can be sensitive to Python/eventlet versions.
- LLDP-based latency is a controller-derived link estimate. Application RTT
  comes from ping and should be reported separately.
- Directional counter loss can include polling skew and controller traffic.
- The utilization projection assumes the configured expected application rate;
  a future version should use per-flow OpenFlow meters/counters when supported.
- The queueing projection is a safety model that needs empirical calibration.
- The Braess topology is provided, but an actual repeatable Braess effect has
  not yet been demonstrated or measured here.
- Rule priority does not provide strict bandwidth reservation. Queue/QoS
  configuration is future SDN work if faculty requires hard guarantees.
- Host mobility, IPv6, controller failover, authentication, and production
  medical-device security are outside this prototype.

## Future live healthcare input

A future `WearableHealthcareDataSource` can translate a consented smartwatch,
wearable, or IoT API event into `HealthcareRecord`. It must expose only the
metadata needed for routing, avoid private patient fields, and use the same
traffic-type vocabulary. No smartwatch integration exists today.

## Research and patent-oriented contribution

The implemented technical combination is healthcare-criticality-aware profiles
+ measured multi-QoS SDN routing + fixed normalized cost + pre-deployment
Braess safety validation + OpenFlow enforcement + auditable evidence. The
distinctive behavior is the extra before/after safety gate: the controller can
reject the currently attractive route when its projected redistribution harms
critical flows or aggregate QoS. This is a basis for experiments and prior-art
review. It is not a legal claim of novelty or patentability.

## Safe faculty-review claims

You may say that the repository now implements and unit-tests modular data
sources, six explainable healthcare profiles, measured-metric calculations,
normalization, class-specific QoS cost, candidate selection, deterministic
Braess validation, telemetry warm-up fallback, anti-flapping logic, SQLite
persistence, and Ryu/OpenFlow integration code.

Do not yet say that this laptop has demonstrated dynamic OpenFlow rerouting,
improved critical-flow performance, an empirical Braess paradox, statistically
significant gains, wearable input, production readiness, or patentability.
Those claims require the Linux commands above and preserved experimental data.
