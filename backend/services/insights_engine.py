import hashlib
from typing import List, Dict, Any, Optional
from neo4j import Session
from backend.models.insights import InsightItem, InsightType, InsightSeverity
from backend.logging_config import logger


def generate_insight_id(insight_type: str, seed: str) -> str:
    hash_str = hashlib.md5(f"{insight_type}_{seed}".encode("utf-8")).hexdigest()[:8]
    return f"INS-{insight_type}-{hash_str.upper()}"


class InsightsEngine:
    """
    Automated Rule & Pattern Insights Engine covering all 10 canonical insight types.
    """

    @staticmethod
    def detect_shared_entities(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (n)
        WHERE (n:Person OR n:Phone OR n:BankAccount OR n:Vehicle OR n:SocialHandle OR n:IPAddress)
          AND size(n.case_ids) > 1
          AND ($case_id IS NULL OR $case_id IN n.case_ids)
        RETURN labels(n) as labels,
               coalesce(n.name, n.phone_number, n.account_number, n.vin, n.handle, n.ip_address, n.person_id) as identifier,
               n.case_ids as case_ids,
               coalesce(n.person_id, n.phone_number, n.account_number, n.vin, n.handle_id, n.ip_address) as entity_id
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            label = r.get("labels", ["Entity"])[0] if r.get("labels") else "Entity"
            ident = r.get("identifier")
            cases = r.get("case_ids") or []
            ent_id = str(r.get("entity_id"))
            
            insight = InsightItem(
                insight_id=generate_insight_id("SHARED_ENTITY", f"{ent_id}_{'-'.join(sorted(cases))}"),
                insight_type=InsightType.SHARED_ENTITY,
                title=f"Shared {label} Across Cases: {ident}",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"{label} '{ident}' is referenced in multiple distinct cases: {', '.join(cases)}.",
                    f"Entity unique identifier: {ent_id}."
                ],
                derived_interpretation="Indicates direct cross-case entity overlap or recurring operational infrastructure across separate investigations.",
                alternative_explanations=[
                    "Legitimate second-hand asset transfer or vehicle resale",
                    "Recycled telephone number reassigned by carrier",
                    "Shared family or household asset"
                ],
                entities_involved=[ent_id],
                case_ids=cases,
                metadata={"entity_type": label, "identifier": ident}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_cross_case_links(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (p1:Phone)-[r:CALLED]->(p2:Phone)
        WHERE any(c1 IN p1.case_ids WHERE NOT c1 IN p2.case_ids)
          AND ($case_id IS NULL OR $case_id IN p1.case_ids OR $case_id IN p2.case_ids)
        RETURN p1.phone_number as src_phone, p1.case_ids as src_cases,
               p2.phone_number as dst_phone, p2.case_ids as dst_cases,
               r.timestamp as timestamp, r.duration_seconds as duration,
               r.case_id as call_case
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            src = r.get("src_phone")
            dst = r.get("dst_phone")
            all_cases = list(set((r.get("src_cases") or []) + (r.get("dst_cases") or [])))
            
            insight = InsightItem(
                insight_id=generate_insight_id("CROSS_CASE_LINK", f"{src}_{dst}"),
                insight_type=InsightType.CROSS_CASE_LINK,
                title=f"Cross-Case Telecommunication: {src} -> {dst}",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"Phone {src} (Cases: {r.get('src_cases')}) directly communicated with Phone {dst} (Cases: {r.get('dst_cases')}).",
                    f"Call timestamp: {r.get('timestamp') or 'Unknown'}, duration: {r.get('duration') or 0} seconds."
                ],
                derived_interpretation="Direct coordination channel between targets belonging to separate open investigations.",
                alternative_explanations=[
                    "Accidental or misdialed connection",
                    "Public service or common merchant inquiry"
                ],
                entities_involved=[src, dst],
                case_ids=all_cases,
                metadata={"type": "CALLED"}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_bridge_nodes(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (p:Person)-[:OWNS|HAS_HANDLE|LINKED_TO_IP]->(asset)
        WITH p, count(DISTINCT asset) as asset_count, p.case_ids as cases
        WHERE size(cases) >= 2 AND asset_count >= 2
          AND ($case_id IS NULL OR $case_id IN cases)
        RETURN p.person_id as person_id, p.name as name, cases, asset_count
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            pid = r.get("person_id")
            name = r.get("name")
            cases = r.get("cases") or []
            insight = InsightItem(
                insight_id=generate_insight_id("BRIDGE_NODE", f"{pid}_{'-'.join(sorted(cases))}"),
                insight_type=InsightType.BRIDGE_NODE,
                title=f"Bridge Actor Connecting Investigations: {name}",
                severity=InsightSeverity.CRITICAL,
                observed_facts=[
                    f"Person '{name}' ({pid}) links {len(cases)} distinct cases: {', '.join(cases)}.",
                    f"Controls {r.get('asset_count', 0)} documented operational assets across these cases."
                ],
                derived_interpretation="Key network bridge or coordinator facilitating inter-syndicate operations.",
                alternative_explanations=[
                    "Intermediary professional service provider (broker, lawyer, accountant)",
                    "Shared social acquaintance without criminal conspiracy"
                ],
                entities_involved=[pid],
                case_ids=cases,
                metadata={"asset_count": r.get("asset_count")}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_transfer_chains(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH p = (b1:BankAccount)-[r1:TRANSFERRED_TO]->(b2:BankAccount)-[r2:TRANSFERRED_TO]->(b3:BankAccount)
        WHERE b1 <> b3
          AND ($case_id IS NULL OR any(c IN b1.case_ids WHERE c = $case_id) OR any(c IN b2.case_ids WHERE c = $case_id) OR any(c IN b3.case_ids WHERE c = $case_id))
        RETURN b1.account_number as acc1, b2.account_number as acc2, b3.account_number as acc3,
               r1.amount as amt1, r2.amount as amt2,
               r1.timestamp as t1, r2.timestamp as t2,
               b1.case_ids as c1, b2.case_ids as c2, b3.case_ids as c3
        LIMIT 25
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            acc1, acc2, acc3 = r.get("acc1"), r.get("acc2"), r.get("acc3")
            all_cases = list(set((r.get("c1") or []) + (r.get("c2") or []) + (r.get("c3") or [])))
            insight = InsightItem(
                insight_id=generate_insight_id("TRANSFER_CHAIN", f"{acc1}_{acc2}_{acc3}"),
                insight_type=InsightType.TRANSFER_CHAIN,
                title=f"Multi-Hop Financial Transfer Chain: {acc1} -> {acc2} -> {acc3}",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"Fund transfer sequence detected: {acc1} -> {acc2} (Amount: {r.get('amt1')}) -> {acc3} (Amount: {r.get('amt2')}).",
                    f"Timestamps: Stage 1 ({r.get('t1')}), Stage 2 ({r.get('t2')})."
                ],
                derived_interpretation="Rapid multi-hop fund movement consistent with financial layering or pass-through mule accounts.",
                alternative_explanations=[
                    "Routine commercial supplier payments or subcontracting disbursements",
                    "Payroll or treasury liquidity rebalancing"
                ],
                entities_involved=[acc1, acc2, acc3],
                case_ids=all_cases,
                metadata={"hop_count": 2, "amounts": [r.get("amt1"), r.get("amt2")]}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_high_fan_in(session: Session, case_id: Optional[str] = None, threshold: int = 3) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (src:BankAccount)-[r:TRANSFERRED_TO]->(target:BankAccount)
        WITH target, count(DISTINCT src) as in_degree, sum(r.amount) as total_in, target.case_ids as cases
        WHERE in_degree >= $threshold
          AND ($case_id IS NULL OR $case_id IN cases)
        RETURN target.account_number as account_number, target.holder_name as holder,
               in_degree, total_in, cases
        """
        results = session.run(cypher, {"case_id": case_id, "threshold": threshold}).data()
        for r in results:
            acc = r.get("account_number")
            cases = r.get("cases") or []
            insight = InsightItem(
                insight_id=generate_insight_id("HIGH_FAN_IN", f"{acc}_{r.get('in_degree')}"),
                insight_type=InsightType.HIGH_FAN_IN,
                title=f"High Fan-In Account (Collection Funnel): {acc}",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"BankAccount '{acc}' (Holder: {r.get('holder') or 'Unknown'}) receives transfers from {r.get('in_degree')} distinct accounts.",
                    f"Total inflow volume documented: {r.get('total_in')} across {len(cases)} case(s)."
                ],
                derived_interpretation="Account functions as a central fund aggregator or pooling node for illicit collections.",
                alternative_explanations=[
                    "E-commerce merchant payment aggregator",
                    "Crowdfunding or charity collection account"
                ],
                entities_involved=[acc],
                case_ids=cases,
                metadata={"in_degree": r.get("in_degree"), "total_inflow": r.get("total_in")}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_high_fan_out(session: Session, case_id: Optional[str] = None, threshold: int = 3) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (src:BankAccount)-[r:TRANSFERRED_TO]->(target:BankAccount)
        WITH src, count(DISTINCT target) as out_degree, sum(r.amount) as total_out, src.case_ids as cases
        WHERE out_degree >= $threshold
          AND ($case_id IS NULL OR $case_id IN cases)
        RETURN src.account_number as account_number, src.holder_name as holder,
               out_degree, total_out, cases
        """
        results = session.run(cypher, {"case_id": case_id, "threshold": threshold}).data()
        for r in results:
            acc = r.get("account_number")
            cases = r.get("cases") or []
            insight = InsightItem(
                insight_id=generate_insight_id("HIGH_FAN_OUT", f"{acc}_{r.get('out_degree')}"),
                insight_type=InsightType.HIGH_FAN_OUT,
                title=f"High Fan-Out Account (Fund Dispersal): {acc}",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"BankAccount '{acc}' (Holder: {r.get('holder') or 'Unknown'}) disburses funds to {r.get('out_degree')} distinct accounts.",
                    f"Total outflow volume documented: {r.get('total_out')} across {len(cases)} case(s)."
                ],
                derived_interpretation="Account functions as a distribution hub dispersing funds to multiple downstream operatives or mules.",
                alternative_explanations=[
                    "Corporate payroll or vendor settlement account",
                    "Dividend or prize distribution system"
                ],
                entities_involved=[acc],
                case_ids=cases,
                metadata={"out_degree": r.get("out_degree"), "total_outflow": r.get("total_out")}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_infrastructure_reuse(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        # Case A: Shared IMEI across multiple phone numbers
        cypher_imei = """
        MATCH (ph:Phone)
        WHERE ph.imei IS NOT NULL AND ph.imei <> ''
        WITH ph.imei as imei, collect(ph.phone_number) as phones, collect(DISTINCT ph.case_ids) as nested_cases
        WHERE size(phones) > 1
        RETURN imei, phones, nested_cases
        """
        results_imei = session.run(cypher_imei).data()
        for r in results_imei:
            imei = r.get("imei")
            if not imei:
                continue
            phones = r.get("phones") or []
            cases = list(set([c for sub in r.get("nested_cases", []) for c in sub if c]))
            if case_id and case_id not in cases:
                continue
            insight = InsightItem(
                insight_id=generate_insight_id("INFRASTRUCTURE_REUSE", f"imei_{imei}"),
                insight_type=InsightType.INFRASTRUCTURE_REUSE,
                title=f"Hardware IMEI Reused Across Multiple SIMs: {imei}",
                severity=InsightSeverity.CRITICAL,
                observed_facts=[
                    f"Hardware IMEI '{imei}' is shared across {len(phones)} phone numbers: {', '.join(phones)}.",
                    f"Appears across cases: {', '.join(cases) if cases else 'N/A'}."
                ],
                derived_interpretation="Single physical handset used with multiple rotating SIM cards (burner SIM rotation).",
                alternative_explanations=[
                    "Refurbished/second-hand device with cloned IMEI",
                    "Dual-SIM mobile phone legitimately hosting multiple personal numbers"
                ],
                entities_involved=phones,
                case_ids=cases,
                metadata={"imei": imei}
            )
            insights.append(insight)

        # Case B: Shared IP address across multiple persons/phones
        cypher_ip = """
        MATCH (entity)-[:LINKED_TO_IP]->(ip:IPAddress)
        WITH ip, collect(DISTINCT coalesce(entity.person_id, entity.phone_number, entity.handle_id)) as entities,
             collect(DISTINCT ip.case_ids) as nested_cases
        WHERE size(entities) > 1
        RETURN ip.ip_address as ip_address, entities, nested_cases
        """
        results_ip = session.run(cypher_ip).data()
        for r in results_ip:
            ip = r.get("ip_address")
            if not ip:
                continue
            entities = r.get("entities") or []
            cases = list(set([c for sub in r.get("nested_cases", []) for c in sub if c]))
            if case_id and case_id not in cases:
                continue
            insight = InsightItem(
                insight_id=generate_insight_id("INFRASTRUCTURE_REUSE", f"ip_{ip}"),
                insight_type=InsightType.INFRASTRUCTURE_REUSE,
                title=f"Shared IP Infrastructure: {ip}",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"IP Address '{ip}' is actively linked to {len(entities)} distinct entities: {', '.join(entities)}.",
                    f"Associated case(s): {', '.join(cases) if cases else 'N/A'}."
                ],
                derived_interpretation="Entities share physical network location, VPN exit node, or common cyber infrastructure.",
                alternative_explanations=[
                    "Public Wi-Fi, cybercafe, or commercial VPN gateway",
                    "Carrier-Grade NAT (CGNAT) dynamic IP allocation"
                ],
                entities_involved=entities,
                case_ids=cases,
                metadata={"ip_address": ip}
            )
            insights.append(insight)

        return insights

    @staticmethod
    def detect_possible_co_location(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (e1)-[l1:LOCATED_AT]->(loc:Location)<-[l2:LOCATED_AT]-(e2)
        WHERE elementId(e1) < elementId(e2)
          AND ($case_id IS NULL OR $case_id IN loc.case_ids OR $case_id IN l1.case_id OR $case_id IN l2.case_id)
        RETURN loc.location_id as loc_id, loc.name as loc_name,
               coalesce(e1.person_id, e1.vin, e1.phone_number) as entity1,
               coalesce(e2.person_id, e2.vin, e2.phone_number) as entity2,
               l1.timestamp as t1, l2.timestamp as t2,
               loc.case_ids as cases
        LIMIT 20
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            loc_id = r.get("loc_id")
            e1, e2 = r.get("entity1"), r.get("entity2")
            cases = r.get("cases") or []
            insight = InsightItem(
                insight_id=generate_insight_id("POSSIBLE_CO_LOCATION", f"{loc_id}_{e1}_{e2}"),
                insight_type=InsightType.POSSIBLE_CO_LOCATION,
                title=f"Co-Location at Location: {r.get('loc_name') or loc_id}",
                severity=InsightSeverity.MEDIUM,
                observed_facts=[
                    f"Entities '{e1}' and '{e2}' were both recorded at Location '{r.get('loc_name') or loc_id}'.",
                    f"Observed timestamps: Entity 1 ({r.get('t1')}), Entity 2 ({r.get('t2')})."
                ],
                derived_interpretation="Possible physical meeting, surveillance overlap, or joint presence at critical landmark.",
                alternative_explanations=[
                    "High-footfall public venue (mall, transit hub, airport)",
                    "Coincidental visits separated in time"
                ],
                entities_involved=[str(e1), str(e2)],
                case_ids=cases,
                metadata={"location_id": loc_id, "location_name": r.get("loc_name")}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_cross_domain_paths(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (p1:Person)-[:OWNS]->(ph1:Phone)-[c:CALLED]->(ph2:Phone)<-[:OWNS]-(p2:Person)-[:OWNS]->(b1:BankAccount)-[t:TRANSFERRED_TO]->(b2:BankAccount)
        WHERE p1 <> p2
          AND ($case_id IS NULL OR $case_id IN p1.case_ids OR $case_id IN p2.case_ids)
        RETURN p1.name as p1_name, p1.person_id as p1_id,
               p2.name as p2_name, p2.person_id as p2_id,
               ph1.phone_number as ph1, ph2.phone_number as ph2,
               b1.account_number as b1, b2.account_number as b2,
               p1.case_ids as c1, p2.case_ids as c2
        LIMIT 10
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            p1_id, p2_id = r.get("p1_id"), r.get("p2_id")
            cases = list(set((r.get("c1") or []) + (r.get("c2") or [])))
            insight = InsightItem(
                insight_id=generate_insight_id("CROSS_DOMAIN_PATH", f"{p1_id}_{p2_id}_{r.get('b1')}_{r.get('b2')}"),
                insight_type=InsightType.CROSS_DOMAIN_PATH,
                title=f"Cross-Domain Telecom & Financial Link: {r.get('p1_name')} <-> {r.get('p2_name')}",
                severity=InsightSeverity.CRITICAL,
                observed_facts=[
                    f"Person '{r.get('p1_name')}' called Person '{r.get('p2_name')}' via phones {r.get('ph1')} -> {r.get('ph2')}.",
                    f"Person '{r.get('p2_name')}' subsequently transferred funds from Account {r.get('b1')} -> {r.get('b2')}."
                ],
                derived_interpretation="Correlated telephonic coordination directly preceding financial settlement between subjects.",
                alternative_explanations=[
                    "Legitimate commercial phone confirmation followed by supplier invoice settlement"
                ],
                entities_involved=[p1_id, p2_id, r.get("ph1"), r.get("ph2"), r.get("b1"), r.get("b2")],
                case_ids=cases,
                metadata={"domains": ["TELECOM", "BANKING"]}
            )
            insights.append(insight)
        return insights

    @staticmethod
    def detect_prior_case_links(session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        insights = []
        cypher = """
        MATCH (p:Person)-[:HAS_PRIOR_CASE]->(pc:PriorCase)
        WHERE ($case_id IS NULL OR $case_id IN p.case_ids)
        RETURN p.person_id as person_id, p.name as name,
               pc.prior_case_id as prior_id, pc.case_number as case_num,
               pc.offense as offense, pc.jurisdiction as jurisdiction,
               pc.status as status, pc.year as year,
               p.case_ids as case_ids
        """
        results = session.run(cypher, {"case_id": case_id}).data()
        for r in results:
            pid = r.get("person_id")
            name = r.get("name")
            cases = r.get("case_ids") or []
            insight = InsightItem(
                insight_id=generate_insight_id("PRIOR_CASE_LINK", f"{pid}_{r.get('prior_id')}"),
                insight_type=InsightType.PRIOR_CASE_LINK,
                title=f"Prior Criminal Record Match: {name} ({r.get('offense') or 'Prior Offense'})",
                severity=InsightSeverity.HIGH,
                observed_facts=[
                    f"Person '{name}' ({pid}) is linked to Prior Record '{r.get('case_num')}' in {r.get('jurisdiction') or 'Unknown'}.",
                    f"Offense: {r.get('offense') or 'N/A'}, Status: {r.get('status') or 'RECORDED'}, Year: {r.get('year') or 'N/A'}."
                ],
                derived_interpretation="Active subject has documented history of prior offenses in law enforcement archives.",
                alternative_explanations=[
                    "Historical case ended in full acquittal or exoneration",
                    "Homonymous subject identity confusion (requires verification against national ID / biometrics)"
                ],
                entities_involved=[e for e in [pid, r.get("prior_id") or r.get("case_num")] if e],

                case_ids=cases,
                metadata={"case_number": r.get("case_num"), "offense": r.get("offense")}
            )
            insights.append(insight)
        return insights

    @classmethod
    def run_all_detectors(cls, session: Session, case_id: Optional[str] = None) -> List[InsightItem]:
        all_insights: List[InsightItem] = []
        detectors = [
            cls.detect_shared_entities,
            cls.detect_cross_case_links,
            cls.detect_bridge_nodes,
            cls.detect_transfer_chains,
            cls.detect_high_fan_in,
            cls.detect_high_fan_out,
            cls.detect_infrastructure_reuse,
            cls.detect_possible_co_location,
            cls.detect_cross_domain_paths,
            cls.detect_prior_case_links,
        ]
        for detector in detectors:
            try:
                detected = detector(session, case_id)
                all_insights.extend(detected)
            except Exception as e:
                logger.warning(f"Error running detector {detector.__name__}: {e}")

        # Deduplicate insights by insight_id
        unique_insights: Dict[str, InsightItem] = {}
        for ins in all_insights:
            unique_insights[ins.insight_id] = ins
        return list(unique_insights.values())

