"""
Store.

The persistence layer behind the REST API. Scenarios are small and mutable
(CRUD), so they're kept in memory and mirrored to disk as JSON, reloaded at
startup by scanning the scenarios directory. Experiments are neither small
nor mutable once run -- ResultStorage (Phase 6) already writes everything
needed to disk in a durable, self-describing layout, so experiment retrieval
methods here just read straight from those files rather than keeping a
separate in-memory cache to invalidate. That means experiment results
survive a server restart for free; scenario edits made between a save and a
crash do not (acceptable for this phase -- a real database is explicitly a
later-phase upgrade per the project's own storage notes).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.scenarios.config import ScenarioConfig, ScenarioValidationError
from backend.experiments.runner import ExperimentRunner
from backend.experiments.storage import ResultStorage


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    """The request is fine but the resource isn't in a state that can serve it
    (e.g. asking for results of an experiment that is still running)."""


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACTIVE_STATUSES = ("queued", "running", "saving")


class Store:
    def __init__(self, root_dir: str, max_runs: int = 100, max_nodes: int = 200,
                 max_duration_s: float = 3600.0):
        """max_* are guard rails so a typo can't queue a multi-hour job on a
        laptop -- not scientific limits; raise them freely if you mean to."""
        self.root_dir = root_dir
        self.max_runs = max_runs
        self.max_nodes = max_nodes
        self.max_duration_s = max_duration_s
        self._lock = threading.Lock()
        self._live: Dict[str, dict] = {}       # in-memory status of experiments run this session
        self._threads: Dict[str, threading.Thread] = {}
        self.scenarios_dir = os.path.join(root_dir, "scenarios")
        self.experiments_dir = os.path.join(root_dir, "experiments")
        os.makedirs(self.scenarios_dir, exist_ok=True)
        os.makedirs(self.experiments_dir, exist_ok=True)

        self._scenarios: Dict[str, ScenarioConfig] = {}
        self._load_scenarios()

    # -- scenarios (in-memory + disk-mirrored) ---------------------------------

    def _scenario_path(self, scenario_id: str) -> str:
        return os.path.join(self.scenarios_dir, f"{scenario_id}.json")

    def _load_scenarios(self) -> None:
        for filename in os.listdir(self.scenarios_dir):
            if not filename.endswith(".json"):
                continue
            scenario_id = filename[: -len(".json")]
            with open(os.path.join(self.scenarios_dir, filename)) as f:
                data = json.load(f)
            self._scenarios[scenario_id] = ScenarioConfig.from_dict(data)

    def create_scenario(self, data: dict) -> str:
        scenario = self._parse_scenario(data)
        scenario_id = _new_id()
        self._scenarios[scenario_id] = scenario
        self._persist_scenario(scenario_id)
        return scenario_id

    def get_scenario(self, scenario_id: str) -> ScenarioConfig:
        if scenario_id not in self._scenarios:
            raise NotFoundError(f"No scenario with id {scenario_id}")
        return self._scenarios[scenario_id]

    def list_scenarios(self) -> List[dict]:
        return [{"id": sid, "name": s.name, "num_nodes": s.num_nodes,
                 "algorithms": s.algorithms, "num_runs": s.num_runs}
                for sid, s in self._scenarios.items()]

    def update_scenario(self, scenario_id: str, data: dict) -> None:
        if scenario_id not in self._scenarios:
            raise NotFoundError(f"No scenario with id {scenario_id}")
        scenario = self._parse_scenario(data)
        self._scenarios[scenario_id] = scenario
        self._persist_scenario(scenario_id)

    def delete_scenario(self, scenario_id: str) -> None:
        if scenario_id not in self._scenarios:
            raise NotFoundError(f"No scenario with id {scenario_id}")
        del self._scenarios[scenario_id]
        path = self._scenario_path(scenario_id)
        if os.path.exists(path):
            os.remove(path)

    def _persist_scenario(self, scenario_id: str) -> None:
        with open(self._scenario_path(scenario_id), "w") as f:
            json.dump(self._scenarios[scenario_id].to_dict(), f, indent=2)

    def _parse_scenario(self, data) -> ScenarioConfig:
        """from_dict + validate, with malformed input (wrong types, missing
        keys in nested structures) reported as a ScenarioValidationError --
        i.e. the client's fault (HTTP 400) -- rather than escaping as a
        TypeError/KeyError that would surface as a server error."""
        if not isinstance(data, dict):
            raise ScenarioValidationError("Scenario must be a JSON object")
        try:
            scenario = ScenarioConfig.from_dict(data)
            scenario.validate()
        except ScenarioValidationError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ScenarioValidationError(f"Malformed scenario: {type(e).__name__}: {e}")
        return scenario

    def _check_limits(self, scenario: ScenarioConfig) -> None:
        if scenario.num_runs > self.max_runs:
            raise ScenarioValidationError(f"num_runs {scenario.num_runs} exceeds the limit of {self.max_runs}")
        if scenario.num_nodes > self.max_nodes:
            raise ScenarioValidationError(f"num_nodes {scenario.num_nodes} exceeds the limit of {self.max_nodes}")
        if scenario.simulation_duration_s <= 0:
            raise ScenarioValidationError("simulation_duration_s must be positive")
        if scenario.simulation_duration_s > self.max_duration_s:
            raise ScenarioValidationError(
                f"simulation_duration_s {scenario.simulation_duration_s} exceeds the limit of {self.max_duration_s}")

    def _resolve_scenario(self, scenario_id: Optional[str], inline_scenario: Optional[dict]) -> ScenarioConfig:
        if scenario_id is not None:
            scenario = self.get_scenario(scenario_id)
        elif inline_scenario is not None:
            scenario = self._parse_scenario(inline_scenario)
        else:
            raise ValueError("Must provide either scenario_id or inline_scenario")
        self._check_limits(scenario)
        return scenario

    # -- experiments: synchronous run, or background run with status/progress ----
    #
    # Everything read AFTER an experiment completes comes straight from the
    # files ResultStorage wrote (single source of truth, survives a restart).
    # Only in-flight state (status, per-algorithm progress) is held in memory,
    # mirrored to status.json so a restart can tell "finished" from "died".

    def run_experiment(self, scenario_id: Optional[str] = None,
                        inline_scenario: Optional[dict] = None,
                        save_raw_events: bool = True) -> str:
        """
        Runs synchronously and returns once complete, holding the caller (and
        an HTTP request) open for the full duration. For anything longer than
        a few seconds prefer start_experiment_async(), which returns
        immediately and reports progress via get_experiment_status().
        """
        scenario = self._resolve_scenario(scenario_id, inline_scenario)
        result = ExperimentRunner(scenario).run(save_raw_events=save_raw_events)
        experiment_id = _new_id()
        output_dir = os.path.join(self.experiments_dir, experiment_id)
        ResultStorage.save(result, output_dir, save_raw_events=save_raw_events)
        return experiment_id

    def start_experiment_async(self, scenario_id: Optional[str] = None,
                                inline_scenario: Optional[dict] = None,
                                save_raw_events: bool = True) -> str:
        """Validate, then run in a background thread. Returns the experiment id
        immediately; poll get_experiment_status() for progress. A bad scenario
        raises here, synchronously -- nothing is queued for it."""
        scenario = self._resolve_scenario(scenario_id, inline_scenario)
        experiment_id = _new_id()
        status = {
            "experiment_id": experiment_id,
            "status": "queued",
            "scenario_name": scenario.name,
            "created_at": _now(),
            "started_at": None,
            "completed_at": None,
            "error": None,
            "progress": {a: {"completed": 0, "total": scenario.num_runs} for a in scenario.algorithms},
        }
        os.makedirs(os.path.join(self.experiments_dir, experiment_id), exist_ok=True)
        with self._lock:
            self._live[experiment_id] = status
        self._persist_status(json.loads(json.dumps(status)))

        thread = threading.Thread(target=self._execute_async, args=(experiment_id, scenario, save_raw_events),
                                  name=f"experiment-{experiment_id}", daemon=True)
        self._threads[experiment_id] = thread
        thread.start()
        return experiment_id

    def wait(self, experiment_id: str, timeout: Optional[float] = None) -> None:
        """Block until a background experiment finishes (for tests and demos)."""
        thread = self._threads.get(experiment_id)
        if thread is not None:
            thread.join(timeout)

    def _execute_async(self, experiment_id: str, scenario: ScenarioConfig, save_raw_events: bool) -> None:
        self._update_status(experiment_id, status="running", started_at=_now())

        def on_progress(algorithm: str, completed: int, total: int) -> None:
            self._update_status(experiment_id, progress_for=(algorithm, completed, total))

        try:
            result = ExperimentRunner(scenario).run(save_raw_events=save_raw_events,
                                                    progress_callback=on_progress)
            # All simulations are done, but writing raw events to disk can take a
            # few seconds on its own -- say so, rather than sitting at 100% "running".
            self._update_status(experiment_id, status="saving")
            ResultStorage.save(result, os.path.join(self.experiments_dir, experiment_id),
                               save_raw_events=save_raw_events)
            self._update_status(experiment_id, status="completed", completed_at=_now())
        except Exception as e:  # noqa: BLE001 -- any failure must become a visible status, not a silent dead thread
            self._update_status(experiment_id, status="failed", completed_at=_now(),
                                error=f"{type(e).__name__}: {e}")

    def _update_status(self, experiment_id: str, progress_for=None, **changes) -> None:
        with self._lock:
            status = self._live[experiment_id]
            if progress_for is not None:
                algorithm, completed, total = progress_for
                status["progress"][algorithm] = {"completed": completed, "total": total}
            status.update(changes)
            snapshot = json.loads(json.dumps(status))
        self._persist_status(snapshot)

    def _persist_status(self, status: dict) -> None:
        directory = os.path.join(self.experiments_dir, status["experiment_id"])
        tmp = os.path.join(directory, "status.json.tmp")
        final = os.path.join(directory, "status.json")
        terminal = status.get("status") in ("completed", "failed")
        # On Windows, os.replace raises PermissionError (WinError 5) if another handle has the
        # destination open at that instant (OneDrive sync, antivirus, a concurrent reader).
        # That is transient, so retry briefly. A progress tick is only a convenience mirror
        # (the live status is held in memory), so losing one must never kill the experiment;
        # a terminal status is retried for longer and then re-raised.
        attempts = 50 if terminal else 10
        for attempt in range(attempts):
            try:
                with open(tmp, "w") as f:
                    json.dump(status, f, indent=2)
                os.replace(tmp, final)  # atomic: never a half-written file
                return
            except PermissionError:
                if attempt == attempts - 1:
                    if terminal:
                        raise
                    return
                time.sleep(0.05 * (attempt + 1) if attempt < 10 else 0.5)

    def get_experiment_status(self, experiment_id: str) -> dict:
        directory = self._experiment_dir(experiment_id)  # NotFoundError if unknown
        with self._lock:
            live = self._live.get(experiment_id)
            if live is not None:
                return json.loads(json.dumps(live))
        status_path = os.path.join(directory, "status.json")
        if os.path.exists(status_path):
            with open(status_path) as f:
                status = json.load(f)
            if status["status"] in _ACTIVE_STATUSES:
                # Marked active on disk but nothing is running it: the process that
                # owned it died. Say so rather than reporting "running" forever.
                status["status"] = "failed"
                status["error"] = "Interrupted: the server stopped before this experiment finished"
            return status
        if os.path.exists(os.path.join(directory, "experiment_metadata.json")):
            return {"experiment_id": experiment_id, "status": "completed"}  # a synchronous run
        raise NotFoundError(f"No experiment with id {experiment_id}")

    def _require_completed(self, experiment_id: str) -> None:
        status = self.get_experiment_status(experiment_id)
        if status["status"] == "failed":
            raise ConflictError(f"Experiment {experiment_id} failed: {status.get('error')}")
        if status["status"] != "completed":
            raise ConflictError(f"Experiment {experiment_id} is {status['status']}; "
                                f"results are available once it completes")

    def _experiment_dir(self, experiment_id: str) -> str:
        path = os.path.join(self.experiments_dir, experiment_id)
        if not os.path.isdir(path):
            raise NotFoundError(f"No experiment with id {experiment_id}")
        return path

    def list_experiments(self) -> List[dict]:
        out = []
        for experiment_id in sorted(os.listdir(self.experiments_dir)):
            directory = os.path.join(self.experiments_dir, experiment_id)
            if not os.path.isdir(directory):
                continue
            try:
                status = self.get_experiment_status(experiment_id)
            except NotFoundError:
                continue
            metadata_path = os.path.join(directory, "experiment_metadata.json")
            if os.path.exists(metadata_path):
                with open(metadata_path) as f:
                    metadata = json.load(f)
                out.append({
                    "id": experiment_id,
                    "name": metadata["scenario"]["name"],
                    "status": status["status"],
                    "algorithms": metadata["algorithms"],
                    "total_runs": metadata["total_runs"],
                    "generated_at_utc": metadata["generated_at_utc"],
                })
            else:  # queued/running/failed: no metadata written yet
                progress = status.get("progress", {})
                out.append({
                    "id": experiment_id,
                    "name": status.get("scenario_name"),
                    "status": status["status"],
                    "algorithms": list(progress.keys()),
                    "total_runs": next((p["total"] for p in progress.values()), None),
                    "generated_at_utc": None,
                })
        return out

    def get_experiment_metadata(self, experiment_id: str) -> dict:
        self._require_completed(experiment_id)
        path = os.path.join(self._experiment_dir(experiment_id), "experiment_metadata.json")
        with open(path) as f:
            return json.load(f)

    def get_experiment_metrics(self, experiment_id: str) -> dict:
        self._require_completed(experiment_id)
        path = os.path.join(self._experiment_dir(experiment_id), "aggregated", "scenario_summary.json")
        with open(path) as f:
            return json.load(f)

    def get_experiment_results(self, experiment_id: str) -> dict:
        """All per-run raw results (metrics + validation; events only if
        they were saved), grouped by algorithm."""
        self._require_completed(experiment_id)
        exp_dir = self._experiment_dir(experiment_id)
        raw_dir = os.path.join(exp_dir, "raw")
        results: Dict[str, List[dict]] = {}
        if not os.path.isdir(raw_dir):
            return results
        for algorithm in sorted(os.listdir(raw_dir)):
            algo_dir = os.path.join(raw_dir, algorithm)
            if not os.path.isdir(algo_dir):
                continue
            runs = []
            for filename in sorted(os.listdir(algo_dir)):
                if not filename.endswith(".json"):
                    continue
                with open(os.path.join(algo_dir, filename)) as f:
                    runs.append(json.load(f))
            results[algorithm] = runs
        return results

    def get_experiment_graph_data(self, experiment_id: str) -> dict:
        """
        Chart-ready series, not rendered images -- Phase 12's frontend will
        feed this straight into its charting library. One entry per metric,
        each holding one (algorithm -> {mean, std}) point per algorithm, in
        the shape a bar chart with error bars wants directly.
        """
        aggregated = self.get_experiment_metrics(experiment_id)
        metric_names = sorted({m for metrics in aggregated.values() for m in metrics})
        series = {}
        for metric_name in metric_names:
            series[metric_name] = [
                {"algorithm": algo, "mean": metrics.get(metric_name, {}).get("mean"),
                 "std": metrics.get(metric_name, {}).get("std")}
                for algo, metrics in aggregated.items()
            ]
        return {"metrics": series}