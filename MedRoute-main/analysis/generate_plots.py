"""Generate comparison plots only from experiment rows collected by Mininet."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def generate(database: Path, output_directory: Path, run_id: str | None = None) -> None:
    with sqlite3.connect(str(database)) as connection:
        frame = pd.read_sql_query("SELECT * FROM experiments ORDER BY timestamp", connection)
    if frame.empty:
        raise SystemExit("No actual experiments are stored; refusing to create empty or fabricated plots.")
    if run_id is not None:
        frame = frame[frame["run_id"] == run_id]
        if frame.empty:
            raise SystemExit("No actual experiments found for run_id={}".format(run_id))
    output_directory.mkdir(parents=True, exist_ok=True)
    suffix = "-{}".format(run_id) if run_id else "-all"
    frame.to_csv(output_directory / "raw-experiment-rows{}.csv".format(suffix), index=False)
    metrics = ("latency_ms", "jitter_ms", "packet_loss_pct", "throughput_bps", "path_cost")
    for metric in metrics:
        available = frame.dropna(subset=[metric])
        if available.empty:
            continue
        summary = available.groupby(["scenario", "routing_mode"])[metric].mean().unstack()
        axis = summary.plot(kind="bar", title="{}: routing comparison".format(metric))
        axis.set_ylabel(metric)
        axis.figure.tight_layout()
        axis.figure.savefig(output_directory / "{}{}.png".format(metric, suffix), dpi=160)
        plt.close(axis.figure)
    numeric = [name for name in metrics if frame[name].notna().any()]
    summary_table = frame.groupby(["scenario", "routing_mode"])[numeric].agg(
        ["count", "mean", "std", "median"]
    )
    summary_table.to_csv(output_directory / "summary{}.csv".format(suffix))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("results/medroute.db"))
    parser.add_argument("--output", type=Path, default=Path("results/plots"))
    parser.add_argument("--run-id")
    arguments = parser.parse_args()
    generate(arguments.database, arguments.output, arguments.run_id)
