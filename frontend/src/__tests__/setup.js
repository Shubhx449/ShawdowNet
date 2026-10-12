// Browser APIs jsdom lacks, and network doubles. Nothing here talks to a real server.
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }; // Recharts
window.HTMLAnchorElement.prototype.click = function () { window.__lastDownload = { name: this.download, href: this.href }; };
