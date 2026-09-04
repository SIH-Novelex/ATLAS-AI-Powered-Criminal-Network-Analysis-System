from typing import Dict, Any, List, Optional
from neo4j import Session
from backend.models.rankings import RankingItem, RankingsResponse
from backend.logging_config import logger


class RankingService:
    """
    Centrality & Entity Rankings Service using Cypher with native GDS procedure support and fallback.
    """

    @classmethod
    def get_rankings(
        cls,
        session: Session,
        metric: str = "degree",
        label: Optional[str] = "Person",
        limit: int = 10,
        case_id: Optional[str] = None
    ) -> RankingsResponse:
        metric_clean = metric.lower().strip()
        label_clean = label.strip() if label else None

        if metric_clean == "degree":
            return cls._calculate_degree(session, label_clean, limit, case_id)
        elif metric_clean == "weighted_degree":
            return cls._calculate_weighted_degree(session, label_clean, limit, case_id)
        elif metric_clean == "cross_case_relevance":
            return cls._calculate_cross_case_relevance(session, label_clean, limit, case_id)
        elif metric_clean in ["pagerank", "betweenness"]:
            return cls._calculate_gds_metric(session, metric_clean, label_clean, limit, case_id)
        else:
            return RankingsResponse(
                metric=metric,
                label=label,
                supported=False,
                reason=f"Unknown metric '{metric}'. Supported metrics: degree, weighted_degree, betweenness, pagerank, cross_case_relevance.",
                rankings=[]
            )

    @classmethod
    def _calculate_degree(cls, session: Session, label: Optional[str], limit: int, case_id: Optional[str]) -> RankingsResponse:
        cypher = """
        MATCH (n)
        WHERE ($label IS NULL OR $label IN labels(n))
          AND ($case_id IS NULL OR $case_id IN n.case_ids)
        OPTIONAL MATCH (n)-[r]-()
        WITH n, count(r) as degree
        ORDER BY degree DESC
        LIMIT $limit
        RETURN coalesce(n.person_id, n.phone_number, n.account_number, n.vin, n.handle_id, n.ip_address, n.location_id, elementId(n)) as id,
               coalesce(n.name, n.phone_number, n.account_number, n.vin, n.handle, n.ip_address) as name,
               labels(n) as labels,
               toFloat(degree) as score,
               n.case_ids as case_ids
        """
        rows = session.run(cypher, {"label": label, "limit": limit, "case_id": case_id}).data()
        rankings = []
        for idx, r in enumerate(rows, 1):
            rankings.append(RankingItem(
                id=str(r["id"]),
                name=r.get("name"),
                labels=r.get("labels") or [],
                score=float(r["score"]),
                rank=idx,
                case_ids=r.get("case_ids") or [],
                details={"degree": int(r["score"])}
            ))
        return RankingsResponse(
            metric="degree",
            label=label,
            supported=True,
            total_ranked=len(rankings),
            rankings=rankings
        )

    @classmethod
    def _calculate_weighted_degree(cls, session: Session, label: Optional[str], limit: int, case_id: Optional[str]) -> RankingsResponse:
        cypher = """
        MATCH (n)
        WHERE ($label IS NULL OR $label IN labels(n))
          AND ($case_id IS NULL OR $case_id IN n.case_ids)
        OPTIONAL MATCH (n)-[r]-()
        WITH n, count(r) as raw_degree,
             sum(coalesce(r.amount, 0.0) + coalesce(r.duration_seconds, 0) * 0.1 + 1.0) as weighted_score
        ORDER BY weighted_score DESC
        LIMIT $limit
        RETURN coalesce(n.person_id, n.phone_number, n.account_number, n.vin, n.handle_id, n.ip_address, elementId(n)) as id,
               coalesce(n.name, n.phone_number, n.account_number, n.vin, n.handle) as name,
               labels(n) as labels,
               toFloat(weighted_score) as score,
               raw_degree,
               n.case_ids as case_ids
        """
        rows = session.run(cypher, {"label": label, "limit": limit, "case_id": case_id}).data()
        rankings = []
        for idx, r in enumerate(rows, 1):
            rankings.append(RankingItem(
                id=str(r["id"]),
                name=r.get("name"),
                labels=r.get("labels") or [],
                score=round(float(r["score"]), 2),
                rank=idx,
                case_ids=r.get("case_ids") or [],
                details={"raw_degree": int(r["raw_degree"])}
            ))
        return RankingsResponse(
            metric="weighted_degree",
            label=label,
            supported=True,
            total_ranked=len(rankings),
            rankings=rankings
        )

    @classmethod
    def _calculate_cross_case_relevance(cls, session: Session, label: Optional[str], limit: int, case_id: Optional[str]) -> RankingsResponse:
        cypher = """
        MATCH (n)
        WHERE ($label IS NULL OR $label IN labels(n))
          AND size(n.case_ids) > 0
          AND ($case_id IS NULL OR $case_id IN n.case_ids)
        OPTIONAL MATCH (n)-[r]-()
        WITH n, size(n.case_ids) as case_count, count(r) as total_degree
        WITH n, case_count, total_degree,
             (toFloat(case_count) * 10.0 + toFloat(total_degree)) as relevance_score
        ORDER BY relevance_score DESC
        LIMIT $limit
        RETURN coalesce(n.person_id, n.phone_number, n.account_number, n.vin, n.handle_id, n.ip_address, elementId(n)) as id,
               coalesce(n.name, n.phone_number, n.account_number, n.vin, n.handle) as name,
               labels(n) as labels,
               toFloat(relevance_score) as score,
               case_count,
               total_degree,
               n.case_ids as case_ids
        """
        rows = session.run(cypher, {"label": label, "limit": limit, "case_id": case_id}).data()
        rankings = []
        for idx, r in enumerate(rows, 1):
            rankings.append(RankingItem(
                id=str(r["id"]),
                name=r.get("name"),
                labels=r.get("labels") or [],
                score=round(float(r["score"]), 2),
                rank=idx,
                case_ids=r.get("case_ids") or [],
                details={"case_count": int(r["case_count"]), "degree": int(r["total_degree"])}
            ))
        return RankingsResponse(
            metric="cross_case_relevance",
            label=label,
            supported=True,
            total_ranked=len(rankings),
            rankings=rankings
        )

    @classmethod
    def _calculate_gds_metric(cls, session: Session, metric: str, label: Optional[str], limit: int, case_id: Optional[str]) -> RankingsResponse:
        # Whitelist validation to prevent Cypher injection via dynamic label
        ALLOWED_LABELS = {
            "Person", "Phone", "BankAccount", "Vehicle", "SocialHandle",
            "IPAddress", "Location", "CellTower", "Transaction", "FIR",
            "PriorCase", "SourceRecord", "Case"
        }
        target_label = label or "Person"
        if target_label not in ALLOWED_LABELS:
            return RankingsResponse(
                metric=metric,
                label=label,
                supported=False,
                reason=f"Invalid label '{target_label}'. Allowed labels: {', '.join(sorted(ALLOWED_LABELS))}.",
                total_ranked=0,
                rankings=[]
            )

        # 1. Verify GDS is installed and available
        try:
            session.run("CALL gds.version() YIELD version RETURN version").single()
        except Exception:
            logger.info(f"GDS is not available on Neo4j for metric '{metric}'. Returning supported:false.")
            return RankingsResponse(
                metric=metric,
                label=label,
                supported=False,
                reason="Neo4j Graph Data Science (GDS) library is not installed or available on this Neo4j instance.",
                total_ranked=0,
                rankings=[]
            )

        # GDS is available: run stream query
        try:
            graph_name = f"gds_temp_{metric}_{target_label}"
            
            # Drop graph if exists
            session.run(f"CALL gds.graph.drop('{graph_name}', false) YIELD graphName")
            
            # Project graph
            session.run(f"""
            CALL gds.graph.project(
                '{graph_name}',
                '{target_label}',
                '*'
            )
            """)

            proc = "gds.pageRank.stream" if metric == "pagerank" else "gds.betweenness.stream"
            gds_cypher = f"""
            CALL {proc}('{graph_name}')
            YIELD nodeId, score
            WITH gds.util.asNode(nodeId) AS n, score
            ORDER BY score DESC
            LIMIT $limit
            RETURN coalesce(n.person_id, n.phone_number, n.account_number, n.vin, n.handle_id, elementId(n)) as id,
                   coalesce(n.name, n.phone_number, n.account_number, n.vin, n.handle) as name,
                   labels(n) as labels,
                   toFloat(score) as score,
                   n.case_ids as case_ids
            """
            rows = session.run(gds_cypher, {"limit": limit}).data()

            # Clean up projection
            session.run(f"CALL gds.graph.drop('{graph_name}', false) YIELD graphName")

            rankings = []
            for idx, r in enumerate(rows, 1):
                rankings.append(RankingItem(
                    id=str(r["id"]),
                    name=r.get("name"),
                    labels=r.get("labels") or [],
                    score=round(float(r["score"]), 4),
                    rank=idx,
                    case_ids=r.get("case_ids") or [],
                    details={"raw_score": float(r["score"])}
                ))

            return RankingsResponse(
                metric=metric,
                label=label,
                supported=True,
                total_ranked=len(rankings),
                rankings=rankings
            )
        except Exception as e:
            logger.warning(f"Error executing GDS metric '{metric}': {e}")
            return RankingsResponse(
                metric=metric,
                label=label,
                supported=False,
                reason=f"Failed to execute GDS procedure: {str(e)}",
                total_ranked=0,
                rankings=[]
            )

