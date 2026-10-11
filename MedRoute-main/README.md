# MedRoute

MedRoute is an SDN prototype for prioritized routing of healthcare network
traffic. Ryu discovers the topology, obtains live OpenFlow/LLDP measurements,
classifies healthcare flows, evaluates candidate routes, applies a
Braess-motivated counterfactual safety gate, and owns all IPv4 forwarding-rule
installation and removal.

The implementation does **not** claim a Nash/Wardrop solver, classical Braess
paradox proof, clinical decision support, bandwidth guarantees, AI/ML, or
patentability. The safety gate asks a narrower, testable question: could moving
the proposed flow to a candidate route harm an active critical flow or measured
aggregate network cost under the controller's documented load projection?

## Current status

| Area | Implementation | Runtime evidence in this checkout |
|---|---|---|
| Controller-owned forwarding | Cookies, route versions, paired reverse rules, make-before-break update, expiry, host-move and link-failure handling | Component-tested; WSL/OVS rerun required |
| Live network telemetry | OpenFlow counters, corrected LLDP latency, jitter, directional loss estimate, carried rate, utilization and residual capacity | Component-tested; WSL rerun required |
| Five-term route cost | Latency, jitter, loss, utilization and throughput/demand adequacy | Unit-tested |
| Patient-source contract | Validated schema, freshness, pseudonymous source, five-tuple binding, stale fallback | Test adapter only; actual device/API blocked until identified |
| Route-safety gate | Counterfactual protected-flow/aggregate checks, hysteresis, cooldown and post-action validation records | Unit-tested projection; live rerun required |
| Evidence pipeline | Randomized repeated matrix, raw JSON, SQLite, versions, source hash, flow dumps, CSV summaries and plots | Runners present; no new WSL run in this checkout |
| Dashboard | Read-only asynchronous API and UI for topology, metrics, flows, source state, decisions, safety and experiments | HTTP/component-tested; live controller state requires WSL |
| AI/ML | Deliberately deferred | Pending sufficient project-generated telemetry and a defined target |

No raw output substantiating the old Review-2 values (42/13 ms, 64/91
Mb/s, 8.4/0.2% loss, or 11.2/1.8 ms jitter) was found in the supplied
checkout. New evidence receives a new `run_id` and cannot retroactively prove
those figures. See [RESULTS.md](RESULTS.md).

## Runtime pipeline

```text
Mininet/real traffic
  -> Ryu PacketIn + healthcare port profile
  -> optional fresh patient-source priority bound to the same network five-tuple
  -> OpenFlow port counters + LLDP + echo telemetry
  -> five-term path cost
  -> candidate selection + counterfactual route-safety gate
  -> versioned forward/reverse OpenFlow rules (controller only)
  -> periodic rerouting + post-action measurement
  -> SQLite/raw JSON -> read-only dashboard and evidence plots
```

The default policy maps UDP destination ports 5001–5006 to ECG, ICU,
telemedicine, EHR, imaging and CCTV profiles. This is transparent demonstration
policy, not a clinical claim. A fresh patient-source signal can change the
routing criticality of a specifically bound five-tuple. When it expires, the
controller returns to the network traffic profile at its next reevaluation.

## Routing cost

For path `P` and requested or measured active-flow demand `r`:

```text
C(P) = wL NL + wJ NJ + wP NP + wU NU + wT NT

NL = clip(sum(link latency ms) / 100)
NJ = clip(sqrt(sum(link jitter ms²)) / 50)
NP = clip(end-to-end composed loss percent / 5)
NU = clip(max link utilization percent / 100)
NT = clip(r / minimum measured residual capacity)
```

All normalized terms are clipped to `[0,1]`; each profile's weights sum to
one. `NT` is called the throughput-adequacy term. It compares the requested
demand with residual capacity. Measured carried throughput is displayed and
stored, but is not treated as available capacity or rewarded merely because a
path is already busy. During an active flow, ingress OpenFlow flow-rule byte
counters estimate its demand; before that estimate is fresh, the declared
workload rate is the fallback.

See [docs/telemetry_and_cost.md](docs/telemetry_and_cost.md) for units,
provenance, aggregation, freshness and limitations.

## Controller-owned forwarding

`medroute_controller.py` is the only component that changes forwarding. It:

- uses protocol-aware five-tuple matches;
- allocates MedRoute namespace cookies and monotonically increasing route versions;
- installs downstream rules before the ingress rule, for both directions;
- sends per-switch barriers before deleting obsolete cookie versions;
- requests flow statistics, handles `FlowRemoved`, and removes inactive state;
- invalidates or reroutes flows after host movement, switch loss, or link removal.

The update is make-before-break per flow. OpenFlow message ordering and barriers
on individual switch connections reduce transient inconsistency, but do not
provide network-wide atomicity. ARP is centrally replicated only to access
ports. A telemetry warm-up fallback is still a controller decision and installs
controller-owned shortest-path rules.

Experiment code may call `ovs-ofctl dump-flows` to capture read-only evidence.
It never calls `add-flow` or `del-flows`.

## Patient-source modes

The ingestion process is separate from the read-only dashboard:

- `live`: reserved for a named, source-specific adapter that has been verified;
- `test`: explicit non-clinical test input;
- `replay`: saved input, never presented as live.

The generic webhook refuses `live` mode. It accepts no name, patient ID, email,
phone or address fields. See [docs/patient_integration.md](docs/patient_integration.md).

## Start the system in the existing WSL environment

From Ubuntu/WSL, point `MEDROUTE_VENV` at the already-working Linux virtual
environment. Do not use this checkout's Windows `.venv/Scripts` from Linux.

```bash
cd /mnt/c/Users/VICTUS/Downloads/MedRoute-main/MedRoute-main
export MEDROUTE_VENV=/path/to/your/existing/linux/venv
bash scripts/run_demo.sh normal
```

The script starts:

- Ryu on OpenFlow TCP 6653;
- the read-only dashboard at <http://127.0.0.1:8080>;
- the patient gateway at <http://127.0.0.1:8091> in visibly labeled `test` mode;
- the Mininet CLI.

It does not generate a patient reading automatically. In another WSL terminal,
an explicitly labeled test signal can be sent with:

```bash
cd /mnt/c/Users/VICTUS/Downloads/MedRoute-main/MedRoute-main
export MEDROUTE_VENV=/path/to/your/existing/linux/venv
"$MEDROUTE_VENV/bin/python" scripts/send_test_reading.py --priority 5 \
  --freshness-seconds 60
```

At the Mininet prompt, create a healthcare flow:

```text
h2 iperf3 -s -p 5001 &
h1 iperf3 -c 10.0.0.2 -u -b 500K -t 60 -p 5001 &
```

Create a condition change:

```text
bg2 iperf3 -s -p 6001 &
bg1 iperf3 -c 10.0.0.4 -u -b 85M -t 30 -p 6001 &
```

Verify actual switch state without changing it:

```text
sh ovs-ofctl -O OpenFlow13 dump-flows s1
sh ovs-ofctl -O OpenFlow13 dump-flows s2
sh ovs-ofctl -O OpenFlow13 dump-flows s3
sh ovs-ofctl -O OpenFlow13 dump-flows s4
```

The dashboard should show the active flow, fresh measurements, cost terms,
safety decision, rule version events and any measured route change. Allow at
least two 2-second counter intervals for telemetry warm-up and 6 seconds for a
periodic reevaluation.

## Tests

Windows-side tests that do not need NetworkX/Ryu/Mininet:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Full tests in the existing WSL environment:

```bash
cd /mnt/c/Users/VICTUS/Downloads/MedRoute-main/MedRoute-main
export MEDROUTE_VENV=/path/to/your/existing/linux/venv
PYTHONDONTWRITEBYTECODE=1 "$MEDROUTE_VENV/bin/python" -m unittest discover -s tests -v
```

## Evidence runs

Run a small smoke trial before the matrix:

```bash
RUN_ID=smoke-001 bash scripts/run_smoke.sh
```

Run a randomized repeated baseline/QoS/MedRoute matrix:

```bash
RUN_ID=matrix-001 REPETITIONS=3 RANDOM_SEED=308 DURATION=10 \
  bash scripts/run_experiment_matrix.sh
```

Run the candidate-link route-safety comparison:

```bash
RUN_ID=safety-001 BRAESS_REPETITIONS=3 RANDOM_SEED=308 DURATION=10 \
  bash scripts/run_braess_experiment.sh
```

Each trial recreates Mininet/OVS state and records trial order, timestamps,
configuration, tool versions, source SHA-256, controller decisions, path costs,
safety results, selected path, raw iperf/ping output and switch flow dumps.
Latency is entered in the experiment table only when the controller-recorded
ICMP path matches the healthcare switch path; otherwise it remains null and the
raw record explains why.

Inspect and regenerate:

```bash
sqlite3 -header -column results/medroute.db \
  "select run_id,scenario,routing_mode,latency_ms,jitter_ms,packet_loss_pct,throughput_bps,selected_path_json,raw_path from experiments order by id;"

"$MEDROUTE_VENV/bin/python" analysis/generate_plots.py \
  --database results/medroute.db --run-id matrix-001
```

Outputs are under `results/raw`, `results/plots`, controller logs, the SQLite
database, and each run's trial-order TSV. Negative or null results are retained.

## Important limitations

- WSL/OVS live behavior must be rerun after these changes; component tests are
  not evidence of installed switch behavior.
- OpenFlow port-pair loss is an asynchronous directional estimate, not exact
  per-flow receiver loss. Iperf receiver results are the end-to-end validation.
- LLDP/echo latency is an estimate; correlated end-to-end RTT is recorded separately.
- The safety projection is a queue-inspired heuristic and requires repeated
  with/without-candidate-link measurements before any Braess-type claim.
- No actual wearable source has been selected or verified yet.
- The default criticality policy reacts only to a non-clinical upstream
  `priority_signal`. Physiological measurements can be transported and shown,
  but do not alter routing until an agreed policy is documented.
- ML remains pending. Cybersecurity datasets such as CICIoMT2024 do not provide
  the routing/congestion/clinical-criticality targets required here.
