import { useCallback, useEffect, useRef, useState } from "react";
import { liveSocketUrl } from "../api";

const MAX_EVENTS = 3000; // keep memory bounded on long plays

// One WebSocket = one server-side session (see README, Phase 9). All server state arrives as messages.
export function useLiveSession() {
  const ws = useRef(null);
  const [conn, setConn] = useState("connecting"); // connecting | open | closed
  const [session, setSession] = useState(null); // {network, adapter}
  const [state, setState] = useState(null); // latest snapshot
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [playing, setPlaying] = useState(false);

  const onMessage = useCallback((m) => {
    switch (m.type) {
      case "session.created":
        setSession({ network: m.network, adapter: m.adapter });
        setState(m.state); setEvents([]); setError(null); setPlaying(false); break;
      case "events":
        setState(m.state);
        setEvents((old) => { const all = old.concat(m.events); return all.length > MAX_EVENTS ? all.slice(-MAX_EVENTS) : all; });
        break;
      case "state": setState(m.state); break;
      case "error": setError(m.error); break;
      case "ack":
        if (m.command === "sim.play") setPlaying(true);
        if (m.command === "sim.pause" || m.command === "sim.step") setPlaying(false);
        break;
      default: break;
    }
  }, []);

  const connect = useCallback(() => {
    const sock = new WebSocket(liveSocketUrl());
    ws.current = sock;
    setConn("connecting");
    sock.onopen = () => setConn("open");
    sock.onclose = () => { if (ws.current === sock) { setConn("closed"); setPlaying(false); } };
    sock.onmessage = (ev) => onMessage(JSON.parse(ev.data));
  }, [onMessage]);

  useEffect(() => {
    connect();
    return () => { const s = ws.current; ws.current = null; s && s.close(); };
  }, [connect]);

  const send = useCallback((msg) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) { setError(null); ws.current.send(JSON.stringify(msg)); }
    else setError("Not connected to the backend.");
  }, []);

  return { conn, session, state, events, error, playing, send, reconnect: connect, clearError: () => setError(null) };
}
