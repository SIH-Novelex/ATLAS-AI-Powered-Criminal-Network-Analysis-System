<div align="center">

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!-- 🚀 DYNAMIC ANIMATED CYBER-FORENSIC HUD BANNER                      -->
<!-- ═══════════════════════════════════════════════════════════════════ -->
<a href="#-system-architecture">
  <img src="docs/assets/hero_banner.svg" width="100%" alt="AI-Powered Criminal Network Analysis System Hero Banner" />
</a>

<br/>

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!-- 🎖️ LIVE MISSION TELEMETRY BADGES                                    -->
<!-- ═══════════════════════════════════════════════════════════════════ -->
<p align="center">
  <img src="https://img.shields.io/badge/System_Status-MISSION_READY_🟢-00F5D4?style=for-the-badge&logo=radar&logoColor=black" alt="Status" />
  <img src="https://img.shields.io/badge/Automated_Tests-211%20Passed%20(100%25)-00E676?style=for-the-badge&logo=pytest&logoColor=white" alt="Tests" />
  <img src="https://img.shields.io/badge/AI_Copilot-Gemini_2.5_Flash_Online-7928CA?style=for-the-badge&logo=google&logoColor=white" alt="Gemini AI" />
  <img src="https://img.shields.io/badge/Graph_Engine-Neo4j_5.20_LTS-008CC1?style=for-the-badge&logo=neo4j&logoColor=white" alt="Neo4j" />
  <img src="https://img.shields.io/badge/Blockchain-SHA--256_Custody-FF9900?style=for-the-badge&logo=blockchaindotcom&logoColor=white" alt="Blockchain" />
  <img src="https://img.shields.io/badge/Failover_Protection-Zero--Downtime_Active-00BBF9?style=for-the-badge&logo=shield&logoColor=white" alt="Failover" />
</p>

---

<p align="center">
  <b>An enterprise-grade forensic graph intelligence platform engineered for law enforcement, defense intelligence, and cyber-crime units.<br/>Harmonizes multi-jurisdictional FIRs, Call Detail Records (CDR), Banking Wires, and CCTV ANPR into an interactive Neo4j property graph, executes 10 autonomous pattern detectors, anchors evidence in a cryptographic blockchain, and features an integrated Gemini 2.5 Flash Graph Copilot with zero-downtime offline failover.</b>
</p>

</div>

---

## 📑 Repository Navigation

| 🎨 [Frontend](frontend/README.md) | ⚙️ [Backend](backend/README.md) | 📂 [Dataset](dataset/README.md) | 🧪 [Tests](tests/README.md) | 🔒 [Blockchain](data/README.md) | 📑 [Documentation](docs/README.md) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| Single Page App & Vis.js | FastAPI & Cypher Engine | Multi-Modal Case Payloads | 211 Passing Tests | SHA-256 Custody Ledger | Architecture Specifications |

---

## 🤖 Interactive Gemini 2.5 Flash Graph Copilot

The **Gemini AI Copilot** is docked directly inside the interactive graph visualization canvas (`#graphAiPanel`), enabling field officers and analysts without graph theory knowledge to query complex criminal topologies in plain natural language.

<div align="center">
  <img src="docs/assets/copilot_terminal.svg" width="100%" alt="Gemini Copilot Terminal Interface" />
</div>

<br/>

### ⚡ One-Click Autonomous Prompt Chips
- 🔍 **Summarize Key Suspects**: Isolates primary syndicate operatives, aliases, and charge sheets.
- 💸 **Find Laundering Rings**: Traces structured layering across nominee mule accounts.
- 🌐 **Explain Cross-Case Connections**: Identifies overlapping entities connecting disconnected FIRs.
- 👑 **Who is the Central Kingpin?**: Analyzes PageRank and bridge betweenness centrality.

---

## 🔑 Gemini API Key Configuration & Rotation Guide

The platform uses Google Gemini 2.5 Flash. You can obtain a free key and rotate it at any time with zero downtime.

### Step 1: Obtain a Free Key
1. Visit **[Google AI Studio](https://aistudio.google.com/app/apikey)**.
2. Sign in with any Google account.
3. Click **"Create API Key"** and copy your token.

### Step 2: Configure in `.env`
Edit or create your `.env` file in the project root:
```ini
# .env
GEMINI_API_KEY=AIzaSyYourGeneratedGeminiKeyHere
GEMINI_MODEL=gemini-2.5-flash
```

### Step 3: Restart Backend
```powershell
uvicorn backend.main:app --reload
```

---

### 🛡️ Zero-Downtime Heuristic Failover Guarantee
> [!IMPORTANT]
> **What happens if your free Gemini credit expires or Google returns HTTP 429 Quota Exceeded?**
>
> The system includes a **hardened, autonomous fail-soft architecture** (`backend/services/gemini_service.py`):
> 1. If the API key is missing, expired, or rate-limited, the system **never crashes, never fails, and displays zero error alerts**.
> 2. It immediately shifts to an internal **Deterministic Graph Heuristics Engine**:
>    - Dynamically evaluates node degree centrality, PageRank, and betweenness scores.
>    - Scans active pattern detections (Hawala, SIM swaps, co-locations).
>    - Generates a structured, evidence-backed investigative briefing directly in the chat panel.
> 3. Once a new valid key is provided in `.env`, the system automatically resumes utilizing Gemini 2.5 Flash!

---

## 🕵️ The 10 Scoped Cypher Pattern Detectors

Continuous, case-scoped graph algorithms engineered to identify sophisticated criminal tradecraft:

<div align="center">
  <img src="docs/assets/detectors_grid.svg" width="100%" alt="10 Scoped Cypher Detectors HUD" />
</div>

<br/>

| # | Detector Name | Threat Level | Algorithmic Mechanism |
| :-: | :--- | :-: | :--- |
| **1** | **Frequent Caller Spikes** | <img src="https://img.shields.io/badge/HIGH-7928CA?style=flat-square" /> | Detects communication volume outliers (>30 calls) between unassociated nodes in short intervals. |
| **2** | **Burner SIM / IMEI Multi-Swap** | <img src="https://img.shields.io/badge/CRITICAL-FF0055?style=flat-square" /> | Traces multiple phone numbers registered to or transmitting through identical physical IMEI hardware. |
| **3** | **Hawala & Laundering Rings** | <img src="https://img.shields.io/badge/CRITICAL-FF0055?style=flat-square" /> | Detects rapid-succession funds transfers traversing 3+ intermediary accounts to obscure origin. |
| **4** | **Mule Account Syndicates** | <img src="https://img.shields.io/badge/HIGH-7928CA?style=flat-square" /> | Flags historically dormant accounts suddenly receiving high-velocity, high-sum deposits. |
| **5** | **Cross-Case Suspect Overlap** | <img src="https://img.shields.io/badge/CRITICAL-FF0055?style=flat-square" /> | Pinpoints identical person nodes, vehicles, or bank accounts present across separate FIRs. |
| **6** | **Vehicle Convoy Tracking** | <img src="https://img.shields.io/badge/MEDIUM-F77F00?style=flat-square" /> | Identifies pairs or groups of vehicles logged at identical CCTV ANPR cameras within 5-minute margins. |
| **7** | **Crime Scene Co-Location** | <img src="https://img.shields.io/badge/HIGH-7928CA?style=flat-square" /> | Matches cell tower sector connections of multiple suspects within the temporal window of an FIR. |
| **8** | **Meeting & Association Clusters** | <img src="https://img.shields.io/badge/MEDIUM-F77F00?style=flat-square" /> | Calculates network clique density to discover co-accused meeting clusters. |
| **9** | **Shell Company / Dummy Address Rings** | <img src="https://img.shields.io/badge/MEDIUM-F77F00?style=flat-square" /> | Detects multiple commercial legal entities registered to identical physical postal addresses. |
| **10** | **Centrality Kingpin Leaderboard** | <img src="https://img.shields.io/badge/ANALYTIC-00BBF9?style=flat-square" /> | Executes PageRank & Betweenness Centrality to isolate network commanders vs. logistics mules. |

---

## 🔒 Cryptographic Chain of Custody (Blockchain Ledger)

In compliance with forensic digital evidence admissibility standards (e.g., Section 65B of the Indian Evidence Act / BSA provisions), every piece of ingested data is permanently anchored in a local cryptographic blockchain.

<div align="center">
  <img src="docs/assets/blockchain_pipeline.svg" width="100%" alt="Blockchain Pipeline Diagram" />
</div>

<br/>

- **Deterministic Merkle Trees**: All nodes and edges are normalized and hashed with SHA-256.
- **Unbroken Cryptographic Hash Chaining**: Every block contains the `previous_hash` of its predecessor.
- **Tamper Verification API**: Call `GET /api/blockchain/verify` to validate ledger integrity anytime.

---

## 🏛️ System Architecture Pipeline

```mermaid
flowchart TD
    classDef ingestion fill:#00F5D4,stroke:#00A896,stroke-width:2px,color:#000;
    classDef core fill:#1E293B,stroke:#3B82F6,stroke-width:2px,color:#fff;
    classDef ai fill:#7928CA,stroke:#FF0080,stroke-width:2px,color:#fff;
    classDef visual fill:#0F172A,stroke:#10B981,stroke-width:2px,color:#fff;

    subgraph INGEST ["📥 Multi-Source Evidence Ingestion"]
        A1[📁 Case Payloads: FIR, CDR, CCTV, Bank]:::ingestion
        A2[⚡ Live Streaming Events: /api/events]:::ingestion
    end

    subgraph BACKEND ["⚙️ Core Intelligence Backend (FastAPI + Neo4j)"]
        B[Schema Mapper & Deduplication]:::core
        C[(Neo4j Property Graph)]:::core
        D[Cryptographic SHA-256 Merkle Service]:::core
        E[Centrality Engine: PageRank & Betweenness]:::core
        F[10 Scoped Suspicious Pattern Detectors]:::core
        G[Ambiguity-Safe Pathfinding Service]:::core
    end

    subgraph COPILOT ["🤖 Intelligent Reasoning Layer"]
        H[Google Gemini 2.5 Flash Copilot]:::ai
        I[Fail-Soft Graph Heuristic Engine]:::ai
    end

    subgraph UI ["🎨 Analyst Workspace (Single-Page App)"]
        J[Vis.js ForceAtlas2 Interactive Canvas]:::visual
        K[Docked Copilot Chat & Quick Chips]:::visual
        L[Suspicious Pattern Alerts & Threat Badges]:::visual
        M[Blockchain Evidence Ledger Inspector]:::visual
    end

    A1 --> B
    A2 --> B
    B --> C
    B --> D
    C --> E
    C --> F
    C --> G
    C --> H
    E --> H
    F --> H
    H -.->|Quota / Offline Fallback| I
    C --> J
    H --> K
    I --> K
    F --> L
    D --> M
```

---

## 🚀 Quickstart & Installation

### Option 1: One-Click Docker Setup (Recommended)
Spins up Neo4j 5.x and the FastAPI application with all APOC graph procedures pre-installed:

```powershell
# 1. Clone the repository
git clone https://github.com/Thxrun-07/AI-Powered-Criminal-Network-Analysis-System.git
cd AI-Powered-Criminal-Network-Analysis-System

# 2. Configure environment
copy .env.example .env

# 3. Start containers
docker-compose up -d
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser!

---

### Option 2: Native Local Setup

```powershell
# 1. Clone & enter repository
git clone https://github.com/Thxrun-07/AI-Powered-Criminal-Network-Analysis-System.git
cd AI-Powered-Criminal-Network-Analysis-System

# 2. Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env

# 5. Launch application
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Comprehensive Verification Suite

Run all 211 unit and integration tests completely offline without requiring an external database:

```powershell
pytest tests/unit tests/integration
```

```text
============================= test session starts =============================
platform win32 -- Python 3.14.0, pytest-9.1.1
rootdir: C:\Users\shinc\projects\AI-Powered-Criminal-Network-Analysis-System
collected 211 items

tests\unit\test_ai_insights.py .....                                     [  2%]
tests\unit\test_batched_relationship_writers.py .....................    [ 12%]
tests\unit\test_blockchain_ledger.py ....                                [ 14%]
tests\unit\test_case_delete.py ......                                    [ 17%]
tests\unit\test_delta_processor.py ................                      [ 24%]
tests\unit\test_entity_search.py .....                                   [ 27%]
tests\unit\test_event_model.py ...............                           [ 34%]
tests\unit\test_insights_10_types.py ..........                          [ 38%]
tests\unit\test_logging_masking.py ...                                   [ 40%]
tests\unit\test_rankings.py ....                                         [ 42%]
tests\unit\test_scoped_detectors.py .................................... [ 59%]
......................                                                   [ 69%]
tests\unit\test_shortest_path.py ......                                  [ 72%]
tests\integration\test_events_api.py .........................           [ 84%]
tests\integration\test_health_and_reset.py ....                          [ 86%]
tests\integration\test_ingest_merge.py ....                              [ 88%]
tests\integration\test_ingest_ordering.py ......                         [ 90%]
tests\integration\test_ingest_replace.py ..                              [ 91%]
tests\integration\test_legacy_ingest_casedata.py ................        [ 99%]
tests\integration\test_unified_ingest.py .                               [100%]

====================== 211 passed in 3.01s =======================
```

---

## 📂 Repository File Structure

```
AI-Powered-Criminal-Network-Analysis-System/
├── README.md                           # ⭐ Main Showcase & System Overview
├── .env.example                        # Safe environment template
├── .gitignore                          # Strict security exclusion for secrets & caches
├── docker-compose.yml                  # Neo4j + Backend container orchestration
├── Dockerfile                          # Multi-stage production container build
├── pytest.ini                          # Pytest configuration
├── requirements.txt                    # Pinned production dependencies
│
├── frontend/                           # 🎨 Modern Single-Page Analyst Application
│   ├── index.html                      # Interactive Vis.js network canvas & Copilot UI
│   └── README.md                       # 👉 Detailed Frontend Guide
│
├── backend/                            # ⚙️ FastAPI Graph Intelligence Engine
│   ├── main.py                         # Application entrypoint & static routes
│   ├── database.py                     # Neo4j driver connection pool
│   ├── config.py                       # Settings & environment validation
│   ├── logging_config.py               # Structured logging with PII masking
│   ├── models/                         # Pydantic v2 schemas (Entities, Events, Insights)
│   ├── routers/                        # REST API endpoint controllers
│   ├── services/                       # Graph writers, Detectors, Blockchain & AI
│   └── README.md                       # 👉 Detailed Backend Architecture Guide
│
├── dataset/                            # 📂 Forensic Case Evidence Payloads
│   ├── case_001_homicide.json          # Multi-source homicide case (FIR, CDR, CCTV)
│   ├── case_002_fraud.json             # Corporate fraud with cross-case overlap
│   ├── envelope_sample.json            # Real-time streaming event envelope
│   └── README.md                       # 👉 Forensic Dataset & Ingestion Guide
│
├── tests/                              # 🧪 Automated Test Suite (211+ Passing Tests)
│   ├── unit/                           # Isolated unit tests
│   ├── integration/                    # API & pipeline integration tests
│   ├── live/                           # Live Neo4j equivalence tests
│   ├── conftest.py                     # Mock fixtures & blockchain test isolation
│   └── README.md                       # 👉 Testing Suite Guide & Commands
│
├── data/                               # 🔒 Cryptographic Blockchain Ledger
│   ├── blockchain_ledger.json          # Verifiable chain-of-custody blocks
│   └── README.md                       # 👉 Blockchain & Tamper-Proofing Guide
│
└── docs/                               # 📑 Technical Specifications & Runbooks
    ├── assets/                         # 🎨 High-Res Vector SVG HUD Visuals
    │   ├── hero_banner.svg             # Animated Radar & Packet-Flow Hero HUD
    │   ├── copilot_terminal.svg        # Glassmorphism Copilot Interaction Window
    │   ├── detectors_grid.svg          # 10 Cypher Pattern Detectors Dashboard
    │   └── blockchain_pipeline.svg     # Cryptographic Merkle Chain Flow
    ├── EVENTS_API.md                   # Real-time event streaming specification
    ├── SUMMARY.md                      # Comprehensive system architecture & entity model
    ├── SYSTEM_ARCHITECTURE_AND_OPERATIONS_GUIDE.md # Production runbook & failover guide
    └── README.md                       # 👉 Documentation Index & Roadmap
```

---

<div align="center">

<p align="center">
  <b>Built for the Smart India Hackathon (SIH) &bull; National Law Enforcement Innovation</b><br/>
  <i>Empowering investigators with Graph AI, Cryptographic Integrity, and Autonomous Reasoning.</i>
</p>

</div>
