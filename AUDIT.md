# Original repository audit

This audit records the state found before the 6 October 2026 continuation work.
It separates source-code presence from verified behavior.

## Files originally present

| File | Observed content | Assessment before changes |
|---|---|---|
| `medroute_controller.py` | Ryu 1.3 app, topology events, NetworkX shortest path, packet-out forwarding | Partial. Discovery and path lookup were plausible. It sent only the triggering packet at the current switch and did not install end-to-end OpenFlow routes. ARP was flooded. No monitoring, QoS, priority integration, dynamic rerouting, or Braess logic existed. |
| `hospital_topology.py` | Four-switch diamond with two hosts and two paths | Useful Review-1 topology. OpenFlow 1.3 was explicit. No load hosts or automated scenarios. |
| `hos_top.py` | Similar topology with the alternative route commented out | Duplicate/older topology variant. |
| `network_monitor.py` | Started a separate two-host Mininet network, ran one ping and printed `ifconfig` | A demonstration script, not a controller monitor. Its values were not collected, normalized, persisted, or used for routing. |
| `traffic_classifier.py` | Six string classes and integer criticality | Basic functionality present. No port mapping, rationale, or routing effect. |
| `traffic_generator.py` | Random traffic-type metadata | Basic synthetic metadata only; it did not send network traffic. |
| `healthcare_pipeline.py` | Generator followed by classifier | Basic composition present and disconnected from SDN. |
| `review 1  requirements` | Review notes including obsolete AI/game-theory plans | Historical artifact. Several claims described intended or previous-laptop behavior, not reproducible evidence in this export. |
| `analysis/blank` | Empty placeholder | Unnecessary. |
| three `*.pyc` files | Python 3.10 bytecode | Generated artifacts; removed. |

There was no `README.md`, dependency manifest, `.gitignore`, test suite, result
database, raw experiment data, plot code, data-source abstraction, QoS cost
module, Braess validator, or automated experiment runner.

## Review-1 claims and evidence

| Claim | What the source supported |
|---|---|
| Four-switch Mininet/Open vSwitch topology | Supported by topology source. Execution was reported in the historical note but no raw log was included. |
| Ryu/OpenFlow connection | Controller and topology configuration supported it in principle. Not rerun on this Windows laptop. |
| Dynamic topology discovery | Ryu topology API handlers and logging existed. |
| NetworkX shortest path | Implemented. |
| IPv4 forwarding | Partial packet-out behavior existed; full path rules did not. |
| ARP handling | Only learning-switch flooding; no proxy ARP. |
| Healthcare classification | Implemented for six explicit input strings. |
| Network monitoring | Not implemented as controller telemetry. |
| QoS/Braess/dynamic criticality routing | Missing. |

## Environment found on this laptop

- Windows PowerShell 5.1 on Windows build 26100.
- The `python` command resolved to a broken Microsoft Store alias.
- A working Python 3.11.9 interpreter exists at
  `C:\Users\VICTUS\AppData\Local\Programs\Python\Python311\python.exe`.
- pip 24.0 is installed for that interpreter.
- NetworkX, NumPy, Pandas, Matplotlib, Ryu, Mininet, and Open vSwitch were not
  available in the working interpreter/PATH.
- WSL is present as a Windows command, but distro enumeration returned access
  denied in this session.
- Package download was blocked by the current network policy.
- The supplied directory contains no `.git` directory. It is an exported copy,
  so history, branches, remotes, and a meaningful `git status` are unavailable.

