# 📑 Technical Documentation & System Specifications

The `docs/` directory contains comprehensive technical specifications, architectural blueprints, operations runbooks, and API streaming documentation for the **AI-Powered Criminal Network Analysis System**.

---

## 📂 Document Index

```
docs/
├── EVENTS_API.md                             # Real-time event streaming API specification
├── SUMMARY.md                                # Comprehensive system architecture & entity model
├── SYSTEM_ARCHITECTURE_AND_OPERATIONS_GUIDE.md # Operations runbook, deployment & failover guide
└── README.md                                 # This guide
```

---

## 📖 Document Summaries

### 1. [EVENTS_API.md](EVENTS_API.md)
- **Purpose**: Definitive API contract for real-time law enforcement event streaming.
- **Topics Covered**:
  - `POST /api/events` endpoint contract.
  - Event types: `FIR`, `ARREST`, `SEIZURE`, `INTERROGATION`, `LOCATION`, `VEHICLE`, `BANK_ACCOUNT`, `CDR_CALL`.
  - Idempotent MERGE operations and out-of-order temporal resolution.
  - Event envelope structure and sample payloads.

### 2. [SUMMARY.md](SUMMARY.md)
- **Purpose**: Full technical specification of the Criminal Network Analysis platform.
- **Topics Covered**:
  - Neo4j graph schema (Nodes: `Person`, `Vehicle`, `BankAccount`, `Location`, `Case`, etc.).
  - Edge definitions and relationship weights.
  - In-depth logic for all **10 Suspicious Pattern Detectors** (Hawala rings, mule accounts, SIM swaps, etc.).
  - Graph centrality formulas (PageRank, Betweenness Centrality, Degree Centrality).
  - Shortest path ambiguity resolution engine.

### 3. [SYSTEM_ARCHITECTURE_AND_OPERATIONS_GUIDE.md](SYSTEM_ARCHITECTURE_AND_OPERATIONS_GUIDE.md)
- **Purpose**: Production deployment, resilience, and operational management guide.
- **Topics Covered**:
  - Multi-tier system architecture (FastAPI, Neo4j, Gemini Flash, Blockchain Ledger).
  - Production environment configuration and `.env` variables.
  - **Gemini API Key Rotation & Quota Management**: Detailed runbook on updating API keys when Google AI Studio limits expire.
  - **Zero-Downtime Heuristic Fallback**: Explanation of how the platform gracefully degrades to offline graph heuristics without service interruption.
  - Health check probes (`GET /health`), Docker Compose operations, and backup/restore procedures.
