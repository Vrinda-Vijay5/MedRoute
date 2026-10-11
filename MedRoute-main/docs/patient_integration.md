# Patient/wearable integration contract

Actual device integration is pending identification of the available device,
companion application or API. Test/replay input must never be presented as
actual patient data.

## Envelope

`POST /v1/readings` accepts:

- pseudonymous `source_id`;
- `source_type` and interface name;
- source timestamp and 1–300 second freshness interval;
- readings with explicit metric, numeric value and unit;
- a required network flow binding: source/destination IP, IP protocol and
  optional source/destination ports;
- non-identifying metadata only.

Supported transport fields are heart rate (`bpm`), SpO2 (`percent`),
respiratory rate (`breaths/min`), temperature (`degC`) and the non-clinical
demonstration `priority_signal` (`level`). The generic gateway rejects common
identity fields.

## Modes and stale behavior

`test`, `replay` and `live` are stored with every reading. The generic webhook
cannot start in `live` mode; a real source requires a named, source-specific
adapter. A signal expires according to its source timestamp plus freshness.
Expired signals are excluded from controller lookup and the next 6-second
reevaluation returns that flow to its ordinary network traffic profile. The
dashboard displays stale/disconnected state and does not keep an old value
labeled live.

## Routing policy

`config/criticality_policy.json` is explicitly a routing demonstration policy,
not clinical logic. The default policy changes routing only from an upstream
`priority_signal`. Physiological values can be validated, recorded and shown,
but have no default threshold-to-criticality mapping. Any real integration must
document who produces the upstream priority, its units, clock behavior,
disconnect semantics, and its association with the transmitting network flow.
