"""
Route handlers.

Each handler takes (store, path_params, body) and returns (status_code,
response_dict). Deliberately independent of any HTTP library -- server.py
is the only file that knows about sockets/threads; these are plain
functions, trivially unit-testable without starting a server.
"""

from __future__ import annotations

from typing import Optional

from backend.api.store import Store, NotFoundError, ConflictError
from backend.scenarios.config import ScenarioConfig, ScenarioValidationError


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _require(body: Optional[dict]) -> dict:
    if body is None:
        raise ApiError(400, "Request body is required")
    if not isinstance(body, dict):
        raise ApiError(400, "Request body must be a JSON object")
    return body


# -- health / defaults ---------------------------------------------------------

def health(store: Store, params: dict, body: Optional[dict]):
    return 200, {"status": "ok"}


def defaults(store: Store, params: dict, body: Optional[dict]):
    """The paper-default ScenarioConfig, so a UI form can prefill itself."""
    return 200, ScenarioConfig().to_dict()


# -- scenarios ----------------------------------------------------------------

def create_scenario(store: Store, params: dict, body: Optional[dict]):
    body = _require(body)
    try:
        scenario_id = store.create_scenario(body)
    except ScenarioValidationError as e:
        raise ApiError(400, str(e))
    return 201, {"id": scenario_id, "scenario": store.get_scenario(scenario_id).to_dict()}


def list_scenarios(store: Store, params: dict, body: Optional[dict]):
    return 200, {"scenarios": store.list_scenarios()}


def get_scenario(store: Store, params: dict, body: Optional[dict]):
    try:
        scenario = store.get_scenario(params["id"])
    except NotFoundError as e:
        raise ApiError(404, str(e))
    return 200, {"id": params["id"], "scenario": scenario.to_dict()}


def update_scenario(store: Store, params: dict, body: Optional[dict]):
    body = _require(body)
    try:
        store.update_scenario(params["id"], body)
    except NotFoundError as e:
        raise ApiError(404, str(e))
    except ScenarioValidationError as e:
        raise ApiError(400, str(e))
    return 200, {"id": params["id"], "scenario": store.get_scenario(params["id"]).to_dict()}


def delete_scenario(store: Store, params: dict, body: Optional[dict]):
    try:
        store.delete_scenario(params["id"])
    except NotFoundError as e:
        raise ApiError(404, str(e))
    return 200, {"deleted": params["id"]}


# -- experiments --------------------------------------------------------------

def run_experiment(store: Store, params: dict, body: Optional[dict]):
    """
    Default (backward compatible): runs synchronously, responds 201 with the
    finished experiment's metadata. With "async": true in the body: validates,
    starts a background run, and responds 202 immediately -- poll the returned
    status_url for per-algorithm progress.
    """
    body = _require(body)
    scenario_id = body.get("scenario_id")
    inline_scenario = body.get("scenario")
    save_raw_events = body.get("save_raw_events", True)
    run_async = bool(body.get("async", False))
    if not scenario_id and not inline_scenario:
        raise ApiError(400, "Provide either 'scenario_id' or an inline 'scenario' object")
    try:
        if run_async:
            experiment_id = store.start_experiment_async(
                scenario_id=scenario_id, inline_scenario=inline_scenario, save_raw_events=save_raw_events)
            return 202, {"experiment_id": experiment_id, "status": "queued",
                         "status_url": f"/api/experiments/{experiment_id}/status"}
        experiment_id = store.run_experiment(scenario_id=scenario_id, inline_scenario=inline_scenario,
                                              save_raw_events=save_raw_events)
    except NotFoundError as e:
        raise ApiError(404, str(e))
    except ScenarioValidationError as e:
        raise ApiError(400, str(e))
    return 201, {"experiment_id": experiment_id,
                 "metadata": store.get_experiment_metadata(experiment_id)}


def get_experiment_status(store: Store, params: dict, body: Optional[dict]):
    try:
        return 200, store.get_experiment_status(params["id"])
    except NotFoundError as e:
        raise ApiError(404, str(e))


def list_experiments(store: Store, params: dict, body: Optional[dict]):
    return 200, {"experiments": store.list_experiments()}


def get_experiment(store: Store, params: dict, body: Optional[dict]):
    try:
        return 200, store.get_experiment_metadata(params["id"])
    except NotFoundError as e:
        raise ApiError(404, str(e))
    except ConflictError as e:
        raise ApiError(409, str(e))


def get_experiment_results(store: Store, params: dict, body: Optional[dict]):
    try:
        return 200, {"results": store.get_experiment_results(params["id"])}
    except NotFoundError as e:
        raise ApiError(404, str(e))
    except ConflictError as e:
        raise ApiError(409, str(e))


def get_experiment_metrics(store: Store, params: dict, body: Optional[dict]):
    try:
        return 200, store.get_experiment_metrics(params["id"])
    except NotFoundError as e:
        raise ApiError(404, str(e))
    except ConflictError as e:
        raise ApiError(409, str(e))


def get_experiment_graphs(store: Store, params: dict, body: Optional[dict]):
    try:
        return 200, store.get_experiment_graph_data(params["id"])
    except NotFoundError as e:
        raise ApiError(404, str(e))
    except ConflictError as e:
        raise ApiError(409, str(e))


def list_adapters(store, path_params, body):
    """GET /api/adapters -- communication adapters and what each can do (for the Mode 1 UI)."""
    from backend.adapters.registry import list_adapters as _list
    return 200, {"adapters": _list()}
