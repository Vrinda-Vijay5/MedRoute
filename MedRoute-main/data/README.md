# Healthcare network workload profiles

`healthcare_traffic_profiles.csv` is MedRoute's reproducible **input workload
catalog**, not a patient dataset and not experiment output. It has six
synthetic traffic profiles. The synthetic metadata source loads traffic type,
criticality, protocol, port, rate, packet size, and representative duration
from this CSV; the traffic classifier's explicit UDP-port and criticality
policy is tested against the catalog so the two stay aligned.

All rates, packet sizes, and durations are declared engineering workload
assumptions for exercising network routing. They are not clinical limits or
claims about actual devices. The dedicated Braess test intentionally declares
its own scenario load (2 Mbit/s ECG and five 20 Mbit/s imaging senders) and
records that configuration with each run. The ordinary experiment runner also
records its chosen rates/durations. Neither scenario overwrites these profiles.

The CSV contains no names, diagnoses, device identifiers, or patient records.
Its purpose is to generate repeatable healthcare-traffic-shaped network loads.
Latency, jitter, packet loss, throughput, and utilization are collected from
Mininet/OpenFlow/iperf/ping during execution and belong in experiment evidence,
never in this input catalog.

The synthetic source uses traffic type, rate, packet size, port, criticality,
and duration from the catalog when it creates metadata records. A consumer can
use those fields to shape traffic; the existing Mininet experiment scripts
still set their scenario-specific `iperf3` rates and durations explicitly.
`duration_seconds` does not override an experiment's explicit duration.
`source_role` and `destination_role` are explanatory labels, not real endpoint
addresses.
