"""Compatibility entry point retained for the original Review-1 command."""

from mininet.log import setLogLevel

from hospital_topology import run


if __name__ == "__main__":
    setLogLevel("info")
    run()

