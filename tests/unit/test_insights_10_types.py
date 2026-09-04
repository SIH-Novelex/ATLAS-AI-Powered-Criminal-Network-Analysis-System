import pytest
from unittest.mock import MagicMock
from backend.services.insights_engine import InsightsEngine
from backend.models.insights import InsightType, InsightSeverity, InsightItem


class CustomMockSession:
    def __init__(self, return_data):
        self._return_data = return_data

    def run(self, query, parameters=None):
        mock_result = MagicMock()
        mock_result.data.return_value = self._return_data
        return mock_result


def test_insight_shared_entity():
    session = CustomMockSession([
        {
            "labels": ["Phone"],
            "identifier": "+919876543210",
            "case_ids": ["CASE-2024-001", "CASE-2024-002"],
            "entity_id": "+919876543210"
        }
    ])
    insights = InsightsEngine.detect_shared_entities(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.SHARED_ENTITY
    assert "+919876543210" in insights[0].observed_facts[0]
    assert len(insights[0].case_ids) == 2


def test_insight_cross_case_link():
    session = CustomMockSession([
        {
            "src_phone": "+919876543210",
            "src_cases": ["CASE-01"],
            "dst_phone": "+919876543999",
            "dst_cases": ["CASE-02"],
            "timestamp": "2024-10-20T10:00:00Z",
            "duration": 300,
            "call_case": "CASE-01"
        }
    ])
    insights = InsightsEngine.detect_cross_case_links(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.CROSS_CASE_LINK
    assert "+919876543210" in insights[0].title


def test_insight_bridge_node():
    session = CustomMockSession([
        {
            "person_id": "P-001",
            "name": "Devendra Sharma",
            "cases": ["CASE-01", "CASE-02"],
            "asset_count": 4
        }
    ])
    insights = InsightsEngine.detect_bridge_nodes(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.BRIDGE_NODE
    assert "Devendra Sharma" in insights[0].title


def test_insight_transfer_chain():
    session = CustomMockSession([
        {
            "acc1": "ACC-001",
            "acc2": "ACC-002",
            "acc3": "ACC-003",
            "amt1": 500000.0,
            "amt2": 480000.0,
            "t1": "2024-10-14T10:00:00Z",
            "t2": "2024-10-14T14:00:00Z",
            "c1": ["CASE-01"],
            "c2": ["CASE-01"],
            "c3": ["CASE-01"]
        }
    ])
    insights = InsightsEngine.detect_transfer_chains(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.TRANSFER_CHAIN
    assert "ACC-001 -> ACC-002 -> ACC-003" in insights[0].title


def test_insight_high_fan_in():
    session = CustomMockSession([
        {
            "account_number": "ACC-POOL-999",
            "holder": "Shell Trading LLC",
            "in_degree": 5,
            "total_in": 12000000.0,
            "cases": ["CASE-01"]
        }
    ])
    insights = InsightsEngine.detect_high_fan_in(session, threshold=3)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.HIGH_FAN_IN
    assert "ACC-POOL-999" in insights[0].title


def test_insight_high_fan_out():
    session = CustomMockSession([
        {
            "account_number": "ACC-DISPERSE-111",
            "holder": "Hawala Hub",
            "out_degree": 6,
            "total_out": 9500000.0,
            "cases": ["CASE-02"]
        }
    ])
    insights = InsightsEngine.detect_high_fan_out(session, threshold=3)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.HIGH_FAN_OUT


def test_insight_infrastructure_reuse():
    # Mocking IMEI reuse
    session = CustomMockSession([
        {
            "imei": "IMEI-864201041122334",
            "phones": ["+919876543210", "+919876543211"],
            "nested_cases": [["CASE-01"], ["CASE-01"]]
        }
    ])
    insights = InsightsEngine.detect_infrastructure_reuse(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.INFRASTRUCTURE_REUSE
    assert "IMEI-864201041122334" in insights[0].title


def test_insight_possible_co_location():
    session = CustomMockSession([
        {
            "loc_id": "LOC-HOTEL-01",
            "loc_name": "Grand Park Hotel",
            "entity1": "P-002",
            "entity2": "P-003",
            "t1": "2024-10-15T19:00:00Z",
            "t2": "2024-10-15T19:05:00Z",
            "cases": ["CASE-01"]
        }
    ])
    insights = InsightsEngine.detect_possible_co_location(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.POSSIBLE_CO_LOCATION


def test_insight_cross_domain_paths():
    session = CustomMockSession([
        {
            "p1_name": "Devendra Sharma",
            "p1_id": "P-001",
            "p2_name": "Karan Singhania",
            "p2_id": "P-002",
            "ph1": "+919876543210",
            "ph2": "+919876543211",
            "b1": "ACC-001",
            "b2": "ACC-002",
            "c1": ["CASE-01"],
            "c2": ["CASE-01"]
        }
    ])
    insights = InsightsEngine.detect_cross_domain_paths(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.CROSS_DOMAIN_PATH


def test_insight_prior_case_link():
    session = CustomMockSession([
        {
            "person_id": "P-001",
            "name": "Devendra Sharma",
            "prior_id": "CRIM-001",
            "case_num": "CC-2019-450",
            "offense": "Extortion",
            "jurisdiction": "Bangalore",
            "status": "CONVICTED",
            "year": 2019,
            "case_ids": ["CASE-01"]
        }
    ])
    insights = InsightsEngine.detect_prior_case_links(session)
    assert len(insights) == 1
    assert insights[0].insight_type == InsightType.PRIOR_CASE_LINK
    assert "Extortion" in insights[0].title

