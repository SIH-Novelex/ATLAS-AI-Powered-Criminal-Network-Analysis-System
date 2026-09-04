from typing import Dict, Any, List, Optional, Union, Tuple
from neo4j import Session
from backend.models.path import ShortestPathResponse, AmbiguityCandidate, AmbiguousPathResponse
from backend.config import settings
from backend.logging_config import logger


class PathService:
    """
    Ambiguity-Safe Shortest Path Service with strict Person entity matching.
    """

    @classmethod
    def find_shortest_path(
        cls,
        session: Session,
        suspect_name: Optional[str] = None,
        victim_name: Optional[str] = None,
        suspect_id: Optional[str] = None,
        victim_id: Optional[str] = None,
        max_depth: Optional[int] = None
    ) -> Union[ShortestPathResponse, AmbiguousPathResponse]:
        depth = max_depth or settings.DEFAULT_MAX_PATH_DEPTH
        
        # 1. Resolve Suspect Node(s)
        suspect_candidates = cls._resolve_person(session, person_id=suspect_id, name=suspect_name)
        if len(suspect_candidates) == 0:
            return ShortestPathResponse(
                nodes=[],
                relationships=[],
                path_length=0,
                summary=f"Suspect person not found with identifier '{suspect_id or suspect_name}'."
            )
        if len(suspect_candidates) > 1:
            logger.warning(f"Ambiguity detected for suspect '{suspect_name}': {len(suspect_candidates)} candidates found.")
            return AmbiguousPathResponse(
                ambiguous=True,
                message=f"Multiple Person entities match suspect '{suspect_name}'. Please specify 'suspect_id' to disambiguate.",
                candidates=suspect_candidates
            )
        
        target_suspect_id = suspect_candidates[0].person_id

        # 2. Resolve Victim Node(s)
        victim_candidates = cls._resolve_person(session, person_id=victim_id, name=victim_name)
        if len(victim_candidates) == 0:
            return ShortestPathResponse(
                nodes=[],
                relationships=[],
                path_length=0,
                summary=f"Victim person not found with identifier '{victim_id or victim_name}'."
            )
        if len(victim_candidates) > 1:
            logger.warning(f"Ambiguity detected for victim '{victim_name}': {len(victim_candidates)} candidates found.")
            return AmbiguousPathResponse(
                ambiguous=True,
                message=f"Multiple Person entities match victim '{victim_name}'. Please specify 'victim_id' to disambiguate.",
                candidates=victim_candidates
            )

        target_victim_id = victim_candidates[0].person_id

        # 3. Execute Shortest Path Cypher
        # Dynamic variable-length depth safely bounded
        bounded_depth = max(1, min(depth, 15))
        cypher = f"""
        MATCH (a:Person {{person_id: $s_id}}), (b:Person {{person_id: $v_id}})
        MATCH p = shortestPath((a)-[*..{bounded_depth}]-(b))
        RETURN p
        """
        result = session.run(cypher, {"s_id": target_suspect_id, "v_id": target_victim_id})
        record = result.single()

        if not record or not record.get("p"):
            return ShortestPathResponse(
                nodes=[],
                relationships=[],
                path_length=0,
                summary=f"No path found between suspect '{suspect_candidates[0].name}' and victim '{victim_candidates[0].name}' within depth {bounded_depth}."
            )

        neo_path = record["p"]
        nodes_list = []
        relationships_list = []
        path_labels_sequence = []

        for node in neo_path.nodes:
            n_data = dict(node)
            node_labels = list(node.labels)
            node_id = str(
                n_data.get("person_id")
                or n_data.get("case_id")
                or n_data.get("phone_number")
                or n_data.get("account_number")
                or n_data.get("vin")
                or n_data.get("license_plate")
                or n_data.get("handle_id")
                or n_data.get("ip_address")
                or n_data.get("location_id")
                or n_data.get("cell_tower_id")
                or n_data.get("fir_id")
                or n_data.get("prior_case_id")
                or n_data.get("source_record_id")
                or getattr(node, "element_id", str(node.id))
            )
            node_name = str(
                n_data.get("name")
                or n_data.get("case_name")
                or n_data.get("phone_number")
                or n_data.get("account_number")
                or n_data.get("vin")
                or n_data.get("license_plate")
                or n_data.get("case_id")
                or n_data.get("fir_number")
                or node_id
            )
            
            nodes_list.append({
                "id": str(node_id),
                "element_id": getattr(node, "element_id", str(node.id)),
                "labels": node_labels,
                "name": str(node_name),
                "properties": n_data
            })
            path_labels_sequence.append(f"{node_labels[0] if node_labels else 'Node'}({node_name})")

        for rel in neo_path.relationships:
            r_data = dict(rel)
            relationships_list.append({
                "id": getattr(rel, "element_id", str(rel.id)),
                "type": rel.type,
                "start_node": getattr(rel.start_node, "element_id", str(rel.start_node.id)),
                "end_node": getattr(rel.end_node, "element_id", str(rel.end_node.id)),
                "properties": r_data
            })

        summary = " -> ".join(path_labels_sequence)
        return ShortestPathResponse(
            ambiguous=False,
            nodes=nodes_list,
            relationships=relationships_list,
            path_length=len(relationships_list),
            summary=summary
        )

    @classmethod
    def _resolve_person(cls, session: Session, person_id: Optional[str] = None, name: Optional[str] = None) -> List[AmbiguityCandidate]:
        if person_id:
            cypher = """
            MATCH (p:Person {person_id: $person_id})
            RETURN p.person_id as person_id, p.name as name, p.aliases as aliases, p.dob as dob, p.case_ids as case_ids
            """
            rows = session.run(cypher, {"person_id": person_id}).data()
        elif name:
            cypher = """
            MATCH (p:Person)
            WHERE toLower(p.name) = toLower($name) OR toLower($name) IN [a IN p.aliases | toLower(a)]
            RETURN p.person_id as person_id, p.name as name, p.aliases as aliases, p.dob as dob, p.case_ids as case_ids
            """
            rows = session.run(cypher, {"name": name}).data()
        else:
            return []

        candidates = []
        for r in rows:
            candidates.append(AmbiguityCandidate(
                person_id=str(r["person_id"]),
                name=str(r["name"]),
                aliases=r.get("aliases") or [],
                dob=r.get("dob"),
                case_ids=r.get("case_ids") or []
            ))
        return candidates

