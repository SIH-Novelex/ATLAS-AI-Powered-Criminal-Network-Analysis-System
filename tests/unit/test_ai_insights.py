import pytest
from unittest.mock import patch, MagicMock
from backend.models.insights import CaseAIInsightResponse
from backend.services.gemini_service import GeminiService


def test_ai_insights_endpoint_success(client):
    """Calling GET /api/cases/{case_id}/ai-insights returns structured AI case dossier."""
    mock_resp = CaseAIInsightResponse(
        case_id="CASE_TEST_01",
        case_name="Operation Test Network",
        status="OPEN",
        risk_level="HIGH",
        executive_summary="Operation Test Network exhibits complex syndicate behavior.",
        modus_operandi="Layered digital payments and burner phones.",
        key_suspects=["Suspect A", "Suspect B"],
        critical_anomalies=["Velocity threshold breach"],
        investigative_leads=["Issue Section 91 CrPC notice"],
        ai_model="gemini-2.5-flash"
    )

    with patch.object(GeminiService, "generate_case_brief", return_value=mock_resp):
        response = client.get("/api/cases/CASE_TEST_01/ai-insights")
        assert response.status_code == 200
        data = response.json()
        assert data["case_id"] == "CASE_TEST_01"
        assert data["ai_model"] == "gemini-2.5-flash"
        assert data["risk_level"] == "HIGH"
        assert "Suspect A" in data["key_suspects"]
        assert len(data["investigative_leads"]) > 0


def test_ai_insights_not_found(client):
    """Calling AI insights for non-existent case returns 404."""
    with patch.object(GeminiService, "generate_case_brief", side_effect=ValueError("Case 'NON_EXISTENT' not found")):
        response = client.get("/api/cases/NON_EXISTENT/ai-insights")
        assert response.status_code == 404


def test_graph_copilot_endpoint_success(client):
    """Calling POST /api/graph/ai-query returns structured AI Copilot answer."""
    from backend.models.insights import GraphAIChatResponse
    mock_chat_resp = GraphAIChatResponse(
        answer="Primary Target is Devendra Sharma coordinating 3 suspect accounts.",
        key_findings=["Primary Target: Devendra Sharma", "Degree: 12"],
        ai_model="gemini-2.5-flash"
    )

    with patch.object(GeminiService, "answer_graph_query", return_value=mock_chat_resp):
        response = client.post("/api/graph/ai-query", json={
            "question": "Who is the kingpin?",
            "case_id": "CASE_TEST_01",
            "nodes_count": 45,
            "edges_count": 52
        })
        assert response.status_code == 200
        data = response.json()
        assert "Devendra Sharma" in data["answer"]
        assert len(data["key_findings"]) == 2
        assert data["ai_model"] == "gemini-2.5-flash"


def test_graph_copilot_empty_question(client):
    """Calling POST /api/graph/ai-query with empty question returns 400."""
    response = client.post("/api/graph/ai-query", json={"question": "   "})
    assert response.status_code == 400
    assert "Question is required" in response.json()["detail"]


def test_graph_copilot_heuristic_fallback(monkeypatch):
    """GeminiService.answer_graph_query falls back cleanly to graph heuristic inference when offline."""
    from backend.models.insights import GraphAIChatResponse
    from backend.services import gemini_service
    monkeypatch.setattr(gemini_service, "HAS_GENAI", False)

    mock_session = MagicMock()
    mock_session.run.side_effect = [
        MagicMock(single=lambda: {"name": "Operation Syndicate"}),  # case row
        MagicMock(data=lambda: [{"name": "Rajiv Sen", "id": "P-01", "roles": ["Kingpin"], "degree": 14}]),  # people
        MagicMock(data=lambda: [{"phone": "+919876543210", "degree": 8}]),  # phones
        MagicMock(data=lambda: [{"account": "ACC-999", "bank": "HDFC", "holder": "Rajiv Sen", "degree": 6}]),  # accounts
        MagicMock(data=lambda: [{"src": "ACC-1", "tgt": "ACC-999", "amount": 500000}]),  # transactions
    ]
    resp = GeminiService.answer_graph_query(
        session=mock_session,
        question="Who is the main kingpin?",
        case_id="CASE-01"
    )
    assert isinstance(resp, GraphAIChatResponse)
    assert "Rajiv Sen" in resp.answer
    assert any("Primary Target" in k for k in resp.key_findings)
    assert "Demonstration Engine" in resp.ai_model

