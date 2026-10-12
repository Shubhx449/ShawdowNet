import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FakeWebSocket, mockApi, renderApp } from "./helpers.jsx";
import defaults from "../__fixtures__/api_defaults.json";
import adapters from "../__fixtures__/api_adapters.json";
import scenarios from "../__fixtures__/api_scenarios.json";
import experiments from "../__fixtures__/api_experiments.json";
import statusDone from "../__fixtures__/api_status_completed.json";
import metrics from "../__fixtures__/api_metrics.json";
import graphs from "../__fixtures__/api_graphs.json";
import results from "../__fixtures__/api_results.json";
import experiment from "../__fixtures__/api_experiment.json";
import wsCreated from "../__fixtures__/ws_session_created.json";
import wsEvents from "../__fixtures__/ws_events.json";

const EID = experiments.experiments[0].id;
const base = () => [
  ["GET", "/health", { status: "ok" }], ["GET", "/defaults", defaults], ["GET", "/adapters", adapters],
  ["GET", "/scenarios", scenarios], ["GET", "/experiments", experiments],
];
const detailRoutes = (status = statusDone, res = results) => [
  ["GET", `/experiments/${EID}/status`, status], ["GET", `/experiments/${EID}/metrics`, metrics],
  ["GET", `/experiments/${EID}/graphs`, graphs], ["GET", `/experiments/${EID}/results`, res], ["GET", `/experiments/${EID}`, experiment],
];

describe("shell and Dashboard", () => {
  it("renders every sidebar page link and the live backend status", async () => {
    mockApi(base());
    renderApp("/");
    for (const name of ["Dashboard", "Experiments", "Live Network", "Scenarios", "Algorithms", "Developer"]) {
      expect(within(document.querySelector("nav")).getByRole("link", { name: new RegExp(name) })).toBeTruthy();
    }
    await waitFor(() => expect(document.querySelector(".status").textContent).toMatch(/backend online/));
  });
  it("shows adapters (LoRa as an unavailable stub), the latest experiment as a link, and a finished roadmap", async () => {
    mockApi(base());
    renderApp("/");
    expect(await screen.findByText(/not available \(stub\)/)).toBeTruthy();
    expect(screen.getByRole("link", { name: EID }).getAttribute("href")).toBe(`/experiments/${EID}`);
    const rows = [...document.querySelectorAll(".table tr")];
    expect(rows.length).toBe(10);
    expect(rows.every((r) => /done$/.test(r.textContent)), "roadmap should be all done").toBe(true);
  });
  it("says so plainly when the backend is down", async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError("fetch failed"); });
    renderApp("/");
    await waitFor(() => expect(document.querySelector(".status").textContent).toMatch(/backend offline/));
    expect((await screen.findAllByText(/Cannot reach the backend/)).length).toBeGreaterThan(0);
  });
  it("unknown URL shows the not-found page", () => {
    mockApi(base());
    renderApp("/nope/at/all");
    expect(screen.getByText("Page not found")).toBeTruthy();
  });
});

describe("Experiments (Mode 2)", () => {
  it("prefills from the server defaults and validates the ratio sum live", async () => {
    mockApi(base());
    renderApp("/experiments");
    const nodes = await screen.findByLabelText("Nodes");
    expect(Number(nodes.value)).toBe(defaults.num_nodes);
    expect(screen.getByText(/Ratios sum to 1\.00/)).toBeTruthy();
    const normal = screen.getByLabelText(/Normal ratio/);
    await userEvent.clear(normal); await userEvent.type(normal, "0.9");
    expect(screen.getByText(/Ratios sum to 1\.\d\d/).className).toBe("error");
  });
  it("cannot be submitted with no algorithm selected", async () => {
    mockApi(base());
    renderApp("/experiments");
    await screen.findByLabelText("Nodes");
    for (const a of ["AEAR", "RSSI", "AODV"]) await userEvent.click(screen.getByLabelText(a));
    expect(screen.getByRole("button", { name: /Run experiment/ }).disabled).toBe(true);
  });
  it("submits the scenario asynchronously with raw events off, then opens the progress page", async () => {
    const calls = mockApi([...base(), ["POST", "/experiments/run", () => [202, { experiment_id: EID, status: "queued" }]],
      ...detailRoutes({ experiment_id: EID, status: "running", scenario_name: "x", error: null, progress: { AEAR: { completed: 1, total: 2 }, RSSI: { completed: 0, total: 2 } } })]);
    renderApp("/experiments");
    await userEvent.click(await screen.findByRole("button", { name: /Run experiment/ }));
    const post = calls.find((c) => c.method === "POST");
    expect(post.body.async).toBe(true);
    expect(post.body.save_raw_events).toBe(false);
    expect(post.body.scenario.num_nodes).toBe(defaults.num_nodes);
    expect(await screen.findByText(/Progress/)).toBeTruthy(); // navigated to /experiments/:id
    expect(screen.getByText("1/2")).toBeTruthy();
  });
  it("shows the server's rejection reason instead of failing silently", async () => {
    mockApi([...base(), ["POST", "/experiments/run", () => [400, { error: "Traffic ratios must sum to 1.0" }]]]);
    renderApp("/experiments");
    await userEvent.click(await screen.findByRole("button", { name: /Run experiment/ }));
    expect(await screen.findByText("Traffic ratios must sum to 1.0")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Run experiment/ }).disabled).toBe(false); // can retry
  });
});

describe("Experiment results", () => {
  it("renders the summary table with all three algorithms and real numbers", async () => {
    mockApi([...base(), ...detailRoutes()]);
    renderApp(`/experiments/${EID}`);
    const table = await screen.findByRole("table", {}, { timeout: 3000 });
    const head = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(head).toEqual(["Metric", "AEAR", "RSSI", "AODV"]); // the server's order: proposed algorithm first, then baselines
    const pdr = within(table).getByText("PDR (%)").closest("tr");
    expect(pdr.textContent).toMatch(/\d/);
    expect(pdr.textContent).toContain("±");
    expect(document.querySelectorAll(".chart").length).toBe(6);
  });
  it("marks the best mean per row; algorithms that tie are all marked, and never a worse one", async () => {
    mockApi([...base(), ...detailRoutes()]);
    renderApp(`/experiments/${EID}`);
    await screen.findByRole("table", {}, { timeout: 3000 });
    const num = (td) => parseFloat(td.textContent);
    let rowsWithBest = 0;
    for (const tr of document.querySelectorAll(".results tbody tr")) {
      const best = [...tr.querySelectorAll("td.best")];
      if (!best.length) continue;
      rowsWithBest++;
      expect(new Set(best.map((td) => td.textContent.split("±")[0].trim())).size, `tied cells differ in ${tr.textContent}`).toBe(1);
      const all = [...tr.querySelectorAll("td:not(:first-child)")].map(num).filter((v) => !Number.isNaN(v));
      const lowerIsBetter = /delay|hops|loss|control|duplicate/i.test(tr.firstChild.textContent);
      expect(num(best[0])).toBe(lowerIsBetter ? Math.min(...all) : Math.max(...all));
    }
    expect(rowsWithBest).toBeGreaterThan(3);
  });
  it("shows a visible warning when any run failed validation", async () => {
    const bad = JSON.parse(JSON.stringify(results));
    const run = Object.values(bad.results)[0][0];
    run.validation = { is_valid: false, summary: "1 error(s): failed_node_forwarding", issues: [{ code: "failed_node_forwarding" }] };
    mockApi([...base(), ...detailRoutes(statusDone, bad)]);
    renderApp(`/experiments/${EID}`);
    expect(await screen.findByText(/failed validation/, {}, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByText(/failed_node_forwarding/)).toBeTruthy();
  });
  it("shows no warning when every run is valid", async () => {
    mockApi([...base(), ...detailRoutes()]);
    renderApp(`/experiments/${EID}`);
    await screen.findByRole("table", {}, { timeout: 3000 });
    expect(screen.queryByText(/failed validation/)).toBeNull();
  });
  it("exports an aggregated CSV that has a header and one row per metric per algorithm", async () => {
    let blob;
    URL.createObjectURL = (b) => { blob = b; return "blob:test"; };
    URL.revokeObjectURL = () => {};
    mockApi([...base(), ...detailRoutes()]);
    renderApp(`/experiments/${EID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Aggregated CSV" }, { timeout: 3000 }));
    const text = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsText(blob); }); // jsdom's Blob has no .text()
    const lines = text.trim().split("\n");
    expect(lines[0]).toBe("algorithm,metric,mean,std,ci95_low,ci95_high,n");
    const perAlg = Object.keys(metrics.AEAR).length;
    expect(lines.length - 1).toBe(3 * perAlg);
    expect(window.__lastDownload.name).toBe(`${EID}_aggregated.csv`);
  });
  it("shows the failure reason of a failed experiment and no results", async () => {
    mockApi([...base(), ...detailRoutes({ experiment_id: EID, status: "failed", scenario_name: "x", error: "processed 2,000,000 events: probable routing loop", progress: { AEAR: { completed: 0, total: 2 } } })]);
    renderApp(`/experiments/${EID}`);
    expect(await screen.findByText(/probable routing loop/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
  it("reports an unknown experiment id", async () => {
    mockApi([...base(), ["GET", "/experiments/zzz/status", () => [404, { error: "Experiment 'zzz' not found" }]]]);
    renderApp("/experiments/zzz");
    expect(await screen.findByText(/not found/)).toBeTruthy();
  });
});

describe("Live Network (Mode 1)", () => {
  async function openSession() {
    globalThis.WebSocket = FakeWebSocket;
    mockApi(base());
    renderApp("/live");
    await waitFor(() => expect(FakeWebSocket.last.readyState).toBe(1));
    await userEvent.click(await screen.findByRole("button", { name: /Create session/ }));
    const ws = FakeWebSocket.last;
    expect(ws.sent[0]).toMatchObject({ type: "session.create", algorithm: "AEAR", adapter: "simulator" });
    act(() => ws.receive(wsCreated));
    return ws;
  }
  it("draws one circle per node and one line per link once the session exists", async () => {
    await openSession();
    const svg = document.querySelector("svg.canvas");
    expect(svg.querySelectorAll("g circle").length).toBeGreaterThanOrEqual(wsCreated.network.nodes.length);
    expect(svg.querySelectorAll("line").length).toBeGreaterThanOrEqual(wsCreated.network.links.length);
  });
  it("sends the packet the user configured, and renders decisions, candidates and the packet from real server messages", async () => {
    const ws = await openSession();
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(ws.sent.at(-1)).toMatchObject({ type: "packet.send", source: "N1", destination: "N2", packet_type: "EMERGENCY", ttl: 12 });
    act(() => ws.receive(wsEvents));
    expect(await screen.findByText(/Routing decision — P001 at N\d+/)).toBeTruthy(); // opens on the packet's latest hop
    expect(document.querySelector("table.cand tr.picked")).toBeTruthy();        // the selected candidate is highlighted
    const back = screen.getByRole("button", { name: "◀" });
    while (!back.disabled) await userEvent.click(back);                         // to the first hop, where N1 had many neighbours
    expect(screen.getByText(/Routing decision — P001 at N1/)).toBeTruthy();
    expect(document.querySelectorAll("table.cand tbody tr").length).toBeGreaterThan(1);
    expect(screen.getByText(/hop decision \d+ \/ \d+/)).toBeTruthy();
    expect(screen.getByRole("cell", { name: "P001" })).toBeTruthy();            // packets table
    expect(document.querySelectorAll(".log-row").length).toBeGreaterThan(3);    // event log
  });
  it("steps through a packet's hop decisions in order", async () => {
    const ws = await openSession();
    act(() => ws.receive(wsEvents));
    await screen.findByText(/Routing decision — P001/);
    const total = Number(screen.getByText(/hop decision/).textContent.match(/\/ (\d+)/)[1]);
    expect(total).toBeGreaterThan(1);
    await userEvent.click(screen.getByRole("button", { name: "◀" }));
    expect(screen.getByText(/hop decision \d+ \//).textContent).toContain(`${total - 1} /`);
  });
  it("greys out time and failure controls when the adapter lacks those capabilities", async () => {
    const ws = await openSession();
    act(() => ws.receive({ ...wsCreated, adapter: { ...wsCreated.adapter, kind: "radio", simulated_time: false, failure_injection: false } }));
    expect(await screen.findByText(/no simulated clock/)).toBeTruthy();
    expect(screen.getByText(/cannot inject failures/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Step" })).toBeNull();
  });
  it("shows a server error in a dismissible banner", async () => {
    const ws = await openSession();
    act(() => ws.receive({ type: "error", request_id: null, error: "Unknown node: 'N999'" }));
    expect(await screen.findByText(/Unknown node/)).toBeTruthy();
    await userEvent.click(screen.getByText(/Unknown node/));
    await waitFor(() => expect(screen.queryByText(/Unknown node/)).toBeNull());
  });
  it("offers a reconnect when the socket closes", async () => {
    globalThis.WebSocket = FakeWebSocket;
    mockApi(base());
    renderApp("/live");
    await waitFor(() => expect(FakeWebSocket.last.readyState).toBe(1));
    act(() => FakeWebSocket.last.onclose());
    expect(await screen.findByRole("button", { name: "Reconnect" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Create session/ }).disabled).toBe(true);
  });
});

describe("Scenario Manager", () => {
  it("lists saved scenarios, opens an editor from the defaults, and saves a new one", async () => {
    const calls = mockApi([...base(), ["POST", "/scenarios", () => [201, { id: "new1" }]]]);
    renderApp("/scenarios");
    expect(await screen.findByText(scenarios.scenarios[0].name)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "New scenario" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(calls.find((c) => c.method === "POST" && c.path === "/scenarios").body.num_nodes).toBe(defaults.num_nodes);
  });
  it("asks before deleting and deletes the right scenario", async () => {
    const id = scenarios.scenarios[0].id;
    const calls = mockApi([...base(), ["DELETE", `/scenarios/${id}`, { deleted: id }]]);
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderApp("/scenarios");
    const del = await screen.findByRole("button", { name: "Delete" });
    await userEvent.click(del);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false); // declined
    await userEvent.click(del);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === `/scenarios/${id}`)).toBe(true));
  });
  it("runs a saved scenario by id", async () => {
    const id = scenarios.scenarios[0].id;
    const calls = mockApi([...base(), ["POST", "/experiments/run", () => [202, { experiment_id: EID }]], ...detailRoutes()]);
    renderApp("/scenarios");
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    expect(calls.find((c) => c.method === "POST").body).toMatchObject({ scenario_id: id, async: true, save_raw_events: false });
  });
});

describe("Algorithm Explorer and Developer", () => {
  it("calculator: AEAR and the RSSI baseline can disagree, and it says so", () => {
    mockApi(base());
    renderApp("/algorithms");
    const line = screen.getByText(/AEAR picks/).parentElement.textContent;
    expect(line).toMatch(/AEAR picks\s*\w+\s*·\s*RSSI baseline picks\s*\w+/);
    expect(line).toMatch(/disagree|same choice/);
  });
  it("calculator: flags weights that do not sum to 1", async () => {
    mockApi(base());
    renderApp("/algorithms");
    const w = screen.getByLabelText("w hop");
    await userEvent.clear(w); await userEvent.type(w, "0.9");
    expect(screen.getByText(/router requires exactly 1\.00/)).toBeTruthy();
  });
  it("developer console shows status and body of an API call, including errors", async () => {
    mockApi([...base(), ["GET", "/nope", () => [404, { error: "No route" }]]]);
    renderApp("/developer");
    await userEvent.clear(screen.getByDisplayValue("/health")); await userEvent.type(screen.getByRole("textbox"), "/nope");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/HTTP 404/)).toBeTruthy();
    expect(screen.getByText(/No route/)).toBeTruthy();
  });
});
