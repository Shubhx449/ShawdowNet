// Shared scenario editor fields, used by the Experiments page and the Scenario Manager.
// Only edits the fields listed here; anything else in the scenario (failure events, AEAR weights,
// queue sampler...) is carried through untouched because callers spread the full scenario object.
export const ALGS = ["AEAR", "RSSI", "AODV"];
const FIELDS = [
  ["num_nodes", "Nodes", 1], ["num_runs", "Runs per algorithm", 1], ["simulation_duration_s", "Duration (s)", 1],
  ["traffic_rate_pps", "Traffic (packets/s)", 0.1], ["communication_range_m", "Range (m)", 10], ["ttl", "TTL", 1],
  ["base_seed", "Base seed", 1], ["emergency_burst_count", "Emergency burst packets", 1],
  ["emergency_burst_start_s", "Burst start (s)", 1], ["emergency_burst_end_s", "Burst end (s)", 1],
];
const RATIOS = [["normal_ratio", "Normal"], ["important_ratio", "Important"], ["emergency_ratio", "Emergency"]];

export default function ScenarioFields({ sc, setSc }) {
  const set = (k, v) => setSc((s) => ({ ...s, [k]: v }));
  const num = (k) => (e) => set(k, e.target.value === "" ? "" : Number(e.target.value));
  const ratioSum = RATIOS.reduce((t, [k]) => t + (Number(sc[k]) || 0), 0);
  const toggleAlg = (a) => set("algorithms", sc.algorithms.includes(a) ? sc.algorithms.filter((x) => x !== a) : [...sc.algorithms, a]);
  return (
    <>
      <label className="field wide">Name<input value={sc.name} onChange={(e) => set("name", e.target.value)} /></label>
      <div className="form-grid">
        {FIELDS.map(([k, label, step]) => (
          <label className="field" key={k}>{label}<input type="number" step={step} value={sc[k]} onChange={num(k)} /></label>
        ))}
      </div>
      <div className="form-grid">
        {RATIOS.map(([k, label]) => (
          <label className="field" key={k}>{label} ratio
            <input type="number" step="0.05" min="0" max="1" value={sc[k]} onChange={num(k)} /></label>
        ))}
      </div>
      <p className={Math.abs(ratioSum - 1) > 1e-6 ? "error" : "muted"}>Ratios sum to {ratioSum.toFixed(2)} (must be 1.00)</p>
      <div className="checks">
        Algorithms:
        {ALGS.map((a) => (
          <label key={a}><input type="checkbox" checked={sc.algorithms.includes(a)} onChange={() => toggleAlg(a)} /> {a}</label>
        ))}
      </div>
    </>
  );
}
