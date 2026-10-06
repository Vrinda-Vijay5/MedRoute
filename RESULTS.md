# Experiment result status

No Mininet/Open vSwitch experiment was executed on this Windows laptop. There
are therefore no baseline-versus-MedRoute performance numbers to report yet.

The repository contains an experiment runner that writes raw `ping` and
`iperf3 -J` evidence plus structured SQLite rows. The plot generator exits when
the experiment table is empty, which prevents placeholder or fabricated graphs.

Verified in the user's Ubuntu 22.04/WSL2 environment before the LLDP telemetry
pipeline repair:

```text
python -m unittest discover -s tests -v         17 tests, PASS
Mininet pingall                                 12/12 received, 0% dropped
UDP healthcare iperf                           about 2.00 Mbit/s
UDP receiver jitter                            about 0.132 ms
UDP receiver loss                              0/2590 packets
```

These tests use explicit fixtures to check formulas and decision logic. Fixture
values are test inputs and must not be presented as experimental network data.

Those WSL observations prove fallback forwarding and OpenFlow installation;
they do not yet prove that measured-QoS/Braess mode ran. The persistent 0/8
telemetry state was traced to an invalid Ryu `Port` construction in the LLDP
timestamp lookup and has now been repaired.

The current suite contains 21 tests. It adds Ryu-compatible LLDP timestamp
lookup, 0/N to N/N readiness, measured idle-link zeros, topology/metric key
matching, stale-LLDP rejection, and monitor-to-routing warm-up transition.
Thirteen dependency-light tests pass in this Windows workspace; NetworkX is
absent here, so the eight routing tests require the WSL environment. Run the
current full total inside Linux with the command in `README.md`.
