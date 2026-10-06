"""Generate comparison plots only from experiment rows collected by Mininet."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def generate(database: Path, output_directory: Path) -> None:
    with sqlite3.connect(str(database)) as connection:
        frame = pd.read_sql_query("SELECT * FROM experiments ORDER BY timestamp", connection)
    if frame.empty:
        raise SystemExit("No actual experiments are stored; refusing to create empty or fabricated plots.")
    output_directory.mkdir(parents=True, exist_ok=True)
    metrics = ("latency_ms", "jitter_ms", "packet_loss_pct", "throughput_bps", "path_cost")
    for metric in metrics:
        available = frame.dropna(subset=[metric])
        if available.empty:
            continue
        summary = available.groupby(["scenario", "routing_mode"])[metric].mean().unstack()
        axis = summary.plot(kind="bar", title="{}: routing comparison".format(metric))
        axis.set_ylabel(metric)
        axis.figure.tight_layout()
        axis.figure.savefig(output_directory / "{}.png".format(metric), dpi=160)
        plt.close(axis.figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("results/medroute.db"))
    parser.add_argument("--output", type=Path, default=Path("results/plots"))
    arguments = parser.parse_args()
    generate(arguments.database, arguments.output)
