# ShadowNet

Adaptive Emergency-Aware LoRa Mesh Routing — a simulation and experiment platform for comparing **AEAR**
against an **RSSI** baseline and an **AODV** baseline: a discrete-event simulator, a batch experiment runner
(Mode 2) with statistics, validation and export, a live step-through network (Mode 1), and a web UI for both.

## Quick start

Needs Python 3.10+ (standard library only) and, to rebuild the UI, Node 18+.

```bash
cd shadownet
python3 -m backend.api            # then open http://127.0.0.1:8000
```

That one process serves the web UI, the REST API (`/api/...`) and the Mode 1 WebSocket (`/ws/live`).
Stored scenarios and experiments go in `shadownet/data/` (created on first use; `--data DIR` to change).
Options: `python3 -m backend.api 8001 --host 127.0.0.1 --data DIR --no-frontend`. Ctrl+C stops it.

The zip includes a pre-built UI (`frontend/dist`, built from the included source). After changing frontend code:

```bash
cd frontend && npm ci && npm run build      # the backend picks up the new build on the next page load
# or, for development with hot reload (run the backend too):   npm run dev   -> http://localhost:5173
```

```bash
python3 -m pip install pytest && python3 -m pytest tests -q     # backend: 319 tests, ~20 s
cd frontend && npm test                                          # frontend: 55 tests, ~10 s
```

What to try first: **Experiments** -> Run experiment (defaults) -> watch progress -> results table, charts, CSV.
Then **Live Network** -> Create session -> Send (Emergency) -> Step / Play -> click through the routing-decision
panel; fail a node and send again. **Algorithms** has a calculator that scores a hop the way AEAR does.

## Status: all 16 phases complete

- [x] **1–7** Simulation core, event engine, AEAR / RSSI / AODV routers, failures + store-and-forward, metrics, experiment runner with statistics and on-disk results, validation layer
- [x] **8** REST API (Mode 2)  ·  **9** WebSocket layer (Mode 1)  ·  **10** communication-adapter abstraction (simulator real, LoRa a stub)
- [x] **11** Web app shell  ·  **12** Mode 2 UI  ·  **13** Mode 1 UI  ·  **14** Scenario Manager, Algorithm Explorer, Developer pages
- [x] **15** Test suites (invariants, regressions, fixtures, page tests)  ·  **16** Integration: single-process serving, launcher, final fixes

## Known limitations (read before relying on results)

- **LoRa is not implemented.** `LoRaAdapter` is a stub that refuses to start; there is no hardware integration.
  The adapter interface was shaped by the simulator's needs and will need changes for real radios.
- **No authentication.** It binds to 127.0.0.1 by default and has no Origin check. Do not expose it on a shared
  network. `--host 0.0.0.0` is on you.
- **The UI has never been looked at in a real browser by its author.** The development sandbox could not
  run one. The 55 frontend tests render every page in jsdom against payloads captured from the real backend and a
  scripted WebSocket, so behaviour, data wiring and error handling are tested, but *visual layout, styling, and
  chart rendering are not* (Recharts draws nothing at jsdom's zero size). Expect to find cosmetic problems.
- **AODV is a recognisable AODV, not an RFC 3561 implementation**, and its numbers depend on these choices (see the
  AODV section). Results from scenarios with node failures produced before Phase 15 may differ: see that section.
- **Sessions are in memory.** A Live session lives on its WebSocket connection; a page refresh loses it. Experiments
  and scenarios persist on disk, but there is no cancel for a running experiment.
- **Run-size guard rails** reject oversized experiments; a single run that exceeds 2,000,000 events is aborted as a
  probable loop (the experiment ends `failed`, no partial results kept).
- **No significance testing in the UI.** The results table bolds the best mean and shows ±std; with few runs a gap
  can be noise. Use the confidence intervals in the JSON export and enough runs.
- Not built: a viewer for raw per-event data (saved to disk only if requested, not served), editing failure events /
  AEAR weights / queue sampler in the UI (they are preserved, not editable), per-priority charts.

## Phase 16 — Integration pass

- `backend/api/static.py` + `server.py`: serve `frontend/dist` from the backend. `/api/...` and `/ws/...` never fall
  through to the UI; unknown extensionless paths (client-side routes such as `/experiments/ab12`) get `index.html`
  so refresh and deep links work; a missing `.js`/`.css` stays 404; files under `assets/` are cached immutably and
  `index.html` is never cached. Path traversal (plain, percent-encoded, double-encoded, backslash, NUL, symlink
  escapes) is refused, and the traversal tests were mutation-checked.
- `python3 -m backend.api` is now a proper launcher: `--host`, `--data`, `--frontend`, `--no-frontend`, a readable
  error when the port is taken, a hint when the UI isn't built, and clean Ctrl+C with live WebSockets. **Change:**
  the data directory is now `<project>/data` instead of `./data` in whatever directory you launched from, so it
  doesn't matter where you start it. Move an existing `data/` if you had one.
- **Bug found and fixed — packet IDs collided between engines.** IDs came from one process-wide counter that the
  runner and every Live session reset. Starting an experiment or opening a second Live tab made a running Live session
  reuse IDs, so a new packet silently replaced an earlier in-flight one (demonstrated: 3 packets sent, 2 tracked, an
  Emergency packet overwritten). Each engine now numbers its own packets. All 27 fingerprinted runs are byte-identical
  before and after, so experiment results are unaffected. Tests: `tests/test_phase16_packet_ids.py` (5 of 6 fail
  against the old behaviour; the threaded-experiments test is a weak guard because the race window is tiny).
- Page-render tests and a fixture shape-drift test (`tests/test_phase15_fixtures.py`): if the backend changes a response
  the UI reads, the Python suite fails until fixtures are regenerated (`python3 scripts/make_frontend_fixtures.py`).

## What's implemented in Phase 1

```
backend/simulation/
    network/
        node.py      Node: id, position, status (NORMAL/CONGESTED/FAILED),
                      per-node PacketQueue, fail()/restore()
        link.py       Link: RSSI simulated via a log-distance path-loss model
                      (explicitly a simulation assumption, not hardware data),
                      ACTIVE/FAILED status for link failure injection
        network.py    Network: builds topology (auto-random or manual),
                      neighbor discovery, available_neighbors_of() — the
                      single source of truth routing engines will use later
    packets/
        packet.py     Packet: NORMAL/IMPORTANT/EMERGENCY priority, TTL,
                      visited-node tracking, hop count, retransmission
                      counting, store-and-forward status, full forwarding
                      history for later metrics traceability
    queues/
        queue_model.py PacketQueue: fixed-capacity FIFO, overflow counting
```

Nothing here does routing yet — these are the building blocks. AEAR/RSSI/AODV
come in Phase 3, once the event-driven engine (Phase 2) exists to drive them.

```
backend/simulation/events/
    event.py       EventType (all 21 event types from the spec), Event record,
                    EventLog (append-only, queryable — the raw data the
                    Metrics engine will read from in Phase 5)
    scheduler.py    EventScheduler: min-heap future-event-list, time-ordered
                    with FIFO tie-break for same-timestamp events

backend/simulation/engine/
    engine.py       SimulationEngine: owns the clock, drives step()/run(),
                    applies network-state effects (node/link fail+restore)
                    with no routing logic baked in, and exposes a
                    register_handler() hook so Phase 3's AEAR/RSSI/AODV can
                    plug into PACKET_GENERATED / PACKET_RECEIVED without
                    this file ever changing
```

## How Phase 3 will plug in (so the design makes sense now)

The engine is intentionally routing-agnostic. A routing engine will:
1. Call `engine.register_handler(EventType.PACKET_GENERATED, my_aear_handler)`
   and the same for `PACKET_RECEIVED`.
2. Inside that handler, read `engine.network.available_neighbors_of(node_id)`,
   compute scores, and call `engine.log_candidate_evaluated(...)` /
   `engine.log_forwarding_decision(...)` for visibility.
3. Call `engine.forward_packet(...)`, `engine.buffer_packet(...)`, or
   `engine.drop_packet(...)` to actually act on the decision.

Three algorithms, one engine, zero engine changes — see `demo_phase2.py` for
a (deliberately naive, non-AEAR) handler doing exactly this end-to-end,
including a node-failure-mid-flight scenario that correctly buffers the
packet.

## AEAR (Phase 3a) — how it works, and how to configure it

```
backend/simulation/routing/aear.py
    AEARWeights    per-priority (signal, hop, congestion) weights, must sum to 1
    AEARConfig     weights_by_priority dict + hop_transmission_delay_s;
                   validated on construction (raises if any weight set is
                   invalid or a priority level is missing)
    AEARCandidate  one evaluated candidate: raw inputs + normalized factors + score
    AEARRouter     registers on PACKET_GENERATED / PACKET_RECEIVED; does the
                   actual candidate discovery, scoring, and forwarding
```

At each hop: discover available (unfailed, link-active) neighbors → drop
already-visited ones → if none left, buffer (store-and-forward) → otherwise
compute `S_j` (normalized RSSI), `H_j` (hop efficiency, from a straight-line
distance-to-destination heuristic — local, not global, per the paper's
constraint that AEAR must never compute a centralized route), and `C_j`
(congestion availability from queue occupancy) for every candidate, apply
the priority-dependent weights, and forward to the highest scorer.

**The default weights in `AEARConfig` are placeholders, not paper values** —
the spec explicitly treats priority-dependent weights as an experimental
parameter to sweep (see the "AEAR Weight Sensitivity" graph requirement),
so pass your own `AEARConfig` once you have real values to test:

```python
from backend.simulation.routing.aear import AEARRouter, AEARConfig, AEARWeights

config = AEARConfig(weights_by_priority={
    0: AEARWeights(signal=0.40, hop=0.30, congestion=0.30),  # NORMAL
    1: AEARWeights(signal=0.35, hop=0.35, congestion=0.30),  # IMPORTANT
    2: AEARWeights(signal=0.30, hop=0.50, congestion=0.20),  # EMERGENCY
})
router = AEARRouter(network, engine, config=config)
```

Run `python3 demo_phase3_aear.py` to see a full decision panel (RSSI,
normalized factors, weights, scores, selection) for every hop of a real
15-node network — including a case where the NORMAL-priority packet gets
stuck and buffered while the EMERGENCY packet (different weights → different
path) gets through. That's a real local-greedy-routing limitation, not a
bug, and it's exactly the kind of scenario that will matter once RSSI/AODV
comparisons start in Mode 2.

**Known scope boundary (by design, not an oversight):** AEAR does not yet
retry a buffered packet when a failed neighbor comes back online — that
re-evaluation trigger is Phase 4 (failure model + store-and-forward), which
will hook `NODE_RECOVERED`/`LINK_RESTORED` to call `engine.unbuffer_packet()`
and re-route. Right now a buffered packet stays buffered for the rest of
the run.

`engine.reset()` clears the clock, event log, scheduler, and packet
registry — but **deliberately does not clear registered handlers**, since a
routing engine should register once and be reused across many experiment
runs. Don't re-register the same handler after a `reset()` or events will
fire twice (this bit the first draft of `demo_phase2.py` — fixed, but worth
remembering as Phase 6's experiment runner starts calling `reset()` between
runs).

Also note: `engine.reset()` does not reset `Network` node/link failure
status — a fresh `Network` (or explicit `restore_node`/`restore_link` calls)
is needed between runs if a previous run failed anything. Phase 6 will
handle this by regenerating a same-seed `Network` per run rather than
reusing a mutated one.

## RSSI baseline (Phase 3b)

```
backend/simulation/routing/rssi.py
    RSSIConfig     just hop_transmission_delay_s — nothing else to configure,
                   by design (no weights, no congestion factor)
    RSSICandidate  node_id, rssi_dbm, status
    RSSIRouter     registers on PACKET_GENERATED / PACKET_RECEIVED; at every
                   hop, forwards to whichever available/unvisited neighbor
                   has the strongest RSSI. Nothing else factors in.
```

This is intentionally the "dumb" baseline: no congestion awareness, no hop
efficiency, no priority weighting (tested explicitly — `test_priority_has_no_
effect_on_selection` proves the same node wins regardless of packet type,
unlike AEAR). Run `python3 demo_phase3_rssi_vs_aear.py` to see AEAR and RSSI
run on the **identical** topology/seed/source/destination — AEAR delivers in
4 hops while RSSI chases signal strength into a dead end and ends up
buffered after 5 hops. That's a real, unforced outcome difference purely
from swapping the routing module, which is exactly the point of the whole
comparison.

## AODV baseline (Phase 3c)

The big one. Unlike AEAR/RSSI's per-hop greedy decisions, AODV needs to
*discover* a route before it can send anything:

```
backend/simulation/routing/aodv.py
    AODVConfig    active_route_timeout_s, route_discovery_timeout_s,
                  rreq/rrep propagation delays, hop transmission delay
    RouteEntry    destination, next_hop, hop_count, dest_seq_num,
                  lifetime_expiry, precursors, valid
    AODVRouter    the full RREQ/RREP/RERR state machine + data forwarding
                  once a route exists
```

What it actually does, end to end:
1. **No route known** → node floods a **RREQ**, broadcast to all currently
   available neighbors, incrementing hop count each rebroadcast.
   `(originator, rreq_id)` pairs are tracked per-node so a RREQ arriving via
   two different paths is only processed once (verified by
   `test_destination_only_replies_once_despite_two_arrival_paths` on a
   diamond topology).
2. Every node that forwards a RREQ caches a **reverse route** back toward
   the originator — that's what lets the reply find its way back.
3. The destination (or any node with a fresh cached route, checked via
   **destination sequence numbers**) sends a **RREP** back along that
   reverse path, hop by hop, establishing a **forward route** at every
   relay as it goes — and recording a **precursor** at each hop (the
   neighbor that will now depend on this node as its next hop).
4. Once the RREP reaches the original source, its routing table has a
   usable entry, and data now flows hop-by-hop via simple table lookups —
   same `engine.forward_packet()` primitive AEAR/RSSI use.
5. **Route maintenance**: a `NODE_FAILED` or `LINK_FAILED` event is checked
   against every routing table; any entry whose `next_hop` is now
   unreachable is invalidated and a **RERR** cascades to that entry's
   precursors, who invalidate their own entries and cascade further.
6. A packet whose destination has no working route gets buffered
   (store-and-forward) while discovery is in flight; if discovery times out
   with no RREP, it stays buffered — no different from AEAR/RSSI's
   documented Phase 4 boundary.

Run `python3 demo_phase3_aodv.py` to see the entire control-plane trace —
every RREQ/RREP hop, the routing table at N1, a cached-route reuse (second
packet triggers **zero** new RREQs), then N3 failing mid-network, RERR
cascading back to N1, and a fresh RREQ attempt (buffered, since N3 was the
only path in that line topology).

**One cosmetic thing to know if you add your own live event printer**: RERR
cascades happen via *synchronous* nested dispatch inside `NODE_FAILED`'s own
handler call, so if you print events from inside a handler (like the demo
does), a cascaded `RERR_SENT` can print *before* the `NODE_FAILED` line that
caused it. The actual `engine.event_log` list is correctly chronological
regardless (verified directly — `NODE_FAILED` event ID 25 precedes
`RERR_SENT` event ID 26 in the real log); it's purely a live-printing
artifact, not an ordering bug. Read from `event_log.all()` for ground truth.

## Phase 4 — Failure model + store-and-forward (closing Phase 3's open loop)

Every Phase 3 router documented the same gap: a packet that gets buffered
because there's no valid next hop stays buffered forever, even once the
network recovers. Phase 4 closes that:

```
backend/simulation/failures/
    recovery.py    FailureRecoveryManager -- algorithm-agnostic; calls
                   router.retry_buffered_packets() on every NODE_RECOVERED
                   / LINK_RESTORED event
    schedule.py    FailureEvent, apply_failure_schedule(), plus two preset
                   builders matching the paper's own scenarios directly:
                   node_failure_scenario() (Scenario 4) and
                   link_disconnection_scenario() (Scenario 5)
```

Each router now exposes `retry_buffered_packets()`:
- **AEAR / RSSI** have no in-flight state to protect, so they simply retry
  every currently-BUFFERED packet whenever called.
- **AODV** is more careful: a packet can be BUFFERED either because it's
  legitimately still mid-route-discovery (waiting on a RREP) or because
  discovery already gave up. Only the latter are safe to retry externally —
  touching a mid-discovery packet would create a duplicate entry in AODV's
  own `pending_packets` tracking and risk routing the same packet twice from
  two different points in its journey. `retry_buffered_packets()` checks
  `pending_packets` before touching anything, and there's a dedicated test
  (`test_recovery_does_not_disturb_a_packet_still_mid_discovery`) proving
  this stays safe.

**A real race this phase's own demo caught**: building `demo_phase4_recovery.py`
surfaced a genuine timing bug — if a link/node recovers at the *exact* same
simulated instant AODV's route-discovery timeout would otherwise give up,
FIFO tie-break processed the give-up in a way that stranded the packet
forever (the recovery-triggered retry ran first, correctly deferred to
AODV's own in-flight tracking, and then the timeout gave up right after with
no one left to retry it again). Fixed properly rather than special-cased:
AODV now does **bounded RREQ retries** (`AODVConfig.max_rreq_retries`,
default 2) before finally giving up, which is both more realistic (real
AODV implementations retry a bounded number of times too) and eliminates
the race. There's a regression test for exactly this scenario
(`test_recovery_and_discovery_timeout_racing_at_the_same_instant`).

Run `python3 demo_phase4_recovery.py` to see both paper scenarios end to
end: **Scenario 4** (AEAR, forwarding node failure during transmission) and
**Scenario 5** (AODV, temporary link disconnection then restoration) — both
buffer correctly on failure and both deliver automatically once the fault
clears, with zero manual intervention beyond registering
`FailureRecoveryManager` once at setup.

## Phase 5 — Metrics engine (everything derived from raw events, nothing else)

```
backend/simulation/metrics/
    engine.py    MetricsEngine.compute(event_log) -> MetricsResult -- the ONLY
                 input is an EventLog, structurally enforcing that no metric
                 can be computed from live Packet/Node state by accident
    sampler.py   QueueSampler -- periodic QUEUE_SNAPSHOT events, since queue
                 utilization/length have no other raw-event source
```

Every metric from the spec's list (PDR, delay, emergency PDR/delay, hop
count, packet loss, routing overhead, recovery time, duplicate rate, queue
overflow/utilization/length, forwarding decisions, retransmissions,
buffering/recovery counts) is implemented, plus a bonus per-priority PDR/delay
breakdown. `MetricsResult.to_dict()` gives a JSON-ready shape for Phase 8's
API and Phase 12's UI later.

**One addition beyond the original event list**: `QUEUE_SNAPSHOT` — queue
occupancy is live state, not an event, so without periodic sampling there
was no honest raw-event source for Queue Utilization/Average/Maximum
Length at all. `QueueSampler` is opt-in (attach it before running, and it
must be given a bounded `end_time` since it reschedules itself — there's a
test, `test_sampler_run_terminates_and_stays_bounded`, that would hang if
that bound were ever broken). Without a sampler, those three metrics come
back `None` with a note explaining why — never a fabricated number.

**Several metrics are honestly zero right now, on purpose**: `retransmissions`,
`failed_forwarding_attempts`, and `duplicate_packet_rate_percent` all read 0
because none of AEAR/RSSI/AODV currently model a probabilistic per-hop
transmission failure or ever duplicate a data packet (loop prevention rules
that out structurally). `MetricsResult.notes` explains this inline so a
zero always reads as "this is what actually happened," never "something
wasn't wired up" — directly satisfying the "never fabricate results, never
hide unfavorable results" requirement.

**`routing_overhead_control_messages` is a genuinely meaningful number**:
it counts only real radio transmissions (RREQ/RREP/RERR *sent*, not
received) — AEAR and RSSI correctly report exactly 0 here, since they make
purely local decisions with zero control-plane airtime, which is one of
their real structural advantages over AODV in a duty-cycle-constrained LoRa
context.

**A genuinely important finding from building `demo_phase5_metrics.py`**:
on one particular random topology (seed 4242, 25 nodes), AEAR and RSSI both
scored **0% PDR** while AODV scored **100%** — and this wasn't caused by the
failure injected into that scenario; the same result holds with no failures
at all. A quick BFS confirms N25 *is* reachable from N1 in that topology.
The cause is the classic **local-minimum problem** in greedy routing: once
every neighbor at some node has already been visited, a purely local,
no-backtracking algorithm is stuck dead, even though a path exists
elsewhere in the graph. AODV's flood-based discovery has no such weakness.
This is a real, worth-keeping result for your AEAR-vs-AODV writeup — but
it's also topology-dependent: Phase 3's own demo showed AEAR cleanly
beating RSSI on a *different* seed. That instability across single
topology instances is exactly why the paper's own methodology insists on
**many independent runs, aggregated as mean ± std** rather than judging
from any one instance — which is precisely what Phase 6 will build.

Run `python3 demo_phase5_metrics.py` to see the full AEAR/RSSI/AODV metrics
table side by side on identical traffic and topology, plus this finding
spelled out at the end.

## Phase 6 — Experiment runner + result storage

```
backend/scenarios/
    config.py      ScenarioConfig -- one serializable object describing an
                   entire experiment: network shape, traffic profile,
                   failure timing, algorithms, run count, seed. Doubles as
                   the data model Phase 14's Scenario Manager will persist.

backend/experiments/
    traffic.py     generate_traffic() -- reproducible Poisson-process arrivals
                   + priority ratios + optional emergency burst window,
                   generated independently of any live Network (node IDs
                   are always "N1".."N{num_nodes}"), so the exact same
                   traffic profile can be reused unchanged across algorithms
    stats.py       mean_std_ci() -- mean/std/95% CI across independent runs
    runner.py      ExperimentRunner -- orchestrates num_runs x algorithms,
                   ExperimentResult.aggregate() for the mean+-std table
    storage.py     ResultStorage -- writes experiment_metadata.json,
                   raw/{ALGORITHM}/run_NNN.json, aggregated/scenario_summary.{csv,json}
```

**The fair-comparison guarantee is structural, not just documented**: for a
given run number, every algorithm gets a *freshly generated* `Network` from
the *same* seed and the *same* pre-generated traffic list — never a mutated
network left over from a previous algorithm's run (that exact mistake was
called out as a footgun back in Phase 2/3, and Phase 6 is where it would
actually bite if not handled). `test_same_run_number_gets_identical_topology_
and_traffic_across_algorithms` checks this directly.

**Statistics use a documented approximation**: the 95% CI uses the normal
approximation (z=1.96) rather than a t-distribution critical value, which
is slightly optimistic (too narrow) for small run counts like the paper's
own default of 10. `stats.py`'s docstring says so explicitly and names
exactly what to swap in (`scipy.stats.t`) if stricter rigor is needed later.

Run `python3 demo_phase6_experiment.py` for the real thing: 5 independent
runs × 3 algorithms on identical conditions, aggregated as mean ± std, saved
to `/tmp/shadownet_demo_results/` in the exact layout above. **The result is
worth reading, not just glancing at**: AODV wins on raw PDR (fits Phase 5's
finding about its global-discovery advantage), but **AEAR achieves higher
Emergency PDR than AODV** in the same run, while carrying zero routing
overhead versus AODV's 1000+ control-plane messages. That's a substantive,
defensible answer to "why AEAR despite AODV" — AEAR isn't trying to win on
aggregate throughput, it's trading some of that away specifically to protect
the traffic category that matters most, at a fraction of the control-plane
cost a LoRa duty cycle can actually afford.

## Phase 7 — Validation layer

```
backend/validation/
    issues.py    ValidationIssue (severity, code, message, evidence),
                 ValidationReport (is_valid, errors, warnings, summary())
    checks.py    individual check functions -- metrics-level (PDR bounds,
                 delivered<=generated, packet loss formula, non-negative
                 delay/hops, throughput consistency) and raw-event-log
                 audits (failed nodes/links never used, no revisited nodes,
                 TTL never negative, AEAR weights sum to 1, unresolved
                 failures flagged as a warning)
    engine.py    ValidationEngine.validate(event_log, metrics, duration) ->
                 ValidationReport, running every check and merging results
```

This is distinct from `MetricsEngine._validate()` (Phase 5), which hard-raises
on a handful of checks that are categorically impossible from correct math
(delivered > generated, say) — that's a bug-in-the-metrics-code signal and
should fail loudly. This layer is for auditing a *completed run* for
anomalies worth a human's attention without necessarily halting anything,
per the spec: "if invalid results occur, show a visible warning" — a
warning, not a crash. `ExperimentRunner` now attaches a `ValidationReport`
to every `RunResult` automatically, and it flows through to stored JSON via
`ResultStorage` with no extra wiring needed.

**This phase caught a real bug, not a hypothetical one.** Building the demo
against a scenario with a mid-run node failure/recovery, one AODV run came
back `INVALID` with `NODE_REVISITED` errors — a genuine data-plane loop
(`N14 -> N6 -> N14 -> N6 -> N3 -> N15`). The cause: AODV's routing-table
forwarding (`_route_data_packet`) looked up `next_hop` and forwarded to it
without ever checking whether *this specific packet* had already visited
that node — unlike AEAR/RSSI, which always filter candidates through
`packet.has_visited()`. Under route churn (exactly what a failure/recovery
cycle causes), two nodes' tables can transiently point back at each other —
a real, well-documented failure mode of distance-vector protocols during
reconvergence, not unique to this simulator. Fixed by adding the same
per-packet visited-node check to AODV's table-lookup forwarding path: if the
table's next hop was already visited by this packet, the route entry stays
valid for *other* packets (nothing is torn down), but this specific packet
falls back to buffering and a fresh route discovery instead of physically
looping. Verified with a 90-run sweep across multiple topologies, traffic
levels, and failure patterns afterward — all clean.

Run `python3 demo_phase7_validation.py` to see a real 9-run experiment
report as entirely clean, then a deliberately corrupted synthetic event log
that trips every check on purpose, to prove they actually fire.

## Phase 8 — REST API for Mode 2 (standard library only, no FastAPI)

```
backend/api/
    store.py       Store -- scenario CRUD (memory + JSON on disk), experiment
                   execution (sync or background thread), status/progress, and
                   reads of finished results straight from disk
    handlers.py    plain functions (store, path_params, body) -> (status, dict);
                   no HTTP in them
    server.py      route table + a thin stdlib ThreadingHTTPServer (CORS on)
    __main__.py    python3 -m backend.api [port]   (data goes in ./data/)
```

Run it: `python3 -m backend.api 8000`, then e.g. `curl localhost:8000/api/health`.
Or `python3 demo_phase8_api.py`, which starts a server in-process and uses it the
way the React frontend will.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health`, `/api/defaults` | liveness; paper-default scenario (to prefill a UI form) |
| POST/GET/PUT/DELETE | `/api/scenarios[/{id}]` | scenario CRUD |
| POST | `/api/experiments/run` | body `{"scenario": {...}}` or `{"scenario_id": ...}`. **Sync** (201 + metadata) by default; add `"async": true` for a **202** and a `status_url` |
| GET | `/api/experiments` | list, each with its `status` |
| GET | `/api/experiments/{id}/status` | `queued`/`running`/`saving`/`completed`/`failed` + per-algorithm progress |
| GET | `/api/experiments/{id}`, `/results`, `/metrics`, `/graphs` | metadata; per-run metrics + validation; aggregated mean/std/CI; chart-ready series |

Behaviours worth knowing:

- **Results before completion return 409**, not an error or partial data. A
  failed experiment reports its error in `status` and its result endpoints also 409.
- **Restart-safe**: finished experiments are read from disk, so they survive a
  restart. One that was mid-run when the process died is reported as
  `failed ("Interrupted...")` instead of staying `running` forever.
- **Bad input is a 400, never a 500** -- including wrong types
  (`"num_nodes": "abc"`), malformed nested fields, non-object bodies. Oversized
  bodies get 413.
- **Guard rails** (`Store(max_runs=100, max_nodes=200, max_duration_s=3600)`)
  reject a typo'd job up front; nothing is queued for a rejected scenario. They
  apply when *running* a scenario, not when saving one.
- **`saving` is a real phase**: after the last simulation finishes, writing raw
  events to disk takes several seconds, so progress can read 100% while the
  experiment is not yet readable. Poll `status`, not the progress numbers.

**Storage cost -- read this before running big experiments.** With raw events
saved (the default, since the spec requires raw storage), a paper-default
scenario (30 nodes, 180 s) writes about **15 MB per simulation run** --
measured: 4 runs x 3 algorithms = 178 MB and 9.5 s, against 3.5 s and a few KB
with `"save_raw_events": false`. At the paper's 10 runs expect roughly 450 MB
per experiment; AODV dominates because every route discovery is logged. Pass
`"save_raw_events": false` when you only need the metrics.

**Known limits, stated plainly**: the graphs endpoint covers the "metric vs
algorithm" charts only -- the spec's load-sweep graphs (PDR vs traffic load,
weight sensitivity) need a multi-experiment sweep and aren't built yet.
Scenarios are held in memory, written to disk on every create/update/delete, and
reloaded at startup -- there is no separate database. There is no
authentication and CORS is `*`: fine for a local research tool, not for a
public host.

## Phase 9 — WebSocket layer for Mode 1 (live event streaming)

```
backend/live/
    session.py       LiveSession -- one Network + SimulationEngine + one algorithm, with
                     send_packet / fail+restore node|link / step / advance / drain_events /
                     snapshot. No sockets in here; Phase 10 wraps this surface.
backend/ws/
    protocol.py      RFC 6455 server side, stdlib only: handshake, text frames, fragmentation,
                     ping/pong, close codes, size limit
    live_handler.py  JSON message layer: one connection = one LiveSession, plus the play loop
    client.py        tiny blocking client used by the tests and the demo
```

Connect to `ws://localhost:8000/ws/live` (same port as the REST API; `server.py` upgrades
that one path, every other route is untouched). Messages are JSON text frames:

| Client sends | Effect |
|---|---|
| `session.create` `{algorithm, scenario:{...ScenarioConfig fields}}` | builds network + engine + router; replies `session.created` with the full network (nodes, links, RSSI) |
| `packet.send` `{source, destination, packet_type, ttl?}` | schedules a packet at the current sim time; ack carries `packet_id` |
| `failure.node_fail` / `node_restore` `{node}`, `failure.link_fail` / `link_restore` `{node_a, node_b}` | injects/clears a failure at the current sim time |
| `sim.step` `{count}` | processes `count` scheduled events, then streams what they produced |
| `sim.play` `{speed}` / `sim.pause` | free-run at `speed` simulated seconds per wall-clock second (0.01-1000) |
| `sim.reset` | rebuilds the same session from scratch |
| `state.get`, `ping` | snapshot / keep-alive |

The server sends `hello`, `ack` (echoes your `request_id`), `error`, `state`, and `events`:
`{"type": "events", "events": [...], "state": {...}}`. The events are the engine's own
events, unmodified, in log order -- `CANDIDATE_EVALUATED` already carries RSSI, normalized
factors, weights and score for every candidate, so the AEAR decision panel (Phase 13) needs
no extra endpoint. `state` is a compact snapshot (node status/queue, failed links, every
packet's status/position/path) so the canvas can redraw from any single message.

Things worth knowing:

- **A "step" is one *scheduled* event, not one log line.** Processing `PACKET_GENERATED` runs
  the router's whole decision synchronously, so one step streams `PACKET_GENERATED`,
  `NEIGHBOR_DISCOVERED`, every `CANDIDATE_EVALUATED`, `FORWARDING_DECISION` and
  `PACKET_FORWARDED` together. The UI can still replay them one at a time from the batch.
- **Same simulation as Mode 2**: a session is built through `ExperimentRunner`'s own network
  and router builders, and a test checks its event stream is identical to a hand-built engine
  run. Same seed => same topology as run 1 of a Mode 2 experiment.
- **Simulated time keeps running while idle** during `sim.play`, so actions are stamped at the
  playhead (which can be far ahead of the last event). Use `sim.step` for exact control.
- **Rejected commands change nothing** (no half-scheduled events) and never close the
  connection: unknown nodes, source == destination, a failed source, double-failing a node,
  failing a link that doesn't exist, bad types, bad JSON all come back as `error`.
- **Limits**: 100 nodes per live session, 8 concurrent connections (extra ones are closed
  with 1013), 1 MB per message, at most 5000 events per advance.
- **Known limits, stated plainly**: no authentication and no Origin check, so any web page
  open in your browser could connect to a locally running server -- fine for a local research
  tool, not for a shared host. One algorithm per session; comparing algorithms live means two
  connections. (Packet IDs were once process-global and collided between sessions; fixed in
  Phase 16 -- each engine now numbers its own packets.)

## Phase 10 — Communication adapter abstraction

```
backend/adapters/
    base.py        CommunicationAdapter (ABC), AdapterCapabilities, AdapterError /
                   AdapterUnavailable / UnsupportedOperation
    simulator.py   SimulatorAdapter -- wraps Phase 9's LiveSession; the only working adapter
    lora.py        LoRaAdapter -- a STUB (see below)
    registry.py    create_adapter(kind, scenario, algorithm), register_adapter(), list_adapters()
```

The Mode 1 message layer now talks only to `CommunicationAdapter`; it never imports
`LiveSession`. `session.create` takes an optional `"adapter"` (default `"simulator"`), and
`session.created` now includes an `"adapter"` object with the adapter's capabilities, so the
UI can grey out controls instead of guessing. `GET /api/adapters` lists every adapter and
whether it is available.

**Capabilities are the point.** A real radio has no simulated clock (nothing to step or
speed up), can't sever a link on demand, and won't emit the simulator's candidate-scoring
trace unless you build that. So each adapter declares `simulated_time`, `failure_injection`,
`decision_trace` and its `algorithms`, and the message layer answers `sim.step`, `sim.play`
and `failure.*` with a clear `error` when the adapter lacks the capability -- never a fake.
A test proves this with a hardware-shaped adapter.

**The LoRa adapter is a stub and says so.** There is no hardware integration in this project
(budget not approved), so `LoRaAdapter` cannot be constructed: asking for it returns
"The LoRa adapter is a stub: no hardware integration exists yet". It is registered so the
extension point is real, and its docstring lists what implementing it involves. The
interface is shaped by what the simulator needed, not by a real radio driver, so expect to
adjust it (probably adding async event delivery and connection lifecycle) when actual
hardware arrives -- don't treat it as a finished hardware contract.

Other notes: a failed `session.create` (unknown or unavailable adapter, bad scenario)
leaves the connection's existing session running untouched; `sim.reset` reuses the
adapter, algorithm and scenario from the last successful create.

## Phase 11 — Frontend app shell

```
frontend/
    vite.config.js   dev server on :5173, proxies /api and /ws (WebSocket) to the backend on :8000
    src/
        main.jsx, App.jsx    router + layout; the PAGES table is the single list of pages
        api.js               fetch wrapper (clear "backend not running" error), liveSocketUrl()
        useBackend.js        useBackendStatus() (polls /api/health), useApi(path)
        styles.css           dark theme as CSS variables
        pages/Dashboard.jsx  backend status, adapters, recent experiments, roadmap
        pages/Placeholder.jsx  "arrives in Phase N" pages for the five not-yet-built screens
```

Run it (two terminals):

```bash
# 1. backend
cd shadownet && python3 -m backend.api 8000

# 2. frontend (needs Node 18+)
cd shadownet/frontend && npm install && npm run dev      # http://localhost:5173
```

Stack choices, made without a spec to follow: plain JavaScript (not TypeScript), plain CSS
with variables (no Tailwind), no chart library yet (Phase 12 adds one when there is
something to chart). The browser only talks to the Vite origin, so there is no CORS
configuration and no hard-coded backend port in the code -- change `BACKEND` in
`vite.config.js` if you run the API elsewhere. `npm run build` produces a static `dist/`,
but nothing serves it yet; that is part of the Phase 16 integration pass.

What is real vs placeholder: the shell, navigation, theme, health indicator and Dashboard
(live data from `/api/health`, `/api/adapters`, `/api/experiments`) are real. Experiments,
Live Network, Scenarios, Algorithms and Developer are stubs that say which phase builds
them. The Dashboard roadmap table is hand-maintained. Verified: `npm run build` succeeds,
and REST and WebSocket both work through the dev proxy. Not verified: how it looks in a
real browser -- I could only build it, not open it.

## Phase 12 — Mode 2 UI

```
frontend/src/
    pages/Experiments.jsx        scenario form (prefilled from /api/defaults) + past experiments list
    pages/ExperimentDetail.jsx   /experiments/:id -- progress -> results (table, charts, export)
    format.js                    number formatting, CSV building, browser download
```

Flow: fill the form -> `POST /api/experiments/run` with `async: true` -> the page polls
`/status` every second and shows one progress bar per algorithm (including the `saving`
phase) -> on `completed` it loads `/metrics`, `/graphs`, `/results` and the metadata.

- **Summary table**: mean ± std per algorithm for the headline metrics (toggle for every
  metric). The best mean is bold where a direction is defined (higher PDR, lower delay...).
  This is *not* a significance test; with few runs, read the ± before trusting a gap.
- **Charts** (Recharts): PDR, emergency PDR, delay, emergency delay, hops, throughput --
  bars are means, whiskers ±1 std. Metrics the scenario didn't collect (e.g. queue length
  with the sampler off) show "Not collected" rather than an empty chart.
- **Validation warning**: if any run failed the Phase 7 validators, a visible banner lists
  them (spec requirement). It is shown above the results, not buried.
- **Export**, all client-side from data already fetched: aggregated CSV (algorithm, metric,
  mean, std, CI low/high, n), per-run CSV (scalar metrics only; per-priority breakdowns are
  in the JSON), and full JSON (metadata + aggregated + every run).
- **Raw events are OFF by default** in the form (see the storage warning under Phase 8);
  tick the box only if you need them.

Not in this phase (deliberately): choosing a saved scenario (Phase 14's Scenario Manager),
editing failure events, custom AEAR weights and the queue sampler (the form keeps whatever the
defaults contain), per-priority charts, and cancelling a running experiment (the API has no
cancel). Verified: `npm run build` passes and every API shape the pages read was checked
against a real run. **Not verified in a browser** -- I can build but not look at it, so layout
and chart rendering are untested by eye. The bundle is ~500 kB+ (Recharts); fine locally.

## Phase 13 — Mode 1 UI (Live Network)

```
frontend/src/
    pages/Live.jsx             page: session panel, controls, canvas, packets, decision panel, event log
    live/useLiveSession.js     WebSocket hook: connection state, session, latest snapshot, events (last 3000), errors
    live/NetworkCanvas.jsx     SVG map of nodes, links, route, candidates, failures
    live/decisions.js          pure: raw events -> one "decision" per (packet, node) hop; event colours/summaries
```

What it does: create a session (algorithm, adapter, nodes, seed, range) -> pick From/To (dropdowns
or click a node on the map) -> send a packet with a priority and TTL -> **Step**, **Step x10** or
**Play** at 0.25x-100x -> watch it move. **Failures**: select a node and Fail/Restore it, or fail a
link by choosing its two end nodes. Controls are greyed out with an explanation when the adapter
lacks a capability (Phase 10), and server errors show in a dismissible banner.

- **Map legend**: blue = route taken so far; purple = the selected next hop of the decision being
  shown; dashed grey = the other candidates (more opaque = higher score); red dashed = failed link;
  red node = failed, amber = congested; green ring = where the selected packet currently is; S/D mark
  its source and destination.
- **Decision panel** (the AEAR panel): step through every hop decision of the selected packet
  (click a packet row, or a log row, to change packet). For AEAR it lists each candidate with score,
  RSSI, estimated hops, queue fill, the three normalised factors and the weights used for that
  priority. RSSI shows score and RSSI only. **AODV has no candidate scoring** -- the panel shows the
  route-table decision it reported instead, rather than inventing a table.
- **Event log**: newest first, last 200 rows, candidate/neighbour events hidden by default (tick to
  show), freeze checkbox, click a row to select its packet.

Limits worth knowing: the browser keeps the last 3000 events (older ones scroll out of the log and
decision history, not out of the server); one session per page load, and a page refresh loses it
(sessions live on the server connection); packet trails redraw from snapshots, not animated frames;
link failure needs two dropdowns, not a click on the link; the panel shows the router's own numbers
and does not recompute the score. **Verified**: `npm run build` passes, and a script drove a real
session over the WebSocket and confirmed every field the page reads exists (network, adapter
capabilities, snapshot status/failed links/packet path, error and ack messages), and that the
decision grouping matches the router's own candidate counts on real events. **Not verified**: how
it looks or feels in a browser, and Play at high speed under a real render loop.

## Phase 14 — Scenario Manager, Algorithm Explorer, Developer pages

```
frontend/src/
    ScenarioFields.jsx       shared scenario editor fields (now used by Experiments AND Scenario Manager)
    algorithms.js            JS mirror of the AEAR scoring formula + default weights
    pages/Scenarios.jsx      list / create / edit / delete saved scenarios; Run a saved scenario
    pages/Algorithms.jsx     how AEAR, RSSI and AODV decide, plus a "score a hop" calculator
    pages/Developer.jsx      API console + per-run validation report viewer
```

- **Scenario Manager**: uses the existing `/api/scenarios` CRUD. "New" starts from the paper
  defaults; "Run" starts an async experiment from the saved scenario (raw events off) and jumps to
  its progress page. Only the fields shown in the form are editable; anything else stored in a
  scenario (failure events, AEAR weights, queue sampler) is preserved on edit, not shown. Saving
  does not apply the run-size guard rails; running does. Scenarios are not yet selectable from the
  Experiments page's own form (there it always starts from the defaults).
- **Algorithm Explorer**: static explanations written from the router code, the default weight
  table per priority, and a calculator: enter candidates (RSSI, estimated hops, queue, capacity),
  pick a priority or edit the weights, and see S/H/C/score, AEAR's pick and the RSSI baseline's pick
  side by side. It flags weights that don't sum to 1 (the router rejects those). Worth knowing: when
  every candidate ties on a factor the router normalises to 1.0, so H becomes 0 for all of them.
  **Parity**: the JS mirror was checked against the real router on 731 candidates / 82 decisions --
  identical winner every time; scores differ by up to ~0.001, purely because events round RSSI to 2
  decimals. The router stays the source of truth.
- **Developer / Debug**: a console for any `/api` call (real requests -- POST/PUT/DELETE change
  data) with status and timing, one-click GETs, and a validation viewer that lists every run of a
  finished experiment with its validity, summary and issues. **Not done**: a viewer for raw
  per-event data. Raw events are saved to disk only when requested and no endpoint serves them yet.
- The Phase 11 placeholder page component was removed; every page in the sidebar is now real.

Verified: `npm run build`; the scenario create/list/get/update/delete/run-from-saved flow and the
validation fields the viewer reads, against a real server; the ratio-sum error reaches the form as
readable text. Not verified: how the pages look in a browser.

## Phase 15 — Tests

```bash
python3 -m pytest tests -q                 # backend: 294 tests, ~15 s
cd frontend && npm test                    # frontend: 28 tests, ~2 s
python3 scripts/make_frontend_fixtures.py  # regenerate frontend fixtures after changing a router
```

New in this phase:

| File | What it checks |
|---|---|
| `tests/test_phase15_invariants.py` | Whole runs, all 3 algorithms x 5 scenarios (stable, congestion, emergency burst, node failures, link failure). Properties computed **independently from the raw event stream**, not from the validator: a packet is delivered at most once and terminal states are final; nothing is forwarded from/to a failed node or over a failed link; hop count + TTL remaining = TTL; event log and time are ordered; metrics equal what the events say; all algorithms see identical traffic; runs are reproducible; every run also passes `ValidationEngine`. It also asserts the failure/congestion scenarios really produce failures/congestion, so they can't silently test nothing. |
| `tests/test_phase15_regressions.py` | The AODV defect below (one test per fix, each verified to fail when its fix is removed), the runner's runaway guard, all four failure-schedule actions, and API error paths that were untested (404s on every unknown id, invalid update leaves the stored scenario intact, malformed/oversized body, CORS preflight, a runaway async experiment ending as `failed` with the reason). |
| `tests/test_phase15_fixtures.py` | Fails when the routers' output no longer matches the fixtures the frontend tests use, so the JavaScript scoring mirror cannot drift silently. |
| `frontend/src/__tests__/*.test.js` | Vitest. The JS AEAR mirror against 488 real candidates (same winner in every decision, factors exact, scores within the router's rounding) plus edge cases (ties, single candidate, zero capacity, weights); decision grouping on real AEAR/RSSI/AODV streams and synthetic interleaved ones; number/CSV formatting. |

Coverage of `backend/`: **95%**. The only uncovered code is the `python -m backend.api` entry point
and the test client's error paths.

### Defect found and fixed: AODV could hang, then run out of memory, when a node failed

Found by the new failure-scenario invariants. With two nodes failed at t=5 s (20 nodes, seed 101),
the AODV run never finished: one packet went round buffer -> route discovery -> instant reply ->
"the route leads back to a node this packet already visited" -> buffer... about 39,000 times at one
simulated instant, then out of memory. In a Mode 2 experiment this would have frozen or killed the
backend. AEAR and RSSI were unaffected. Two changes, in `backend/simulation/routing/aodv.py`:

1. **Root cause**: when a node fails, routes *to* it are now invalidated network-wide, not only routes
   *through* it. Measured: this alone stops the hang. What I did **not** fully trace is where the
   stale entries came from originally; the fix removes the symptom on the evidence that
   invalidating them is sufficient, and a test pins that the safety net below never fires here.
2. **Safety net**: a packet whose only known route loops back is dropped
   (`DROPPED_NO_CANDIDATE`) after `max_loop_fallbacks` (5) consecutive attempts at the same
   simulated instant. The counter resets when time advances, so legitimate spread-out retries
   are unaffected.

Also new: `ExperimentRunner.MAX_EVENTS_PER_RUN` (2,000,000). A run that exceeds it while events are
still due raises `SimulationRunaway`; the experiment ends `failed` with that message and stores no
results. It never silently truncates. Legitimate runs here use ~10^4-10^6 events.

Effect on earlier results: none for scenarios without node failures. I fingerprinted every raw
event of 27 runs (paper defaults, congestion, burst, link failure) before and after: all 27 are
byte-identical. **Any AODV numbers you produced from a scenario that fails a node may differ** and
should be re-run before you quote them. (That only applies if such a run finished; the ones that
hit this bug would not have.)

Other findings, not changed:
- `fmt(100, 0)` in the frontend returned "1" (fixed; no page called it that way).
- A failure schedule naming a link that does not exist in a run's topology aborts that run with
  "No link between ...". Topologies differ per run (seed), schedules are fixed. Pinned by a test.

Not covered, honestly: there are **no React component or browser end-to-end tests** (the pages were
never rendered in a browser here), no load/soak tests, and the WebSocket layer's concurrency limits
are tested only lightly. I did not have your spec's section 34, so these tests follow the code and
the paper's behaviour, not a checklist from the spec. Compare them against that section before you
claim spec compliance.

## Design decisions worth knowing about

- **Reproducibility**: `Network.generate_random(seed=...)` uses one seeded
  RNG for both node placement and RSSI shadowing noise, so the same seed
  always produces the same topology and link qualities. This is required
  for the "everything except the algorithm stays identical" fair-comparison
  rule later.
- **RSSI is simulated, not measured**: `PathLossModel` in `link.py` is a
  standard log-distance path-loss formula with configurable exponent and
  shadowing — clearly separated so it's never confused with real hardware
  data, per the project's own constraint about labeling simulation
  assumptions honestly.
- **Packet IDs are per engine** (since Phase 16): each `SimulationEngine` numbers its own packets
  P001, P002, ... so IDs are comparable across runs/algorithms and independent engines (experiment
  runs, Live sessions) can't interfere. `reset_packet_id_counter()` still exists, but only affects
  `Packet`s constructed directly without an ID (mostly tests); the engine no longer uses it.
- **Congestion is queue-driven**: a node's `CONGESTED` display status is
  purely `queue_occupancy / capacity` — this is separate from (but will
  feed into) AEAR's congestion-availability factor `C_j` in Phase 3.

## Running it

See **Quick start** at the top. Demos (no pytest needed): `python3 demo_phase1.py` ... `python3 demo_phase10_adapters.py`
(one per phase; each prints what it shows). The backend needs only the Python standard library.

## Project layout

```
shadownet/
    backend/
        simulation/   network, packets, queues, events, engine, routing (AEAR, RSSI, AODV), failures, metrics
        scenarios/    ScenarioConfig
        experiments/  traffic, stats, runner (ExperimentRunner, SimulationRunaway), storage
        validation/   post-run integrity checks
        adapters/     CommunicationAdapter, SimulatorAdapter, LoRaAdapter (stub), registry
        live/         LiveSession (Mode 1)
        ws/           RFC 6455 protocol, live message layer, test client
        api/          store, handlers, server (REST + WebSocket upgrade + static UI), static.py, __main__ launcher
    frontend/         Vite + React; src/pages, src/live, src/__tests__, src/__fixtures__; dist/ = built UI
    tests/            backend tests (per phase, plus phase15 invariants/regressions/fixtures, phase16 static/packet ids)
    scripts/          make_frontend_fixtures.py
    demo_phase*.py    one runnable demo per phase
    data/             created at runtime: saved scenarios and experiment results
```
