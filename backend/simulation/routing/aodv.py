"""
AODV — Ad hoc On-Demand Distance Vector routing (RFC 3561-style baseline).

Unlike AEAR/RSSI (which make a fresh local decision at every hop), AODV is
route-discovery-based: a node with no known route to a destination floods a
RREQ, the destination (or a node with a fresh cached route) replies with a
unicast RREP that travels back along the reverse path, and only once that
RREP arrives does the source have a routing-table entry it can actually use
to forward data hop-by-hop.

What's implemented here (a "recognizable" AODV per project scope, not a
byte-exact RFC 3561 stack):
  - RREQ flood with (originator, rreq_id) duplicate suppression
  - Reverse-route caching during RREQ propagation
  - RREP unicast back along the reverse path, establishing forward routes
    and precursor lists as it goes
  - Destination sequence numbers, with the standard freshness rule (never
    overwrite a route with staler sequence-number information)
  - Route lifetime / expiry
  - RERR cascading to precursors on route breakage, triggered both
    proactively (NODE_FAILED / LINK_FAILED) and reactively (a data-forward
    attempt discovering its next hop is no longer usable)

One control-plane event vocabulary quirk to be aware of: EventType only
defines RERR_SENT (no RERR_RECEIVED counterpart). We reuse RERR_SENT for
both "a node reports a break" and "a precursor is notified of a break" --
the receiving node_id on each event instance disambiguates who it's for.

Scope boundary shared with AEAR/RSSI (see README): a packet buffered because
route discovery timed out, or because no route could be found, stays
buffered until something explicitly retries it. This module does not retry
on its own -- Phase 4's `FailureRecoveryManager` (backend/simulation/
failures/) calls each router's `retry_buffered_packets()` when a node/link
recovers, which is the safe, generic hook this router exposes for that.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import Event, EventType
from backend.simulation.network.network import Network
from backend.simulation.packets.packet import Packet, PacketStatus

ALGORITHM_NAME = "AODV"


@dataclass
class AODVConfig:
    active_route_timeout_s: float = 3.0     # how long a route stays valid without refresh
    route_discovery_timeout_s: float = 2.0  # give up waiting for a RREP after this long
    rreq_propagation_delay_s: float = 0.05  # simulated per-hop RREQ broadcast delay
    rrep_propagation_delay_s: float = 0.05  # simulated per-hop RREP unicast delay
    hop_transmission_delay_s: float = 0.05  # simulated per-hop DATA packet transmission delay
    max_rreq_retries: int = 2               # extra RREQ attempts after the first, before giving up
    max_loop_fallbacks: int = 5             # zero-time "route loops back" retries per packet before it is dropped


@dataclass
class RouteEntry:
    """
    One row of a node's AODV routing table. The same table (and the same
    RouteEntry shape) holds both "forward" entries (route to an actual data
    destination) and "reverse" entries (route back to an RREQ originator) --
    exactly as real AODV keeps a single table for both, indexed by
    destination node ID either way.
    """

    destination: str
    next_hop: str
    hop_count: int
    dest_seq_num: int
    lifetime_expiry: float
    precursors: Set[str] = field(default_factory=set)
    valid: bool = True

    def to_dict(self) -> dict:
        return {
            "destination": self.destination,
            "next_hop": self.next_hop,
            "hop_count": self.hop_count,
            "dest_seq_num": self.dest_seq_num,
            "lifetime_expiry": round(self.lifetime_expiry, 3),
            "precursors": sorted(self.precursors),
            "valid": self.valid,
        }


class AODVRouter:
    """
    Registers itself against a SimulationEngine and drives AODV route
    discovery/maintenance plus hop-by-hop data forwarding once routes exist.

    Usage:
        engine = SimulationEngine(network)
        router = AODVRouter(network, engine)
        engine.generate_packet(0.0, "N1", "N9", PacketType.NORMAL)
        engine.run()
    """

    def __init__(self, network: Network, engine: SimulationEngine,
                 config: Optional[AODVConfig] = None):
        self.network = network
        self.engine = engine
        self.config = config or AODVConfig()

        # routing_tables[node_id][destination_or_originator] -> RouteEntry
        self.routing_tables: Dict[str, Dict[str, RouteEntry]] = defaultdict(dict)
        self.seq_numbers: Dict[str, int] = defaultdict(int)
        self.rreq_counters: Dict[str, int] = defaultdict(int)
        self.seen_rreqs: Dict[str, Set[Tuple[str, int]]] = defaultdict(set)
        # (node_id, destination) -> [packet_id, ...] waiting on route discovery
        self.pending_packets: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        # (node_id, destination) -> number of RREQ attempts made so far for
        # the CURRENT discovery episode (reset once it succeeds or truly gives up)
        self.rreq_retry_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        # packet_id -> (engine clock, consecutive loop fallbacks at that clock); see _route_data_packet
        self._loop_fallbacks: Dict[str, Tuple[float, int]] = {}
        # (node_id, destination) -> rreq_id of the most recent attempt, so a
        # stale timeout marker from a superseded earlier attempt can be ignored
        self.latest_rreq_id: Dict[Tuple[str, str], int] = {}

        engine.register_handler(EventType.PACKET_GENERATED, self._on_packet_ready)
        engine.register_handler(EventType.PACKET_RECEIVED, self._on_packet_ready)
        engine.register_handler(EventType.RREQ_RECEIVED, self._on_rreq_received)
        engine.register_handler(EventType.RREP_RECEIVED, self._on_rrep_received)
        engine.register_handler(EventType.RERR_SENT, self._on_rerr_received)
        engine.register_handler(EventType.ROUTE_CHANGED, self._on_route_changed_marker)
        engine.register_handler(EventType.NODE_FAILED, self._on_node_failed)
        engine.register_handler(EventType.LINK_FAILED, self._on_link_failed)

    # -- helpers ----------------------------------------------------------------

    def _link_usable(self, node_id: str, neighbor_id: str) -> bool:
        if neighbor_id not in self.network.nodes:
            return False
        neighbor = self.network.get_node(neighbor_id)
        if not neighbor.is_available:
            return False
        link = self.network.get_link(node_id, neighbor_id)
        return link is not None and link.is_active

    def _valid_route(self, node_id: str, destination: str) -> Optional[RouteEntry]:
        entry = self.routing_tables[node_id].get(destination)
        if entry is None or not entry.valid:
            return None
        if entry.lifetime_expiry < self.engine.clock:
            entry.valid = False
            return None
        return entry

    def _refresh_route(self, node_id: str, destination: str) -> None:
        entry = self.routing_tables[node_id].get(destination)
        if entry:
            entry.lifetime_expiry = self.engine.clock + self.config.active_route_timeout_s

    def _update_route_entry(self, node_id: str, destination: str, next_hop: str,
                             hop_count: int, dest_seq_num: int, lifetime: float) -> RouteEntry:
        """
        Insert or refresh a routing-table entry, applying AODV's standard
        freshness rule: never let staler sequence-number information
        overwrite fresher information, and prefer a shorter hop count when
        sequence numbers tie.
        """
        table = self.routing_tables[node_id]
        existing = table.get(destination)
        if existing is not None:
            if dest_seq_num < existing.dest_seq_num:
                return existing  # stale info -- ignore
            if dest_seq_num == existing.dest_seq_num and existing.valid and hop_count >= existing.hop_count:
                existing.lifetime_expiry = max(existing.lifetime_expiry, lifetime)
                return existing
            existing.next_hop = next_hop
            existing.hop_count = hop_count
            existing.dest_seq_num = dest_seq_num
            existing.lifetime_expiry = lifetime
            existing.valid = True
            return existing
        entry = RouteEntry(destination=destination, next_hop=next_hop, hop_count=hop_count,
                            dest_seq_num=dest_seq_num, lifetime_expiry=lifetime)
        table[destination] = entry
        return entry

    def dump_routing_table(self, node_id: str) -> dict:
        """For Developer/Debug mode (later phase): a JSON-friendly table dump."""
        return {dest: entry.to_dict() for dest, entry in self.routing_tables.get(node_id, {}).items()}

    # -- data packet routing ------------------------------------------------------

    def _on_packet_ready(self, event: Event, engine: SimulationEngine) -> None:
        packet = engine.packets[event.packet_id]
        if packet.is_terminal:
            return
        self._route_data_packet(packet)

    def _route_data_packet(self, packet: Packet) -> None:
        node_id = packet.current_node
        destination = packet.destination

        if packet.ttl <= 0:
            self.engine.drop_packet(packet.packet_id, PacketStatus.DROPPED_TTL_EXPIRED,
                                     reason="TTL reached 0 before delivery", algorithm=ALGORITHM_NAME)
            return

        route = self._valid_route(node_id, destination)
        if route is not None:
            if packet.has_visited(route.next_hop):
                # Loop-safety: AODV's routing table is a network-level,
                # per-packet-agnostic structure. Under route churn (a node
                # failing/recovering mid-run, say), two nodes' tables can
                # transiently point back at each other -- a well-known real
                # failure mode of distance-vector protocols during
                # reconvergence, not unique to this simulator. The route
                # entry itself is still fine for OTHER packets; this one
                # specific packet just can't use it without looping, so
                # fall through to buffering/rediscovery for this packet only
                # rather than invalidating the table entry for everyone.
                #
                # Safety net: if rediscovery keeps handing back the same
                # looping route at the SAME simulated instant (e.g. stale
                # cached routes answering instantly), this would recurse in
                # zero time forever. Bound consecutive same-instant fallbacks
                # and drop the packet explicitly instead of hanging the run.
                clock, count = self._loop_fallbacks.get(packet.packet_id, (None, 0))
                count = count + 1 if clock == self.engine.clock else 1
                self._loop_fallbacks[packet.packet_id] = (self.engine.clock, count)
                if count > self.config.max_loop_fallbacks:
                    self.engine.drop_packet(packet.packet_id, PacketStatus.DROPPED_NO_CANDIDATE,
                                             reason="AODV: the only known route leads back to a node this packet already visited",
                                             algorithm=ALGORITHM_NAME)
                    return
                self._buffer_and_discover(packet)
                return
            if self._link_usable(node_id, route.next_hop):
                self._refresh_route(node_id, destination)
                self.engine.log_forwarding_decision(
                    node_id, packet.packet_id, route.next_hop, algorithm=ALGORITHM_NAME,
                    hop_count=route.hop_count, dest_seq=route.dest_seq_num, source="ROUTING_TABLE",
                )
                self.engine.forward_packet(packet.packet_id, route.next_hop,
                                            transmission_delay=self.config.hop_transmission_delay_s,
                                            algorithm=ALGORITHM_NAME)
                return
            # Table said there was a route, but the next hop is no longer
            # reachable (node/link failed since the last refresh) -- this is
            # a route break discovered reactively; cascade RERR then retry.
            self._handle_route_break(node_id, destination)

        self._buffer_and_discover(packet)

    def _buffer_and_discover(self, packet: Packet) -> None:
        node_id = packet.current_node
        destination = packet.destination
        key = (node_id, destination)
        already_discovering = len(self.pending_packets[key]) > 0
        self.pending_packets[key].append(packet.packet_id)
        self.engine.buffer_packet(packet.packet_id, reason="AODV route discovery in progress",
                                   algorithm=ALGORITHM_NAME)
        if not already_discovering:
            self._originate_rreq(node_id, destination)

    def _release_pending_packets(self, node_id: str, destination: str) -> None:
        key = (node_id, destination)
        self.rreq_retry_counts.pop(key, None)  # discovery succeeded -- reset for next time
        self.latest_rreq_id.pop(key, None)
        packet_ids = self.pending_packets.pop(key, [])
        for pid in packet_ids:
            packet = self.engine.packets.get(pid)
            if packet is None or packet.is_terminal:
                continue
            self.engine.unbuffer_packet(pid)
            self._route_data_packet(packet)

    # -- RREQ: origination and propagation -------------------------------------

    def _originate_rreq(self, node_id: str, destination: str) -> None:
        self.seq_numbers[node_id] += 1  # RFC rule: bump own seq before originating
        self.rreq_counters[node_id] += 1
        rreq_id = self.rreq_counters[node_id]
        originator_seq = self.seq_numbers[node_id]

        known_entry = self.routing_tables[node_id].get(destination)
        dest_seq_known = known_entry.dest_seq_num if (known_entry and known_entry.valid) else None

        self.seen_rreqs[node_id].add((node_id, rreq_id))  # originator never reprocesses its own
        self.latest_rreq_id[(node_id, destination)] = rreq_id

        self.engine.log_now(EventType.RREQ_SENT, node_id=node_id, algorithm=ALGORITHM_NAME,
                            rreq_id=rreq_id, originator=node_id, destination=destination, hop_count=0)

        neighbors = self.network.available_neighbors_of(node_id)
        for neighbor in neighbors:
            self.engine.schedule(
                self.engine.clock + self.config.rreq_propagation_delay_s,
                EventType.RREQ_RECEIVED, node_id=neighbor.node_id, algorithm=ALGORITHM_NAME,
                rreq_id=rreq_id, originator=node_id, originator_seq=originator_seq,
                destination=destination, dest_seq_known=dest_seq_known,
                hop_count=1, previous_hop=node_id,
            )

        self.engine.schedule(
            self.engine.clock + self.config.route_discovery_timeout_s,
            EventType.ROUTE_CHANGED, node_id=node_id, algorithm=ALGORITHM_NAME,
            purpose="route_discovery_timeout", destination=destination, rreq_id=rreq_id,
        )

    def _on_rreq_received(self, event: Event, engine: SimulationEngine) -> None:
        node_id = event.node_id
        meta = event.metadata
        rreq_id = meta["rreq_id"]
        originator = meta["originator"]
        originator_seq = meta["originator_seq"]
        destination = meta["destination"]
        dest_seq_known = meta.get("dest_seq_known")
        hop_count = meta["hop_count"]
        previous_hop = meta["previous_hop"]

        node = self.network.get_node(node_id)
        if not node.is_available:
            return  # node failed since this RREQ was scheduled -- message lost
        if not self._link_usable(previous_hop, node_id):
            return  # link failed in transit -- message lost

        key = (originator, rreq_id)
        if key in self.seen_rreqs[node_id]:
            return  # duplicate arrival via a different path -- ignore
        self.seen_rreqs[node_id].add(key)

        # Cache/refresh the reverse route back toward the RREQ originator.
        self._update_route_entry(node_id, destination=originator, next_hop=previous_hop,
                                  hop_count=hop_count, dest_seq_num=originator_seq,
                                  lifetime=self.engine.clock + self.config.active_route_timeout_s)

        if node_id == destination:
            self.seq_numbers[node_id] = max(self.seq_numbers[node_id], dest_seq_known or 0) + 1
            self._send_rrep(node_id, originator=originator, destination=destination,
                             dest_seq=self.seq_numbers[node_id], hop_count_from_dest=0,
                             send_to=previous_hop)
            return

        cached = self.routing_tables[node_id].get(destination)
        has_fresh_cached_route = (
            cached is not None and cached.valid and cached.lifetime_expiry >= self.engine.clock
            and (dest_seq_known is None or cached.dest_seq_num >= dest_seq_known)
        )
        if has_fresh_cached_route:
            self._send_rrep(node_id, originator=originator, destination=destination,
                             dest_seq=cached.dest_seq_num, hop_count_from_dest=cached.hop_count,
                             send_to=previous_hop)
            return

        # No route known here -- rebroadcast the RREQ onward.
        self.engine.log_now(EventType.RREQ_SENT, node_id=node_id, algorithm=ALGORITHM_NAME,
                            rreq_id=rreq_id, originator=originator, destination=destination,
                            hop_count=hop_count)
        for neighbor in self.network.available_neighbors_of(node_id):
            if neighbor.node_id == previous_hop:
                continue
            self.engine.schedule(
                self.engine.clock + self.config.rreq_propagation_delay_s,
                EventType.RREQ_RECEIVED, node_id=neighbor.node_id, algorithm=ALGORITHM_NAME,
                rreq_id=rreq_id, originator=originator, originator_seq=originator_seq,
                destination=destination, dest_seq_known=dest_seq_known,
                hop_count=hop_count + 1, previous_hop=node_id,
            )

    # -- RREP: origination and propagation -------------------------------------

    def _send_rrep(self, node_id: str, originator: str, destination: str, dest_seq: int,
                   hop_count_from_dest: int, send_to: str) -> None:
        self.engine.log_now(EventType.RREP_SENT, node_id=node_id, algorithm=ALGORITHM_NAME,
                            originator=originator, destination=destination, dest_seq=dest_seq,
                            hop_count=hop_count_from_dest)
        self.engine.schedule(
            self.engine.clock + self.config.rrep_propagation_delay_s,
            EventType.RREP_RECEIVED, node_id=send_to, algorithm=ALGORITHM_NAME,
            originator=originator, destination=destination, dest_seq=dest_seq,
            hop_count=hop_count_from_dest + 1, via=node_id,
        )

    def _on_rrep_received(self, event: Event, engine: SimulationEngine) -> None:
        node_id = event.node_id
        meta = event.metadata
        originator = meta["originator"]
        destination = meta["destination"]
        dest_seq = meta["dest_seq"]
        hop_count = meta["hop_count"]
        via = meta["via"]

        node = self.network.get_node(node_id)
        if not node.is_available:
            return
        if not self._link_usable(via, node_id):
            return  # RREP lost due to link failure in transit

        entry = self._update_route_entry(
            node_id, destination=destination, next_hop=via, hop_count=hop_count,
            dest_seq_num=dest_seq, lifetime=self.engine.clock + self.config.active_route_timeout_s,
        )
        # `via` will now be used by node_id to reach destination -> node_id
        # becomes a precursor on VIA's own table entry for that destination.
        via_entry = self.routing_tables[via].get(destination)
        if via_entry:
            via_entry.precursors.add(node_id)

        if node_id == originator:
            self._release_pending_packets(node_id, destination)
            return

        reverse_entry = self.routing_tables[node_id].get(originator)
        if reverse_entry is None:
            return  # no reverse path cached (shouldn't normally happen) -- drop the RREP
        self._send_rrep(node_id, originator=originator, destination=destination, dest_seq=dest_seq,
                         hop_count_from_dest=hop_count, send_to=reverse_entry.next_hop)

    # -- route maintenance: RERR and failure handling --------------------------

    def _handle_route_break(self, node_id: str, destination: str) -> None:
        entry = self.routing_tables[node_id].get(destination)
        if entry is None or not entry.valid:
            return
        entry.valid = False
        precursors = list(entry.precursors)
        entry.precursors.clear()
        for precursor in precursors:
            # log_now dispatches synchronously to _on_rerr_received below,
            # which cascades the break further if that precursor has its
            # own precursors relying on it for the same destination.
            self.engine.log_now(EventType.RERR_SENT, node_id=precursor, algorithm=ALGORITHM_NAME,
                                destination=destination, reported_by=node_id)

    def _on_rerr_received(self, event: Event, engine: SimulationEngine) -> None:
        self._handle_route_break(event.node_id, event.metadata["destination"])

    def _on_node_failed(self, event: Event, engine: SimulationEngine) -> None:
        failed_node = event.node_id
        for node_id, table in list(self.routing_tables.items()):
            for destination, entry in list(table.items()):
                # Routes THROUGH the failed node are broken, and so are routes TO it: nobody can
                # reach a failed destination, so no node may keep answering route requests for it
                # from a cached entry (that produced an instant-RREP rediscovery loop).
                if entry.valid and (entry.next_hop == failed_node or destination == failed_node):
                    self._handle_route_break(node_id, destination)

    def _on_link_failed(self, event: Event, engine: SimulationEngine) -> None:
        a, b = event.metadata["node_a"], event.metadata["node_b"]
        for owner, other in ((a, b), (b, a)):
            table = self.routing_tables.get(owner, {})
            for destination, entry in list(table.items()):
                if entry.valid and entry.next_hop == other:
                    self._handle_route_break(owner, destination)

    # -- route discovery timeout ------------------------------------------------

    def _on_route_changed_marker(self, event: Event, engine: SimulationEngine) -> None:
        if event.metadata.get("purpose") != "route_discovery_timeout":
            return
        node_id = event.node_id
        destination = event.metadata["destination"]
        rreq_id = event.metadata["rreq_id"]

        if self._valid_route(node_id, destination) is not None:
            return  # discovery succeeded before the timeout fired -- nothing to do

        key = (node_id, destination)
        if self.latest_rreq_id.get(key) != rreq_id:
            return  # a newer RREQ attempt for this (node, destination) has already superseded this timeout marker

        if not self.pending_packets.get(key):
            return  # nothing left waiting (e.g. packets already terminated some other way)

        if self.rreq_retry_counts[key] < self.config.max_rreq_retries:
            # Real AODV retries a bounded number of times before giving up --
            # this also means a recovery that lands in the same instant as a
            # timeout (a genuine race, not just a theoretical one -- see
            # demo_phase4_recovery.py's Scenario 5) gets one more real chance
            # rather than the packet being stranded forever.
            self.rreq_retry_counts[key] += 1
            self._originate_rreq(node_id, destination)
            return

        # Retries exhausted: give up for real. Any still-pending packets stay
        # BUFFERED (store-and-forward). Automatic retry on a LATER node/link
        # recovery is handled externally by FailureRecoveryManager (Phase 4),
        # via retry_buffered_packets() below.
        self.rreq_retry_counts.pop(key, None)
        self.latest_rreq_id.pop(key, None)
        self.pending_packets.pop(key, None)

    # -- recovery hook (Phase 4: called by FailureRecoveryManager) ------------

    def retry_buffered_packets(self) -> None:
        """
        Re-attempt routing for BUFFERED packets that are NOT currently
        tracked in `pending_packets` -- i.e. packets whose route discovery
        already timed out and gave up (see `_on_route_changed_marker`),
        genuinely stuck rather than mid-flight.

        This check matters: a packet still awaiting its RREP is ALSO
        BUFFERED, but it already has its own lifecycle (release-on-RREP or
        give-up-on-timeout) tracked via `pending_packets`. Retrying it here
        too would create a duplicate entry in that list and risk a packet
        being routed twice from two different points in its journey. Only
        packets AODV has already given up on are safe to retry externally.
        """
        for packet in list(self.engine.packets.values()):
            if packet.status.value != "BUFFERED":
                continue
            key = (packet.current_node, packet.destination)
            if packet.packet_id in self.pending_packets.get(key, []):
                continue  # still legitimately mid-discovery -- leave it alone
            self.engine.unbuffer_packet(packet.packet_id)
            self._route_data_packet(packet)
