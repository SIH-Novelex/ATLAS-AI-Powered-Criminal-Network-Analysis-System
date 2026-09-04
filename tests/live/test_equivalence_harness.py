"""
FORMAL BULK-vs-INCREMENTAL EQUIVALENCE HARNESS  (Phase 2, stage 4)  -- live Neo4j.

For each fixture set (a sequence of CaseData payloads):

  PATH A (bulk)          reset -> bulk-ingest every payload in order (IngestionService.ingest_case)
                         -> F  = InsightsEngine.run_all_detectors(case_id=None)           [reference]
  PATH B (incremental)   reset -> for every payload: create the Case node with the shared CASE_MERGE
                         statement (events never create cases), then POST the EXACT same logical
                         records as ONE EventBatch to /api/events/batch (FastAPI TestClient -> router
                         -> DeltaProcessor -> graph_writes -> one tx -> touched -> scoped dispatcher)
                         -> S  = union of the scoped insights returned by every batch
                         -> F' = InsightsEngine.run_all_detectors(case_id=None) after the replay

Assertions (never weakened; failures are reported field by field):
  1. ids(F) == ids(F')                         write parity
  2. F == F'  (all fields except created_at)   full field equivalence
  3. ids(F) ⊆ ids(S)                           scoped processing misses nothing
  4. for every id in F ∩ S: every deterministic field of the scoped item equals the reference item
  5. graph parity: node and relationship multisets identical between the two paths (timestamps excluded)

IDENTITY NORMALISATION (documented exclusions):
  * created_at        -- generation timestamp, excluded from every comparison.
  * POSSIBLE_CO_LOCATION (two pre-existing order dependencies of the FULL detector, both verified live):
    (a) pairs are oriented by ``elementId(e1) < elementId(e2)`` -- a STRING comparison of internal ids --
        so insight_id, entities_involved order and observed_facts wording flip between runs of the SAME
        code path. These insights are therefore identified by (location_id, frozenset(entities)) and
        compared on orientation-independent fields (type, severity, case_ids, metadata, entity SET, title,
        interpretation, alternatives, disclaimer).
    (b) when a pair has several LOCATED_AT edge combinations at one location (an entity observed twice),
        the full query emits several rows with the SAME insight_id and ``run_all_detectors`` keeps whichever
        row Neo4j returned last; the surviving "Observed timestamps" fact is storage-order dependent
        (2 distinct outcomes observed in 4 identical bulk runs on fresh DBs). The harness does NOT drop the
        field: for every path it asserts the reported timestamps are one of the VALID edge combinations of
        that pair in the graph, and requires strict equality across F / F' / S whenever the pair has exactly
        one combination (the common case; all pairs in samples_001_002 and equivalence_scenarios).
  * INFRASTRUCTURE_REUSE: the full detector renders ``collect(ph.phone_number)`` -- an UNORDERED Cypher
    aggregation -- into ``entities_involved`` and the first observed fact. Verified live: the same bulk
    path on fresh DBs produced ['+B1','+A1'] in one run and ['+A1','+B1'] in five others. The phone list
    is therefore compared as a set (sorted) in ``entities_involved`` and inside that fact.
  * case_ids (all types) are compared order-insensitively: the field is semantically a set, and four
    detectors (CROSS_CASE_LINK, TRANSFER_CHAIN, CROSS_DOMAIN_PATH, INFRASTRUCTURE_REUSE) build it with
    Python ``list(set(...))`` whose order depends on per-process string-hash randomisation (verified:
    two orders across 8 interpreter processes). The one fact that embeds that list ("Appears across
    cases: ...") is normalised the same way. Every other field is compared verbatim.
  * ROW-COLLISION GUARD: several full detectors collapse multiple query rows into one insight_id
    (e.g. two CALLED edges between the same phones, two edge combinations for one co-located pair) and
    keep whichever row Neo4j returned last, so per-row facts (timestamps, amounts) become storage-order
    dependent. The harness asserts, per fixture set, that no such collision exists for CROSS_CASE_LINK,
    TRANSFER_CHAIN and CROSS_DOMAIN_PATH (rows == insights); co-location collisions are handled
    explicitly by the edge-combination check above. A fixture that violates the guard fails loudly
    instead of yielding a false comparison.

LIMIT HANDLING (documented):
  * Full detectors hard-code LIMIT 25 (TRANSFER_CHAIN), 20 (POSSIBLE_CO_LOCATION), 10 (CROSS_DOMAIN_PATH).
    On a graph where a LIMIT binds, F itself is a nondeterministic subset, so equivalence would be
    ill-defined. The harness therefore (a) asserts, per fixture set, that the LIMIT-free row count of
    each limited full query is <= its LIMIT (so F is complete and deterministic), and (b) runs the scoped
    detectors LIMIT-free (limit_overrides = None) for S via the Stage 3 override, without changing the
    production defaults. Fixture sets are deliberately small enough for (a) to hold.

Fixture sets: sample cases (case_001 + case_002), sample cases + case_edge_batching, and the
hand-inspectable equivalence_scenarios.json (scenarios A-N of the Stage 4 brief).

Skipped automatically unless Neo4j is reachable (NEO4J_TEST_* env vars). WARNING: wipes the DB.
"""
import inspect
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.live_neo4j

from tests.live.test_live_neo4j_step0 import _driver_or_skip  # noqa: E402
from tests.unit.test_delta_processor import events_from_case_doc  # noqa: E402
from tests.live.test_live_neo4j_delta_processor import graph_snapshot  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures")
SAMPLES = os.path.join("dataset")

FIXTURE_SETS: Dict[str, List[str]] = {
    "samples_001_002": [os.path.join(SAMPLES, "case_001_homicide.json"), os.path.join(SAMPLES, "case_002_fraud.json")],
    "samples_plus_edge": [os.path.join(SAMPLES, "case_001_homicide.json"), os.path.join(SAMPLES, "case_002_fraud.json"),
                          os.path.join(FIXTURES, "case_edge_batching.json")],
    "equivalence_scenarios": [os.path.join(FIXTURES, "equivalence_scenarios.json")],
}

LIMITED_FULL_DETECTORS = {"TRANSFER_CHAIN": ("detect_transfer_chains", 25),
                          "POSSIBLE_CO_LOCATION": ("detect_possible_co_location", 20),
                          "CROSS_DOMAIN_PATH": ("detect_cross_domain_paths", 10)}
LIMIT_FREE = {"TRANSFER_CHAIN": None, "POSSIBLE_CO_LOCATION": None, "CROSS_DOMAIN_PATH": None}


# ---------------------------------------------------------------------------
# payload loading
# ---------------------------------------------------------------------------

def load_payloads(paths: List[str]) -> List[Dict[str, Any]]:
    out = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if "payloads" in doc:  # equivalence_scenarios.json: ordered list of CaseData payloads
            out.extend({k: v for k, v in pl.items() if not k.startswith("_")} for pl in doc["payloads"])
        else:
            out.append(doc)
    return out


# ---------------------------------------------------------------------------
# canonical identity + comparable content
# ---------------------------------------------------------------------------

def canonical_key(ins) -> Tuple:
    if ins.insight_type.value == "POSSIBLE_CO_LOCATION":
        return ("POSSIBLE_CO_LOCATION", ins.metadata.get("location_id"), frozenset(ins.entities_involved))
    return ("ID", ins.insight_id)


_LIST_FACT_PREFIXES = ("phone numbers: ", "distinct entities: ", "Appears across cases: ", "Associated case(s): ")


def _sort_list_fact(fact: str) -> str:
    """Sort the comma-separated list that follows one of the known prefixes (INFRASTRUCTURE_REUSE facts)."""
    for prefix in _LIST_FACT_PREFIXES:
        if prefix in fact:
            head, tail = fact.split(prefix, 1)
            items, dot = (tail[:-1], ".") if tail.endswith(".") else (tail, "")
            return head + prefix + ", ".join(sorted(x.strip() for x in items.split(","))) + dot
    return fact


def comparable(ins) -> Dict[str, Any]:
    """Deterministic content of an insight (created_at excluded; documented set-valued fields normalised)."""
    d = ins.model_dump(mode="json", exclude={"created_at"})
    d["case_ids"] = sorted(d["case_ids"])  # semantically a set (see module doc)
    if ins.insight_type.value == "INFRASTRUCTURE_REUSE":
        d["entities_involved"] = sorted(d["entities_involved"])  # collect() order
        d["observed_facts"] = [_sort_list_fact(f) for f in d["observed_facts"]]
    if ins.insight_type.value == "POSSIBLE_CO_LOCATION":
        # orientation-dependent fields -> orientation-independent equivalents; the observed-timestamp fact is
        # checked separately against the graph (see coloc_timestamps / assert_coloc_timestamps_valid)
        d = {"insight_type": d["insight_type"], "severity": d["severity"], "case_ids": sorted(d["case_ids"]),
             "metadata": d["metadata"], "entities": sorted(d["entities_involved"]),
             "title": d["title"], "derived_interpretation": d["derived_interpretation"],
             "alternative_explanations": d["alternative_explanations"], "disclaimer": d["disclaimer"]}
    return d


def coloc_timestamps(ins) -> Tuple[str, str]:
    """Sorted (orientation-free) pair of timestamps reported in a co-location insight's second fact."""
    for fact in ins.observed_facts:
        m = re.search(r"Entity 1 \((.*?)\), Entity 2 \((.*?)\)", fact)
        if m:
            return tuple(sorted(m.groups()))
    raise AssertionError(f"co-location insight without timestamp fact: {ins.observed_facts}")


def coloc_valid_combos(session) -> Dict[Tuple, set]:
    """For every unordered co-located pair: the set of sorted timestamp pairs of ALL its LOCATED_AT edge
    combinations at that location (what the full query's rows can carry)."""
    rows = session.run("""
        MATCH (e1)-[l1:LOCATED_AT]->(loc:Location)<-[l2:LOCATED_AT]-(e2)
        WHERE elementId(e1) < elementId(e2)
        RETURN loc.location_id AS loc, coalesce(e1.person_id, e1.vin, e1.phone_number) AS a,
               coalesce(e2.person_id, e2.vin, e2.phone_number) AS b, l1.timestamp AS t1, l2.timestamp AS t2
    """).data()
    out: Dict[Tuple, set] = {}
    for r in rows:
        key = ("POSSIBLE_CO_LOCATION", r["loc"], frozenset([str(r["a"]), str(r["b"])]))
        out.setdefault(key, set()).add(tuple(sorted([str(r["t1"]), str(r["t2"])])))
    return out


def assert_coloc_timestamps_valid(label, combos, F_idx, other_idx, other_name):
    """Per common co-location pair: both paths report a VALID edge combination; strict equality when unique."""
    multi = []
    for k in sorted(set(F_idx) & set(other_idx), key=str):
        if k[0] != "POSSIBLE_CO_LOCATION":
            continue
        valid = combos[k]
        tf, to = coloc_timestamps(F_idx[k]), coloc_timestamps(other_idx[k])
        assert tf in valid, f"[{label}] F co-location {k} reports timestamps {tf} not among the graph's edge combinations {valid}"
        assert to in valid, f"[{label}] {other_name} co-location {k} reports timestamps {to} not among the graph's edge combinations {valid}"
        if len(valid) == 1:
            assert tf == to, f"[{label}] {k}: unique edge combination but timestamps differ: F={tf} {other_name}={to}"
        else:
            multi.append((k, sorted(valid), tf, to))
    return multi


def index(insights) -> Dict[Tuple, Any]:
    out: Dict[Tuple, Any] = {}
    for i in insights:
        out[canonical_key(i)] = i
    return out


def field_diffs(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    return {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


# ---------------------------------------------------------------------------
# the two paths
# ---------------------------------------------------------------------------

def _reset(session):
    from backend.services.schema_manager import init_schema
    session.run("MATCH (n) DETACH DELETE n")
    init_schema(session)


def run_bulk_path(session, payloads):
    from backend.models.case_input import CaseData
    from backend.services.ingestion_service import IngestionService
    from backend.services.insights_engine import InsightsEngine
    _reset(session)
    responses = [IngestionService.ingest_case(session, CaseData(**p)) for p in payloads]
    F = InsightsEngine.run_all_detectors(session, None)
    return F, responses, graph_snapshot(session), coloc_valid_combos(session)


def run_incremental_path(driver, payloads, limit_overrides=LIMIT_FREE):
    """Replays through the real HTTP endpoint; the router's processor is given a LIMIT-free scoped runner
    (production default keeps the full detectors' limits -- only the harness lifts them, see module doc)."""
    from fastapi.testclient import TestClient
    from backend.database import db
    from backend.main import app
    from backend.models.case_input import CaseMetadata
    from backend.models.common import get_current_iso_time
    from backend.routers import events as events_router
    from backend.services import graph_writes as gw
    from backend.services.delta_processor import DeltaProcessor
    from backend.services.insights_engine import InsightsEngine
    from backend.services.scoped_detectors import make_detector_runner

    with driver.session() as s:
        _reset(s)

    S: List[Any] = []
    results = []
    harness_processor = DeltaProcessor(detector_runner=make_detector_runner(case_id=None, limit_overrides=limit_overrides))
    with patch.object(db, "get_session", side_effect=lambda *a, **k: driver.session()), \
         patch.object(events_router, "_processor", harness_processor):
        with TestClient(app) as client:
            for p in payloads:
                case_id = p["case_metadata"]["case_id"]
                with driver.session() as s:
                    s.run(gw.CASE_MERGE, {**gw.case_params(CaseMetadata(**p["case_metadata"])), "now": get_current_iso_time()})
                r = client.post("/api/events/batch", json={"case_id": case_id, "events": events_from_case_doc(p), "batch_id": f"harness-{case_id}"})
                assert r.status_code in (200, 201), (case_id, r.status_code, r.text[:300])
                body = r.json()
                assert body["detectors_failed"] == [], (case_id, body["detectors_failed"], [d for d in body["detectors_run"] if d["status"] == "failed"])
                results.append(body)
                from backend.models.insights import InsightItem
                S.extend(InsightItem(**i) for i in body["insights"])
    with driver.session() as s:
        F_prime = InsightsEngine.run_all_detectors(s, None)
        snap = graph_snapshot(s)
    return S, F_prime, results, snap


def _full_query(fn_name: str) -> str:
    from backend.services.insights_engine import InsightsEngine
    src = inspect.getsource(getattr(InsightsEngine, fn_name))
    return re.sub(r"LIMIT \d+", "", re.findall(r'"""(.*?)"""', src, re.S)[0])


def assert_limits_not_binding(session):
    """F is only a deterministic reference if no full-detector LIMIT truncates rows on this graph."""
    report = {}
    for itype, (fn, lim) in LIMITED_FULL_DETECTORS.items():
        n = len(session.run(_full_query(fn), {"case_id": None}).data())
        report[itype] = (n, lim)
        assert n <= lim, f"{itype}: LIMIT-free full query yields {n} rows > LIMIT {lim}; F would be a nondeterministic subset"
    return report


ROW_COLLISION_GUARDED = {"CROSS_CASE_LINK": "detect_cross_case_links",
                         "TRANSFER_CHAIN": "detect_transfer_chains",
                         "CROSS_DOMAIN_PATH": "detect_cross_domain_paths"}


def assert_no_row_collisions(session, F):
    """Full detectors keep the LAST of several rows sharing an insight_id (storage-order dependent per-row
    facts). Fail loudly if the fixture contains such collisions for the guarded types (see module doc)."""
    counts = {}
    for i in F:
        counts[i.insight_type.value] = counts.get(i.insight_type.value, 0) + 1
    report = {}
    for itype, fn in ROW_COLLISION_GUARDED.items():
        rows = len(session.run(_full_query(fn), {"case_id": None}).data())
        report[itype] = (rows, counts.get(itype, 0))
        assert rows == counts.get(itype, 0), (
            f"{itype}: {rows} full-query rows collapse into {counts.get(itype, 0)} insight ids -- the full detector "
            f"resolves this by storage order; adjust the fixture (see module doc, ROW-COLLISION GUARD)")
    return report


# ---------------------------------------------------------------------------
# reporting helper: never weaken, always explain
# ---------------------------------------------------------------------------

def explain_mismatch(label, F_idx, other_idx, other_name):
    only_f = sorted(str(k) for k in set(F_idx) - set(other_idx))
    only_o = sorted(str(k) for k in set(other_idx) - set(F_idx))
    lines = [f"[{label}] identity mismatch:",
             f"  only in F ({len(only_f)}): {only_f}",
             f"  only in {other_name} ({len(only_o)}): {only_o}"]
    for k in set(F_idx) - set(other_idx):
        i = F_idx[k]
        lines.append(f"  F-only {k}: type={i.insight_type.value} entities={i.entities_involved} cases={i.case_ids} (detector: detect_{i.insight_type.value.lower()})")
    for k in set(other_idx) - set(F_idx):
        i = other_idx[k]
        lines.append(f"  {other_name}-only {k}: type={i.insight_type.value} entities={i.entities_involved} cases={i.case_ids}")
    return "\n".join(lines)


def explain_field_diffs(label, F_idx, other_idx, other_name):
    problems = []
    for k in sorted(set(F_idx) & set(other_idx), key=str):
        diffs = field_diffs(comparable(F_idx[k]), comparable(other_idx[k]))
        if diffs:
            problems.append(f"  {k} ({F_idx[k].insight_type.value}): " + "; ".join(f"{f}: F={a!r} {other_name}={b!r}" for f, (a, b) in diffs.items()))
    return f"[{label}] field-level differences ({len(problems)}):\n" + "\n".join(problems) if problems else ""


# ---------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def driver():
    d = _driver_or_skip()
    yield d
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    d.close()


@pytest.mark.parametrize("set_name", sorted(FIXTURE_SETS))
def test_bulk_vs_incremental_equivalence(driver, set_name):
    payloads = load_payloads(FIXTURE_SETS[set_name])

    # PATH A ------------------------------------------------------------------
    with driver.session() as s:
        F, bulk_responses, (nodes_a, rels_a), combos = run_bulk_path(s, payloads)
        limit_report = assert_limits_not_binding(s)
        collision_report = assert_no_row_collisions(s, F)
    assert F, "reference run produced no insights -- fixture set is not exercising the detectors"

    # PATH B ------------------------------------------------------------------
    S, F_prime, results, (nodes_b, rels_b) = run_incremental_path(driver, payloads)

    F_idx, Fp_idx, S_idx = index(F), index(F_prime), index(S)

    # 5. graph parity (write path equivalence) ---------------------------------
    assert nodes_b == nodes_a, f"[{set_name}] node multiset differs between bulk and event replay"
    assert rels_b == rels_a, f"[{set_name}] relationship multiset differs between bulk and event replay"

    # 1. ids(F) == ids(F') -----------------------------------------------------
    assert set(F_idx) == set(Fp_idx), explain_mismatch(set_name, F_idx, Fp_idx, "F'")

    # 2. F == F' field-level ---------------------------------------------------
    diffs = explain_field_diffs(set_name, F_idx, Fp_idx, "F'")
    assert not diffs, diffs
    multi_fp = assert_coloc_timestamps_valid(set_name, combos, F_idx, Fp_idx, "F'")

    # 3. ids(F) ⊆ ids(S) -------------------------------------------------------
    missing = set(F_idx) - set(S_idx)
    assert not missing, explain_mismatch(set_name, F_idx, S_idx, "S")

    # 4. common ids: deterministic fields identical -----------------------------
    diffs = explain_field_diffs(set_name, {k: F_idx[k] for k in F_idx if k in S_idx}, S_idx, "S")
    assert not diffs, diffs
    multi_s = assert_coloc_timestamps_valid(set_name, combos, F_idx, S_idx, "S")

    # sanity on what was compared (not count-only assertions, but recorded for the report)
    by_type = {}
    for i in F:
        by_type[i.insight_type.value] = by_type.get(i.insight_type.value, 0) + 1
    print(f"\n[{set_name}] |F|={len(F_idx)} |F'|={len(Fp_idx)} |S|={len(S_idx)} nodes={len(nodes_a)} rels={len(rels_a)} "
          f"limits(rows/limit)={limit_report} row_collisions(rows/insights)={collision_report} types={by_type} extra_in_S={len(set(S_idx) - set(F_idx))} "
          f"coloc_pairs_with_multiple_edge_combos={len(multi_fp)} (F' vs F: {[(str(k[2]), tf, to) for k, _v, tf, to in multi_fp]})")


def test_equivalence_scenarios_expected_types_and_scenario_coverage(driver):
    """The hand-inspectable fixture must produce exactly the documented per-type counts (F), and the
    incremental batches must show the scenario triggers the fixture was built for."""
    with open(os.path.join(FIXTURES, "equivalence_scenarios.json"), "r", encoding="utf-8") as f:
        doc = json.load(f)
    expected = {k: v for k, v in doc["expected_full_insights"].items() if not k.startswith("_")}
    payloads = load_payloads(FIXTURE_SETS["equivalence_scenarios"])

    with driver.session() as s:
        F, _bulk, _snap, _combos = run_bulk_path(s, payloads)
    got = {}
    for i in F:
        got[i.insight_type.value] = got.get(i.insight_type.value, 0) + 1
    assert got == expected, f"expected per-type counts {expected}, got {got}"

    S, F_prime, results, _ = run_incremental_path(driver, payloads)
    by_case = {r["case_id"]: r for r in results}

    # A. existing entity receives a new case_id -> reported as case_ids_changed on the EQ-B batch, and
    #    the join-triggered detectors ran and found the shared entities / bridge / cross-case link
    b = by_case["EQ-B"]
    assert set(b["touched"]["case_ids_changed"]) == {"Person:P-A1", "Phone:+A1", "BankAccount:ACC-A1"}
    types_b = {i["insight_type"] for i in b["insights"]}
    assert {"SHARED_ENTITY", "BRIDGE_NODE", "CROSS_CASE_LINK", "HIGH_FAN_IN", "TRANSFER_CHAIN", "INFRASTRUCTURE_REUSE"} <= types_b
    # E. the OLD call +A1 -> +A2 became a cross-case link in the EQ-B batch (no new call between them)
    assert any(i["insight_type"] == "CROSS_CASE_LINK" and i["entities_involved"] == ["+A1", "+A2"] for i in b["insights"])
    # D. owner side effect: P-A1 touched via owner_person_id and BRIDGE_NODE found with all 5 assets
    assert "P-A1" in b["touched"]["persons"] and "OWNS" in b["touched"]["relationship_kinds"]
    bridge = next(i for i in b["insights"] if i["insight_type"] == "BRIDGE_NODE")
    assert bridge["entities_involved"] == ["P-A1"] and bridge["metadata"]["asset_count"] == 5
    # I. fan-in re-aggregated over ALL inbound edges (1 from EQ-A + 2 from EQ-B)
    fan_in = next(i for i in b["insights"] if i["insight_type"] == "HIGH_FAN_IN")
    assert fan_in["entities_involved"] == ["ACC-A2"] and fan_in["metadata"]["in_degree"] == 3
    # F/G/H. chains discovered where the touched account is b1 (ACC-B1), b2 (ACC-A1 / ACC-A2) and b3 (ACC-A2 via ACC-B1->ACC-A1->ACC-A2)
    chains = {tuple(i["entities_involved"]) for i in b["insights"] if i["insight_type"] == "TRANSFER_CHAIN"}
    assert {("ACC-B1", "ACC-A1", "ACC-A2"), ("ACC-B1", "ACC-A2", "ACC-A3"), ("ACC-B2", "ACC-A2", "ACC-A3"), ("ACC-A1", "ACC-A2", "ACC-A3")} == chains
    # J. fan-out (EQ-C) ; L. cross-domain paths through every position (EQ-C batch touches p1,p2,ph1,ph2,b1 and implicit b2s)
    c = by_case["EQ-C"]
    assert any(i["insight_type"] == "HIGH_FAN_OUT" and i["metadata"]["out_degree"] == 3 for i in c["insights"])
    assert sum(1 for i in c["insights"] if i["insight_type"] == "CROSS_DOMAIN_PATH") == 3
    # K. co-location through either endpoint: EQ-D touches P-D1, VIN-D1 AND the location LOC-A1 (surveillance log),
    #    so the location anchor re-evaluates all 10 pairs there (generous by design); the 7 pairs that involve the
    #    newly observed P-D1 / VIN-D1 (4 + 4 - 1 shared) are among them.
    d = by_case["EQ-D"]
    assert "LOC-A1" in d["touched"]["locations"] and set(d["touched"]["persons"]) == {"P-D1"} and set(d["touched"]["vehicles"]) == {"VIN-D1"}
    coloc = {frozenset(i["entities_involved"]) for i in d["insights"] if i["insight_type"] == "POSSIBLE_CO_LOCATION"}
    assert len(coloc) == 10
    involving_new = {fs for fs in coloc if fs & {"P-D1", "VIN-D1"}}
    assert len(involving_new) == 7
    assert frozenset({"P-D1", "P-A1"}) in coloc and frozenset({"VIN-D1", "VIN-A1"}) in coloc and frozenset({"P-D1", "VIN-D1"}) in coloc
    # M/N. prior-case link via Person (EQ-A) and via the re-used PriorCase (EQ-D)
    assert any(i["insight_type"] == "PRIOR_CASE_LINK" and i["entities_involved"] == ["P-A1", "PRIOR-A1"] for i in by_case["EQ-A"]["insights"])
    assert any(i["insight_type"] == "PRIOR_CASE_LINK" and i["entities_involved"] == ["P-D1", "PRIOR-A1"] for i in d["insights"])
    assert "PriorCase:PRIOR-A1" in d["touched"]["case_ids_changed"]

    # B. relationship between two PRE-EXISTING entities (EQ-E: one CALL +C1 -> +A2, nothing declared):
    #    both endpoints touched, no case join (relationship writers never append case_ids: preserved semantics),
    #    and the batch still surfaces the cross-case link and the cross-domain path the new edge completes.
    e = by_case["EQ-E"]
    assert e["events_by_type"] == {"COMMUNICATION": 1}
    assert set(e["touched"]["phones"]) == {"+A2", "+C1"} and e["touched"]["relationship_kinds"] == ["CALLED"]
    assert e["touched"]["case_ids_changed"] == [] and e["writes"]["nodes_created"] == 0
    assert any(i["insight_type"] == "CROSS_CASE_LINK" and i["entities_involved"] == ["+C1", "+A2"] for i in e["insights"])
    assert any(i["insight_type"] == "CROSS_DOMAIN_PATH" and i["entities_involved"] == ["P-C1", "P-A2", "+C1", "+A2", "ACC-A2", "ACC-A3"] for i in e["insights"])
    assert not any(i["insight_type"] in ("SHARED_ENTITY", "BRIDGE_NODE", "HIGH_FAN_IN", "HIGH_FAN_OUT", "TRANSFER_CHAIN") for i in e["insights"])
    # C. either endpoint alone is enough for the scoped detectors to find the same two insights
    from backend.models.event import TouchedEntities
    from backend.services.scoped_detectors import SCOPED_DETECTORS
    with driver.session() as s:
        for phone in ("+C1", "+A2"):
            t = TouchedEntities(phones={phone})
            ccl = {tuple(i.entities_involved) for i in SCOPED_DETECTORS["CROSS_CASE_LINK"][0](s, t, None)}
            cdp = {tuple(i.entities_involved) for i in SCOPED_DETECTORS["CROSS_DOMAIN_PATH"][0](s, t, None, limit=None)}
            assert ("+C1", "+A2") in ccl, phone
            assert ("P-C1", "P-A2", "+C1", "+A2", "ACC-A2", "ACC-A3") in cdp, phone


def test_case_isolation_and_case_filter_parity(driver):
    """Case isolation: (1) after case_001 alone, no insight references case_002; (2) cross-case insights appear
    only once the second case actually shares the underlying entities; (3) case_id filtering of the scoped
    detectors equals the full detectors' for every case; (4) a case_ids join triggers the right detectors."""
    from backend.models.event import TouchedEntities
    from backend.services.insights_engine import InsightsEngine
    from backend.services.scoped_detectors import SCOPED_DETECTORS, run_scoped_detectors, select_detectors

    payloads = load_payloads(FIXTURE_SETS["samples_001_002"])
    S, F_prime, results, _ = run_incremental_path(driver, payloads)
    r1, r2 = results

    # (1) case_001 processing produced no insight carrying case_002
    assert all("CASE-2024-002" not in i["case_ids"] for i in r1["insights"])
    assert r1["touched"]["case_ids_changed"] == []
    assert not any(i["insight_type"] in ("SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE") for i in r1["insights"])

    # (2) cross-case insights only after case_002 shares P-001 / +919876543210 / ACC-9001-DEV / the IP
    assert set(r2["touched"]["case_ids_changed"]) == {"Person:P-001", "Phone:+919876543210", "BankAccount:ACC-9001-DEV", "IPAddress:198.51.100.45"}
    cross = [i for i in r2["insights"] if i["insight_type"] in ("SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE")]
    assert cross and all(set(i["case_ids"]) == {"CASE-2024-001", "CASE-2024-002"} for i in cross)
    shared_ids = {i["entities_involved"][0] for i in cross if i["insight_type"] == "SHARED_ENTITY"}
    assert shared_ids == {"P-001", "+919876543210", "ACC-9001-DEV", "198.51.100.45"}
    # the phone that is only a CALL target in case_001 is NOT shared (preserved relationship case_ids semantics)
    assert "+919876543211" not in shared_ids

    # (4) the join alone selects the cross-case detectors
    t = TouchedEntities(case_ids_changed={"Phone:+919876543210"})
    assert {"SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE", "CROSS_DOMAIN_PATH"} == set(select_detectors(t)[0])

    # (3) case_id filter parity: for each case and each detector, scoped(everything touched, case_id) == full(case_id)
    with driver.session() as s:
        q = lambda c: {r["k"] for r in s.run(c)}
        everything = TouchedEntities(
            persons=q("MATCH (n:Person) RETURN n.person_id AS k"), phones=q("MATCH (n:Phone) RETURN n.phone_number AS k"),
            bank_accounts=q("MATCH (n:BankAccount) RETURN n.account_number AS k"), vehicles=q("MATCH (n:Vehicle) RETURN n.vin AS k"),
            social_handles=q("MATCH (n:SocialHandle) RETURN n.handle_id AS k"), ip_addresses=q("MATCH (n:IPAddress) RETURN n.ip_address AS k"),
            locations=q("MATCH (n:Location) RETURN n.location_id AS k"), cell_towers=q("MATCH (n:CellTower) RETURN n.cell_tower_id AS k"),
            prior_cases=q("MATCH (n:PriorCase) RETURN n.prior_case_id AS k"))
        for case_id in ("CASE-2024-001", "CASE-2024-002", "CASE-NONE"):
            full = index(InsightsEngine.run_all_detectors(s, case_id))
            scoped_ins, runs, _ = run_scoped_detectors(s, everything, case_id=case_id, limit_overrides=LIMIT_FREE)
            assert all(r.status == "ok" for r in runs)
            scoped = index(scoped_ins)
            assert set(full) == set(scoped), explain_mismatch(f"case filter {case_id}", full, scoped, "scoped")
            d = explain_field_diffs(f"case filter {case_id}", full, scoped, "scoped")
            assert not d, d
            if case_id == "CASE-NONE":
                assert full == {}  # a case nobody belongs to yields nothing on both paths
