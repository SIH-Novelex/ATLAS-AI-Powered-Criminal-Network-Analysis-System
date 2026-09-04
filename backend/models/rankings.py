from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RankingItem(BaseModel):
    id: str = Field(..., description="Node unique ID or database ID")
    name: Optional[str] = Field(default=None, description="Entity name or label identifier")
    labels: List[str] = Field(default_factory=list, description="Node labels")
    score: float = Field(..., description="Calculated centrality / ranking score")
    rank: int = Field(..., description="1-indexed rank position")
    case_ids: List[str] = Field(default_factory=list, description="Case IDs associated with entity")
    details: Dict[str, Any] = Field(default_factory=dict, description="Supplementary metric breakdown")


class RankingsResponse(BaseModel):
    metric: str = Field(..., description="Algorithm used: degree, weighted_degree, betweenness, pagerank, cross_case_relevance")
    label: Optional[str] = Field(default=None, description="Filtered node label")
    supported: bool = Field(default=True, description="Whether metric is supported on this database instance")
    reason: Optional[str] = Field(default=None, description="Explanation if algorithm is unsupported (e.g. GDS not installed)")
    total_ranked: int = Field(default=0, description="Count of returned rankings")
    rankings: List[RankingItem] = Field(default_factory=list, description="Ranked entities")

