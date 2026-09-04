"""
Scoped insight detectors + dependency dispatcher (Phase 2, stage 3).

Every scoped detector is a *restriction* of the corresponding full-graph
detector in ``InsightsEngine`` to patterns that involve at least one touched
entity. The full detectors are left untouched and remain the reference
implementation; the scoped Cypher is derived from them by:

1. keeping the pattern, the semantic predicates (e.g. ``size(case_ids) > 1``,
   ``b1 <> b3``, ``in_degree >= $threshold``, the asymmetric cross-case test)
   and the ``$case_id`` filter **verbatim**;
2. prepending an *anchor*: the pattern is matched once per pattern position
   in a ``CALL { ... UNION ... }`` block, each branch seeking the touched
   identities (unique-constraint MERGE keys) at that position, so a touched
   entity is discovered **anywhere** in the 1-/2-/3-hop pattern; ``WITH DISTINCT``
   then removes duplicates from overlapping branches;
3. re-aggregating **globally** for anchored entities (fan-in/out, bridge,
   IMEI reuse): the anchor selects *which* accounts / persons / IMEIs to look
   at, the aggregation itself still counts every relationship in the graph;
4. reusing the full detector's Python row->InsightItem formatting (same
   titles, severities, facts, ``insight_id`` seeds), by calling the full
   detector with a session shim that substitutes the scoped Cypher/params.

Case isolation: scoped detectors accept the same ``case_id`` parameter as the
full ones. The dispatcher runs them with ``case_id=None`` (global scope) so
cross-case patterns reachable from touched identities are never missed.

LINKED_TO_IP stays read-only exactly as in the full detectors (BRIDGE_NODE's
relationship union and INFRASTRUCTURE_REUSE's IP branch); nothing here writes
or manufactures it.

Not claimed here: semantic equivalence with the full run. That is established
by the live equivalence harness (next stage).
"""
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from neo4j import Session

from backend.logging_config import logger
from backend.models.event import DetectorRun, EntityKind, TouchedEntities
from backend.models.insights import InsightItem
from backend.services.insights_engine import InsightsEngine


# ---------------------------------------------------------------------------
# Limits. The full detectors hard-code LIMIT 25 / 20 / 10 (non-deterministic
# truncation on large graphs). Scoped queries return only pattern instances
# touching a handful of identities, so the same caps are far less likely to bite;
# they are kept as *parameters* (default = full-detector value) and can be lifted
# (limit=None) for LIMIT-free comparison runs in the equivalence harness.
# ---------------------------------------------------------------------------

FULL_LIMITS: Dict[str, int] = {
    "TRANSFER_CHAIN": 25,
    "POSSIBLE_CO_LOCATION": 20,
    "CROSS_DOMAIN_PATH": 10,
}


def _limit_clause(limit: Optional[int]) -> str:
    return "LIMIT $limit" if limit is not None else ""


class _ScopedQuerySession:
    """
    Session shim handed to a *full* InsightsEngine detector: every ``run`` call
    is answered with the scoped Cypher/params supplied at construction (one per
    call, in order), so the detector's own Python formatting produces the
    InsightItems. Anything the shim does not expect is an error, never a silent
    fallback to the full-graph query.
    """

    def __init__(self, session: Session, queries: Sequence[Tuple[str, Dict[str, Any]]]):
        self._session = session
        self._queries = list(queries)
        self.executed: List[Tuple[str, Dict[str, Any]]] = []

    def run(self, query: str, parameters: Optional[Dict[str, Any]] = None, **kwargs):
        if not self._queries:
            raise RuntimeError("scoped detector issued more queries than expected")
        cypher, params = self._queries.pop(0)
        self.executed.append((cypher, params))
        return self._session.run(cypher, params)


def _run_full_formatting(full_detector: Callable[..., List[InsightItem]], session: Session,
                         queries: Sequence[Tuple[str, Dict[str, Any]]], case_id: Optional[str], **kwargs) -> List[InsightItem]:
    shim = _ScopedQuerySession(session, queries)
    insights = full_detector(shim, case_id, **kwargs)
    if shim._queries:
        raise RuntimeError("scoped detector issued fewer queries than expected")
    return insights


# ---------------------------------------------------------------------------
# Scoped detectors
# ---------------------------------------------------------------------------

class ScopedInsightsEngine:
    """Scoped counterparts of the 10 InsightsEngine detectors (same names + ``_scoped``)."""

    # ---- 1. SHARED_ENTITY -------------------------------------------------
    # Full: any node of 6 labels with size(case_ids) > 1 (+ case filter).
    # Anchor: the node itself, by MERGE key per label. Trigger: node upsert / case join.
    @staticmethod
    def detect_shared_entities_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None) -> List[InsightItem]:
        cypher = """
        CALL {
          MATCH (n:Person) WHERE n.person_id IN $persons RETURN n
          UNION
          MATCH (n:Phone) WHERE n.phone_number IN $phones RETURN n
          UNION
          MATCH (n:BankAccount) WHERE n.account_number IN $bank_accounts RETURN n
          UNION
          MATCH (n:Vehicle) WHERE n.vin IN $vehicles RETURN n
          UNION
          MATCH (n:SocialHandle) WHERE n.handle_id IN $social_handles RETURN n
          UNION
          MATCH (n:IPAddress) WHERE n.ip_address IN $ip_addresses RETURN n
        }
        WITH DISTINCT n
        WHERE (n:Person OR n:Phone OR n:BankAccount OR n:Vehicle OR n:SocialHandle OR n:IPAddress)
          AND size(n.case_ids) > 1
          AND ($case_id IS NULL OR $case_id IN n.case_ids)
        RETURN labels(n) as labels,
               coalesce(n.name, n.phone_number, n.account_number, n.vin, n.handle, n.ip_address, n.person_id) as identifier,
               n.case_ids as case_ids,
               coalesce(n.person_id, n.phone_number, n.account_number, n.vin, n.handle_id, n.ip_address) as entity_id
        """
        params = {
            "persons": sorted(touched.persons), "phones": sorted(touched.phones),
            "bank_accounts": sorted(touched.bank_accounts), "vehicles": sorted(touched.vehicles),
            "social_handles": sorted(touched.social_handles), "ip_addresses": sorted(touched.ip_addresses),
            "case_id": case_id,
        }
        return _run_full_formatting(InsightsEngine.detect_shared_entities, session, [(cypher, params)], case_id)

    # ---- 2. CROSS_CASE_LINK ---------------------------------------------
    # Full: (p1:Phone)-[CALLED]->(p2:Phone) with the ASYMMETRIC predicate
    #       any(c1 IN p1.case_ids WHERE NOT c1 IN p2.case_ids) (+ case filter on either phone).
    # Anchor: either endpoint phone (NOT the new call id): an old call flips to
    #         cross-case when one endpoint gains a case, and a new call must be
    #         evaluated in both directions.
    @staticmethod
    def detect_cross_case_links_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None) -> List[InsightItem]:
        cypher = """
        CALL {
          MATCH (p1:Phone)-[r:CALLED]->(p2:Phone) WHERE p1.phone_number IN $phones RETURN p1, r, p2
          UNION
          MATCH (p1:Phone)-[r:CALLED]->(p2:Phone) WHERE p2.phone_number IN $phones RETURN p1, r, p2
        }
        WITH DISTINCT p1, r, p2
        WHERE any(c1 IN p1.case_ids WHERE NOT c1 IN p2.case_ids)
          AND ($case_id IS NULL OR $case_id IN p1.case_ids OR $case_id IN p2.case_ids)
        RETURN p1.phone_number as src_phone, p1.case_ids as src_cases,
               p2.phone_number as dst_phone, p2.case_ids as dst_cases,
               r.timestamp as timestamp, r.duration_seconds as duration,
               r.case_id as call_case
        """
        params = {"phones": sorted(touched.phones), "case_id": case_id}
        return _run_full_formatting(InsightsEngine.detect_cross_case_links, session, [(cypher, params)], case_id)

    # ---- 3. BRIDGE_NODE --------------------------------------------------
    # Full: Person with size(case_ids) >= 2 AND count(DISTINCT asset) >= 2 over
    #       OWNS|HAS_HANDLE|LINKED_TO_IP (+ case filter). LINKED_TO_IP kept verbatim (read-only).
    # Anchor: the Person, selected either directly or as owner of a touched asset;
    #         the asset count is re-aggregated over ALL of that person's assets.
    @staticmethod
    def detect_bridge_nodes_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None) -> List[InsightItem]:
        cypher = """
        CALL {
          MATCH (p:Person) WHERE p.person_id IN $persons RETURN p
          UNION
          MATCH (p:Person)-[:OWNS]->(a:Phone) WHERE a.phone_number IN $phones RETURN p
          UNION
          MATCH (p:Person)-[:OWNS]->(a:BankAccount) WHERE a.account_number IN $bank_accounts RETURN p
          UNION
          MATCH (p:Person)-[:OWNS]->(a:Vehicle) WHERE a.vin IN $vehicles RETURN p
          UNION
          MATCH (p:Person)-[:HAS_HANDLE]->(a:SocialHandle) WHERE a.handle_id IN $social_handles RETURN p
          UNION
          MATCH (p:Person)-[:LINKED_TO_IP]->(a:IPAddress) WHERE a.ip_address IN $ip_addresses RETURN p
        }
        WITH DISTINCT p
        MATCH (p)-[:OWNS|HAS_HANDLE|LINKED_TO_IP]->(asset)
        WITH p, count(DISTINCT asset) as asset_count, p.case_ids as cases
        WHERE size(cases) >= 2 AND asset_count >= 2
          AND ($case_id IS NULL OR $case_id IN cases)
        RETURN p.person_id as person_id, p.name as name, cases, asset_count
        """
        params = {
            "persons": sorted(touched.persons), "phones": sorted(touched.phones),
            "bank_accounts": sorted(touched.bank_accounts), "vehicles": sorted(touched.vehicles),
            "social_handles": sorted(touched.social_handles), "ip_addresses": sorted(touched.ip_addresses),
            "case_id": case_id,
        }
        return _run_full_formatting(InsightsEngine.detect_bridge_nodes, session, [(cypher, params)], case_id)

    # ---- 4. TRANSFER_CHAIN -----------------------------------------------
    # Full: b1->b2->b3 (TRANSFERRED_TO x2), b1 <> b3, case filter on any of the three, LIMIT 25.
    # Anchor: a touched account at ANY of the three positions (three UNION branches).
    @staticmethod
    def detect_transfer_chains_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None,
                                      limit: Optional[int] = FULL_LIMITS["TRANSFER_CHAIN"]) -> List[InsightItem]:
        cypher = f"""
        CALL {{
          MATCH (b1:BankAccount)-[r1:TRANSFERRED_TO]->(b2:BankAccount)-[r2:TRANSFERRED_TO]->(b3:BankAccount)
          WHERE b1.account_number IN $bank_accounts RETURN b1, r1, b2, r2, b3
          UNION
          MATCH (b1:BankAccount)-[r1:TRANSFERRED_TO]->(b2:BankAccount)-[r2:TRANSFERRED_TO]->(b3:BankAccount)
          WHERE b2.account_number IN $bank_accounts RETURN b1, r1, b2, r2, b3
          UNION
          MATCH (b1:BankAccount)-[r1:TRANSFERRED_TO]->(b2:BankAccount)-[r2:TRANSFERRED_TO]->(b3:BankAccount)
          WHERE b3.account_number IN $bank_accounts RETURN b1, r1, b2, r2, b3
        }}
        WITH DISTINCT b1, r1, b2, r2, b3
        WHERE b1 <> b3
          AND ($case_id IS NULL OR any(c IN b1.case_ids WHERE c = $case_id) OR any(c IN b2.case_ids WHERE c = $case_id) OR any(c IN b3.case_ids WHERE c = $case_id))
        RETURN b1.account_number as acc1, b2.account_number as acc2, b3.account_number as acc3,
               r1.amount as amt1, r2.amount as amt2,
               r1.timestamp as t1, r2.timestamp as t2,
               b1.case_ids as c1, b2.case_ids as c2, b3.case_ids as c3
        {_limit_clause(limit)}
        """
        params = {"bank_accounts": sorted(touched.bank_accounts), "case_id": case_id, "limit": limit}
        return _run_full_formatting(InsightsEngine.detect_transfer_chains, session, [(cypher, params)], case_id)

    # ---- 5./6. HIGH_FAN_IN / HIGH_FAN_OUT ---------------------------------
    # Full: per target (source) account, count(DISTINCT src/target) >= threshold over ALL edges (+ case filter).
    # Anchor: the aggregated account only (target for fan-in, source for fan-out); the
    #         degree is still computed over every TRANSFERRED_TO edge of that account.
    @staticmethod
    def detect_high_fan_in_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None, threshold: int = 3) -> List[InsightItem]:
        cypher = """
        MATCH (target:BankAccount) WHERE target.account_number IN $bank_accounts
        MATCH (src:BankAccount)-[r:TRANSFERRED_TO]->(target)
        WITH target, count(DISTINCT src) as in_degree, sum(r.amount) as total_in, target.case_ids as cases
        WHERE in_degree >= $threshold
          AND ($case_id IS NULL OR $case_id IN cases)
        RETURN target.account_number as account_number, target.holder_name as holder,
               in_degree, total_in, cases
        """
        params = {"bank_accounts": sorted(touched.bank_accounts), "case_id": case_id, "threshold": threshold}
        return _run_full_formatting(InsightsEngine.detect_high_fan_in, session, [(cypher, params)], case_id, threshold=threshold)

    @staticmethod
    def detect_high_fan_out_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None, threshold: int = 3) -> List[InsightItem]:
        cypher = """
        MATCH (src:BankAccount) WHERE src.account_number IN $bank_accounts
        MATCH (src)-[r:TRANSFERRED_TO]->(target:BankAccount)
        WITH src, count(DISTINCT target) as out_degree, sum(r.amount) as total_out, src.case_ids as cases
        WHERE out_degree >= $threshold
          AND ($case_id IS NULL OR $case_id IN cases)
        RETURN src.account_number as account_number, src.holder_name as holder,
               out_degree, total_out, cases
        """
        params = {"bank_accounts": sorted(touched.bank_accounts), "case_id": case_id, "threshold": threshold}
        return _run_full_formatting(InsightsEngine.detect_high_fan_out, session, [(cypher, params)], case_id, threshold=threshold)

    # ---- 7. INFRASTRUCTURE_REUSE -----------------------------------------
    # Full: (A) group ALL phones by IMEI, size(phones) > 1; (B) LINKED_TO_IP groups.
    #       No $case_id in Cypher; case filtering happens in Python (kept: the full
    #       detector's formatting loop is reused unchanged, including that filter).
    # Anchor: (A) IMEIs of touched phones, then re-aggregate over ALL phones sharing
    #         each IMEI. (B) touched IPs via the read-only LINKED_TO_IP pattern
    #         (dead today because nothing writes that edge; kept verbatim).
    @staticmethod
    def detect_infrastructure_reuse_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None) -> List[InsightItem]:
        cypher_imei = """
        MATCH (anchor:Phone) WHERE anchor.phone_number IN $phones
          AND anchor.imei IS NOT NULL AND anchor.imei <> ''
        WITH DISTINCT anchor.imei as touched_imei
        MATCH (ph:Phone {imei: touched_imei})
        WHERE ph.imei IS NOT NULL AND ph.imei <> ''
        WITH ph.imei as imei, collect(ph.phone_number) as phones, collect(DISTINCT ph.case_ids) as nested_cases
        WHERE size(phones) > 1
        RETURN imei, phones, nested_cases
        """
        cypher_ip = """
        MATCH (ip:IPAddress) WHERE ip.ip_address IN $ip_addresses
        MATCH (entity)-[:LINKED_TO_IP]->(ip)
        WITH ip, collect(DISTINCT coalesce(entity.person_id, entity.phone_number, entity.handle_id)) as entities,
             collect(DISTINCT ip.case_ids) as nested_cases
        WHERE size(entities) > 1
        RETURN ip.ip_address as ip_address, entities, nested_cases
        """
        queries = [
            (cypher_imei, {"phones": sorted(touched.phones)}),
            (cypher_ip, {"ip_addresses": sorted(touched.ip_addresses)}),
        ]
        return _run_full_formatting(InsightsEngine.detect_infrastructure_reuse, session, queries, case_id)

    # ---- 8. POSSIBLE_CO_LOCATION -----------------------------------------
    # Full: (e1)-[l1:LOCATED_AT]->(loc)<-[l2:LOCATED_AT]-(e2), elementId(e1) < elementId(e2),
    #       case filter `$case_id IN loc.case_ids OR $case_id IN l1.case_id OR $case_id IN l2.case_id`, LIMIT 20.
    #       Verified live (Neo4j 5.20): l.case_id is a scalar STRING; `$case_id IN <string>` evaluates at
    #       runtime as equality (true iff equal, no error) -> the predicate is kept VERBATIM.
    #       Orientation of pairs depends on elementId() string ordering (pre-existing; the pair *set* is stable).
    # Anchor: the Location, or a touched Person/Vehicle at EITHER end (e1 or e2). e1/e2 stay unlabeled
    #         in the pattern exactly like the full detector (CellTower endpoints included).
    @staticmethod
    def detect_possible_co_location_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None,
                                           limit: Optional[int] = FULL_LIMITS["POSSIBLE_CO_LOCATION"]) -> List[InsightItem]:
        cypher = f"""
        CALL {{
          MATCH (e1)-[l1:LOCATED_AT]->(loc:Location)<-[l2:LOCATED_AT]-(e2)
          WHERE loc.location_id IN $locations RETURN e1, l1, loc, l2, e2
          UNION
          MATCH (e1)-[l1:LOCATED_AT]->(loc:Location)<-[l2:LOCATED_AT]-(e2)
          WHERE (e1:Person AND e1.person_id IN $persons) OR (e1:Vehicle AND e1.vin IN $vehicles) OR (e1:CellTower AND e1.cell_tower_id IN $cell_towers)
          RETURN e1, l1, loc, l2, e2
          UNION
          MATCH (e1)-[l1:LOCATED_AT]->(loc:Location)<-[l2:LOCATED_AT]-(e2)
          WHERE (e2:Person AND e2.person_id IN $persons) OR (e2:Vehicle AND e2.vin IN $vehicles) OR (e2:CellTower AND e2.cell_tower_id IN $cell_towers)
          RETURN e1, l1, loc, l2, e2
        }}
        WITH DISTINCT e1, l1, loc, l2, e2
        WHERE elementId(e1) < elementId(e2)
          AND ($case_id IS NULL OR $case_id IN loc.case_ids OR $case_id IN l1.case_id OR $case_id IN l2.case_id)
        RETURN loc.location_id as loc_id, loc.name as loc_name,
               coalesce(e1.person_id, e1.vin, e1.phone_number) as entity1,
               coalesce(e2.person_id, e2.vin, e2.phone_number) as entity2,
               l1.timestamp as t1, l2.timestamp as t2,
               loc.case_ids as cases
        {_limit_clause(limit)}
        """
        params = {
            "locations": sorted(touched.locations), "persons": sorted(touched.persons),
            "vehicles": sorted(touched.vehicles), "cell_towers": sorted(touched.cell_towers),
            "case_id": case_id, "limit": limit,
        }
        return _run_full_formatting(InsightsEngine.detect_possible_co_location, session, [(cypher, params)], case_id)

    # ---- 9. CROSS_DOMAIN_PATH --------------------------------------------
    # Full: p1-OWNS->ph1-CALLED->ph2<-OWNS-p2-OWNS->b1-TRANSFERRED_TO->b2, p1 <> p2, case filter on p1/p2, LIMIT 10.
    # Anchor: a touched Person (p1|p2), Phone (ph1|ph2) or BankAccount (b1|b2) at ANY of the six positions.
    @staticmethod
    def detect_cross_domain_paths_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None,
                                         limit: Optional[int] = FULL_LIMITS["CROSS_DOMAIN_PATH"]) -> List[InsightItem]:
        pattern = "MATCH (p1:Person)-[:OWNS]->(ph1:Phone)-[c:CALLED]->(ph2:Phone)<-[:OWNS]-(p2:Person)-[:OWNS]->(b1:BankAccount)-[t:TRANSFERRED_TO]->(b2:BankAccount)"
        ret = "RETURN p1, ph1, c, ph2, p2, b1, t, b2"
        branches = [
            f"{pattern} WHERE p1.person_id IN $persons {ret}",
            f"{pattern} WHERE p2.person_id IN $persons {ret}",
            f"{pattern} WHERE ph1.phone_number IN $phones {ret}",
            f"{pattern} WHERE ph2.phone_number IN $phones {ret}",
            f"{pattern} WHERE b1.account_number IN $bank_accounts {ret}",
            f"{pattern} WHERE b2.account_number IN $bank_accounts {ret}",
        ]
        cypher = f"""
        CALL {{
          {" UNION ".join(branches)}
        }}
        WITH DISTINCT p1, ph1, c, ph2, p2, b1, t, b2
        WHERE p1 <> p2
          AND ($case_id IS NULL OR $case_id IN p1.case_ids OR $case_id IN p2.case_ids)
        RETURN p1.name as p1_name, p1.person_id as p1_id,
               p2.name as p2_name, p2.person_id as p2_id,
               ph1.phone_number as ph1, ph2.phone_number as ph2,
               b1.account_number as b1, b2.account_number as b2,
               p1.case_ids as c1, p2.case_ids as c2
        {_limit_clause(limit)}
        """
        params = {
            "persons": sorted(touched.persons), "phones": sorted(touched.phones),
            "bank_accounts": sorted(touched.bank_accounts), "case_id": case_id, "limit": limit,
        }
        return _run_full_formatting(InsightsEngine.detect_cross_domain_paths, session, [(cypher, params)], case_id)

    # ---- 10. PRIOR_CASE_LINK ---------------------------------------------
    # Full: (p:Person)-[:HAS_PRIOR_CASE]->(pc:PriorCase), case filter on p.case_ids.
    # Anchor: the Person or the PriorCase.
    @staticmethod
    def detect_prior_case_links_scoped(session: Session, touched: TouchedEntities, case_id: Optional[str] = None) -> List[InsightItem]:
        cypher = """
        CALL {
          MATCH (p:Person)-[:HAS_PRIOR_CASE]->(pc:PriorCase) WHERE p.person_id IN $persons RETURN p, pc
          UNION
          MATCH (p:Person)-[:HAS_PRIOR_CASE]->(pc:PriorCase) WHERE pc.prior_case_id IN $prior_cases RETURN p, pc
        }
        WITH DISTINCT p, pc
        WHERE ($case_id IS NULL OR $case_id IN p.case_ids)
        RETURN p.person_id as person_id, p.name as name,
               pc.prior_case_id as prior_id, pc.case_number as case_num,
               pc.offense as offense, pc.jurisdiction as jurisdiction,
               pc.status as status, pc.year as year,
               p.case_ids as case_ids
        """
        params = {"persons": sorted(touched.persons), "prior_cases": sorted(touched.prior_cases), "case_id": case_id}
        return _run_full_formatting(InsightsEngine.detect_prior_case_links, session, [(cypher, params)], case_id)


# ---------------------------------------------------------------------------
# Dependency matrix + dispatcher
# ---------------------------------------------------------------------------

#: Registry: insight type -> (scoped detector, full reference detector).
SCOPED_DETECTORS: Dict[str, Tuple[Callable[..., List[InsightItem]], Callable[..., List[InsightItem]]]] = {
    "SHARED_ENTITY": (ScopedInsightsEngine.detect_shared_entities_scoped, InsightsEngine.detect_shared_entities),
    "CROSS_CASE_LINK": (ScopedInsightsEngine.detect_cross_case_links_scoped, InsightsEngine.detect_cross_case_links),
    "BRIDGE_NODE": (ScopedInsightsEngine.detect_bridge_nodes_scoped, InsightsEngine.detect_bridge_nodes),
    "TRANSFER_CHAIN": (ScopedInsightsEngine.detect_transfer_chains_scoped, InsightsEngine.detect_transfer_chains),
    "HIGH_FAN_IN": (ScopedInsightsEngine.detect_high_fan_in_scoped, InsightsEngine.detect_high_fan_in),
    "HIGH_FAN_OUT": (ScopedInsightsEngine.detect_high_fan_out_scoped, InsightsEngine.detect_high_fan_out),
    "INFRASTRUCTURE_REUSE": (ScopedInsightsEngine.detect_infrastructure_reuse_scoped, InsightsEngine.detect_infrastructure_reuse),
    "POSSIBLE_CO_LOCATION": (ScopedInsightsEngine.detect_possible_co_location_scoped, InsightsEngine.detect_possible_co_location),
    "CROSS_DOMAIN_PATH": (ScopedInsightsEngine.detect_cross_domain_paths_scoped, InsightsEngine.detect_cross_domain_paths),
    "PRIOR_CASE_LINK": (ScopedInsightsEngine.detect_prior_case_links_scoped, InsightsEngine.detect_prior_case_links),
}

#: Same order as InsightsEngine.run_all_detectors (matters only for last-write-wins dedupe parity).
DETECTOR_ORDER: List[str] = list(SCOPED_DETECTORS.keys())

#: Dependency matrix (from the verification report). A detector runs when ANY of its kinds is touched.
#: Biased toward running: e.g. Vehicle/SocialHandle/IPAddress trigger BRIDGE_NODE because their upsert may add an
#: OWNS/HAS_HANDLE edge; Phone triggers CROSS_DOMAIN_PATH because ph1/ph2 sit inside that path, etc.
DETECTOR_TRIGGERS: Dict[str, Set[EntityKind]] = {
    "SHARED_ENTITY": {EntityKind.PERSON, EntityKind.PHONE, EntityKind.BANK_ACCOUNT, EntityKind.VEHICLE,
                      EntityKind.SOCIAL_HANDLE, EntityKind.IP_ADDRESS},
    "CROSS_CASE_LINK": {EntityKind.PHONE, EntityKind.CALLED},
    "BRIDGE_NODE": {EntityKind.PERSON, EntityKind.PHONE, EntityKind.BANK_ACCOUNT, EntityKind.VEHICLE,
                    EntityKind.SOCIAL_HANDLE, EntityKind.IP_ADDRESS, EntityKind.OWNS, EntityKind.HAS_HANDLE},
    "TRANSFER_CHAIN": {EntityKind.BANK_ACCOUNT, EntityKind.TRANSFERRED_TO},
    "HIGH_FAN_IN": {EntityKind.BANK_ACCOUNT, EntityKind.TRANSFERRED_TO},
    "HIGH_FAN_OUT": {EntityKind.BANK_ACCOUNT, EntityKind.TRANSFERRED_TO},
    "INFRASTRUCTURE_REUSE": {EntityKind.PHONE, EntityKind.IP_ADDRESS},
    "POSSIBLE_CO_LOCATION": {EntityKind.LOCATION, EntityKind.CELL_TOWER, EntityKind.LOCATED_AT,
                             EntityKind.VEHICLE, EntityKind.PERSON},
    "CROSS_DOMAIN_PATH": {EntityKind.PERSON, EntityKind.PHONE, EntityKind.BANK_ACCOUNT,
                          EntityKind.OWNS, EntityKind.CALLED, EntityKind.TRANSFERRED_TO},
    "PRIOR_CASE_LINK": {EntityKind.PERSON, EntityKind.PRIOR_CASE, EntityKind.HAS_PRIOR_CASE},
}

#: Which detectors care that a node of a given label GAINED a case (case_ids change). Also biased toward running.
CASE_JOIN_TRIGGERS: Dict[str, Set[str]] = {
    "Person": {"SHARED_ENTITY", "BRIDGE_NODE", "CROSS_DOMAIN_PATH", "PRIOR_CASE_LINK", "POSSIBLE_CO_LOCATION"},
    "Phone": {"SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE", "CROSS_DOMAIN_PATH"},
    "BankAccount": {"SHARED_ENTITY", "BRIDGE_NODE", "TRANSFER_CHAIN", "HIGH_FAN_IN", "HIGH_FAN_OUT", "CROSS_DOMAIN_PATH"},
    "Vehicle": {"SHARED_ENTITY", "BRIDGE_NODE", "POSSIBLE_CO_LOCATION"},
    "SocialHandle": {"SHARED_ENTITY", "BRIDGE_NODE"},
    "IPAddress": {"SHARED_ENTITY", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE"},
    "Location": {"POSSIBLE_CO_LOCATION"},
    "CellTower": {"POSSIBLE_CO_LOCATION"},
    "PriorCase": {"PRIOR_CASE_LINK"},
}


def select_detectors(touched: TouchedEntities) -> Tuple[List[str], List[str]]:
    """Return (selected, skipped) insight types for a touched set, in DETECTOR_ORDER.

    A detector is selected when any touched node kind or relationship kind is in its
    trigger set, or when a node whose label is in CASE_JOIN_TRIGGERS for it gained a case.
    """
    kinds = touched.kinds()
    joined_labels = {item.split(":", 1)[0] for item in touched.case_ids_changed}
    selected: List[str] = []
    for name in DETECTOR_ORDER:
        by_kind = bool(DETECTOR_TRIGGERS[name] & kinds)
        by_join = any(name in CASE_JOIN_TRIGGERS.get(label, set()) for label in joined_labels)
        if by_kind or by_join:
            selected.append(name)
    skipped = [n for n in DETECTOR_ORDER if n not in selected]
    return selected, skipped


def run_scoped_detectors(session: Session, touched: TouchedEntities, case_id: Optional[str] = None,
                         limit_overrides: Optional[Dict[str, Optional[int]]] = None,
                         ) -> Tuple[List[InsightItem], List[DetectorRun], List[str]]:
    """
    Dispatcher: run every scoped detector selected for ``touched`` and return
    ``(insights, detector_runs, skipped)``.

    * ``case_id=None`` (default) = global scope, as required so cross-case relationships
      reachable from touched identities are not missed. A case filter can still be applied
      by passing ``case_id`` (same semantics as the full detectors).
    * Failures are captured per detector as ``DetectorRun(status="failed", error=...)``
      -- never swallowed -- and the remaining detectors still run.
    * Insights are de-duplicated by ``insight_id`` with last-write-wins, mirroring
      ``InsightsEngine.run_all_detectors``.
    * ``limit_overrides`` maps insight type -> LIMIT (None lifts it); used by the
      equivalence harness for LIMIT-free comparisons.
    """
    selected, skipped = select_detectors(touched)
    runs: List[DetectorRun] = []
    all_insights: List[InsightItem] = []
    for name in selected:
        scoped, _full = SCOPED_DETECTORS[name]
        kwargs: Dict[str, Any] = {}
        if limit_overrides and name in limit_overrides and name in FULL_LIMITS:
            kwargs["limit"] = limit_overrides[name]
        try:
            found = scoped(session, touched, case_id, **kwargs)
            all_insights.extend(found)
            runs.append(DetectorRun(detector=scoped.__name__, insight_type=name, status="ok", insights=len(found)))
        except Exception as exc:  # reported, not swallowed
            logger.warning(f"Scoped detector {scoped.__name__} failed: {exc}")
            runs.append(DetectorRun(detector=scoped.__name__, insight_type=name, status="failed", insights=0,
                                    error=f"{type(exc).__name__}: {exc}"))
    unique: Dict[str, InsightItem] = {}
    for ins in all_insights:
        unique[ins.insight_id] = ins
    return list(unique.values()), runs, skipped


def make_detector_runner(case_id: Optional[str] = None, limit_overrides: Optional[Dict[str, Optional[int]]] = None):
    """Build a ``DetectorRunner`` for ``DeltaProcessor(detector_runner=...)``."""
    def runner(session: Session, touched: TouchedEntities):
        return run_scoped_detectors(session, touched, case_id=case_id, limit_overrides=limit_overrides)
    runner.__name__ = "scoped_detector_runner"
    return runner
