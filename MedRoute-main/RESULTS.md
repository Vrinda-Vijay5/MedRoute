# Experimental evidence status

## Post-implementation evidence boundary (11 October 2026)

No old raw JSON, SQLite database, controller log, plot, trial configuration, or
flow-table dump was present in the supplied checkout. The Review-2 figures
(42/13 ms latency, 64/91 Mb/s throughput, 8.4/0.2% loss and 11.2/1.8 ms
jitter) therefore remain unverified; this statement does not infer how they
were produced.

The revised runners assign every new execution a `run_id`, trial index, raw
path, source SHA-256 and tool-version record. New results are new evidence and
must not be described as proof of the old slides. This Codex session could not
enter WSL (`E_ACCESSDENIED`), so it produced no new Mininet result rows.

## Dedicated Braess trials reported from WSL by the project owner

The following are the owner's latest reported measurements, not a rerun in
this Windows workspace. The raw WSL SQLite database, iperf JSON, and complete
controller logs were not present in this exported copy, so they cannot be
independently re-audited here.

| Configured inter-switch capacity | Trial | Selected-path ECG RTT |
|---:|---|---:|
| 100 Mbit/s | before, QoS | about 29.554 ms |
| 100 Mbit/s | after_unprotected, QoS | about 9.1 ms |
| 52 Mbit/s | before, QoS | 29.554 ms |
| 52 Mbit/s | after_unprotected, QoS | 9.1 ms |
| 52 Mbit/s | after_medroute, MedRoute | 7.897 ms |

Neither reported run satisfies the primary latency criterion
`after_unprotected > before`. The measurements therefore do **not** demonstrate
latency-based Braess degradation. The 52 Mbit/s run also does not demonstrate
avoidance of such a measured degradation because there was no observed
unprotected degradation to avoid. The controller's projected `BRAESS_RISK`
logs, where present, describe the validator's model decision and do not change
this empirical conclusion.

Earlier WSL checks reported a working Ryu 4.34 / Open vSwitch / Mininet setup,
21/21 tests at that point in development, telemetry readiness, OpenFlow rule
installation, and real iperf UDP measurements. Those are historical
operator-reported checks. They are not a current run of the final tree.

## Current workspace validation

This repository copy is on Windows and does not contain the WSL runtime,
Mininet namespaces, Open vSwitch, or Ryu. Current source-level test status and
commands must be reported from the active environment. In this workspace,
NetworkX is missing from `.venv`; therefore routing-engine tests cannot import.
Do not interpret fixture values or component tests as network measurements.

Experiment artifacts are generated under `results/` and intentionally ignored
by Git. Preserve the raw `results/raw/*.json`, SQLite database, and matching
controller logs for every reported run. Repeat trials and report dispersion
before drawing broader performance conclusions.
