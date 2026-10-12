export const fmt = (v, d = 3) => {
  if (v === null || v === undefined) return "—";
  const s = Number(v).toFixed(d);
  // strip trailing zeros only after a decimal point ("100" must stay "100"); "-0" is just "0"
  const t = s.includes(".") ? s.replace(/\.?0+$/, "") : s;
  return t === "-0" || t === "" ? "0" : t;
};

export function download(name, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

const csvCell = (v) => (v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v);
export const toCsv = (rows) => rows.map((r) => r.map(csvCell).join(",")).join("\n") + "\n";
