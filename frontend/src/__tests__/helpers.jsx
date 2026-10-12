import { vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App.jsx";

// routes: [[method, RegExp|string, data | (body)=>[status, data]], ...]; first match wins. Unmatched = loud failure.
export function mockApi(routes) {
  const calls = [];
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const method = opts.method || "GET";
    const path = String(url).replace(/^\/api/, "");
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ method, path, body });
    for (const [m, pattern, data] of routes) {
      if (m !== method) continue;
      if (typeof pattern === "string" ? pattern !== path : !pattern.test(path)) continue;
      const [status, payload] = typeof data === "function" ? data(body, path) : [200, data];
      return { ok: status < 400, status, json: async () => payload, text: async () => JSON.stringify(payload) };
    }
    throw new Error(`Unmocked request: ${method} ${path}`);
  });
  return calls;
}

export function renderApp(path) {
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

// A scriptable WebSocket: the test plays the server.
export class FakeWebSocket {
  static OPEN = 1;
  static last = null;
  constructor(url) {
    this.url = url; this.sent = []; this.readyState = 0; FakeWebSocket.last = this;
    setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 0);
  }
  send(m) { this.sent.push(JSON.parse(m)); }
  close() { this.readyState = 3; }
  receive(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
}
