"""Compatibility entry point retained for the original Review-1 command."""

from importlib import import_module


def main() -> None:
    """Run the legacy topology entry point when Mininet is available."""
    try:
        # Mininet is an OS-level dependency and is only available in the
        # project's Linux/WSL runtime.  Keep this import lazy so importing or
        # syntax-checking this compatibility module works elsewhere.
        mininet_log = import_module("mininet.log")
        setLogLevel = mininet_log.setLogLevel
        from hospital_topology import run
    except ModuleNotFoundError as error:
        if error.name == "mininet":
            raise SystemExit(
                "Mininet is required to run hos_top.py. "
                "Run it in the documented Ubuntu/WSL2 environment and "
                "install Mininet and Open vSwitch first."
            ) from error
        raise

    setLogLevel("info")
    run()


if __name__ == "__main__":
    main()

