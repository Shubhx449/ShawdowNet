import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="page">
      <header className="page-head"><h1>Page not found</h1></header>
      <div className="card"><Link to="/">Back to the dashboard</Link></div>
    </div>
  );
}
