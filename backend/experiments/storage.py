"""
ResultStorage.

Writes an ExperimentResult to disk in exactly the layout the spec asks for:

    <output_dir>/
        experiment_metadata.json
        raw/
            AEAR/run_001.json
            AEAR/run_002.json
            RSSI/run_001.json
            ...
        aggregated/
            scenario_summary.csv
            scenario_summary.json

JSON/CSV only (stdlib, no pandas) -- consistent with keeping the backend
plain Python for now.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Dict

from backend.experiments.runner import ExperimentResult, AGGREGATABLE_METRIC_FIELDS


class ResultStorage:
    @staticmethod
    def save(result: ExperimentResult, output_dir: str, save_raw_events: bool = True) -> None:
        os.makedirs(output_dir, exist_ok=True)
        ResultStorage._write_metadata(result, output_dir)
        ResultStorage._write_raw_runs(result, output_dir, save_raw_events)
        ResultStorage._write_aggregated(result, output_dir)

    @staticmethod
    def _write_metadata(result: ExperimentResult, output_dir: str) -> None:
        metadata = {
            "scenario": result.scenario.to_dict(),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "total_runs": len(result.runs),
            "algorithms": result.scenario.algorithms,
        }
        path = os.path.join(output_dir, "experiment_metadata.json")
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)

    @staticmethod
    def _write_raw_runs(result: ExperimentResult, output_dir: str, save_raw_events: bool) -> None:
        raw_dir = os.path.join(output_dir, "raw")
        for run in result.runs:
            algo_dir = os.path.join(raw_dir, run.algorithm)
            os.makedirs(algo_dir, exist_ok=True)
            path = os.path.join(algo_dir, f"run_{run.run_number:03d}.json")
            with open(path, "w") as f:
                json.dump(run.to_dict(include_events=save_raw_events), f, indent=2)

    @staticmethod
    def _write_aggregated(result: ExperimentResult, output_dir: str) -> None:
        aggregated_dir = os.path.join(output_dir, "aggregated")
        os.makedirs(aggregated_dir, exist_ok=True)
        aggregated = result.aggregate()

        json_path = os.path.join(aggregated_dir, "scenario_summary.json")
        json_ready = {algo: {metric: agg.to_dict() for metric, agg in metrics.items()}
                      for algo, metrics in aggregated.items()}
        with open(json_path, "w") as f:
            json.dump(json_ready, f, indent=2)

        csv_path = os.path.join(aggregated_dir, "scenario_summary.csv")
        ResultStorage._write_csv(aggregated, csv_path)

    @staticmethod
    def _write_csv(aggregated: Dict[str, dict], csv_path: str) -> None:
        fieldnames = ["algorithm"]
        for metric in AGGREGATABLE_METRIC_FIELDS:
            fieldnames += [f"{metric}_mean", f"{metric}_std", f"{metric}_n"]

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for algorithm, metrics in aggregated.items():
                row = {"algorithm": algorithm}
                for metric_name in AGGREGATABLE_METRIC_FIELDS:
                    agg = metrics.get(metric_name)
                    row[f"{metric_name}_mean"] = agg.mean if agg else None
                    row[f"{metric_name}_std"] = agg.std if agg else None
                    row[f"{metric_name}_n"] = agg.n if agg else 0
                writer.writerow(row)
