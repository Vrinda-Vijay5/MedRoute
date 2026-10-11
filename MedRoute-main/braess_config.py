"""Shared parameters for the currently reproducible dedicated trial.

The 52 Mbit/s value is preserved as a recorded empirical configuration. It is
not represented as a theoretically derived parameter for a classical Braess
equilibrium; see docs/braess_design.md for the analytical model and mapping
limitation.
"""

INTERSWITCH_CAPACITY_MBPS = 52
INTERSWITCH_CAPACITY_BPS = INTERSWITCH_CAPACITY_MBPS * 1_000_000

# Declared workload for the empirical stress comparison. These settings do
# not assert that the classical equilibrium model is realized by Mininet.
ECG_UDP_PORT = 5001
ECG_RATE_BPS = 2_000_000
IMAGING_UDP_PORT = 5005
IMAGING_RATE_BPS = 20_000_000
IMAGING_FLOW_PAIRS = (
    ("h2", "h5"), ("h3", "h6"), ("h7", "h10"),
    ("h8", "h11"), ("h9", "h12"),
)

SHORT_LINK_DELAY_MS = 0.5
OUTER_LINK_DELAY_MS = 12.0


def iperf_rate(rate_bps: int) -> str:
    """Format an exact whole-Mbit/s experiment rate for iperf3."""
    if rate_bps <= 0 or rate_bps % 1_000_000:
        raise ValueError("experiment iperf rate must be a positive whole Mbit/s")
    return "{}M".format(rate_bps // 1_000_000)
