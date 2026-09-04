# 🎨 Frontend Web Application (Analyst Workspace)

The `frontend/` directory contains the modern, responsive Single Page Application (SPA) designed for forensic investigators, intelligence analysts, and law enforcement officers. It provides intuitive, real-time graph visualization, suspicious pattern exploration, chain-of-custody verification, and an interactive **Gemini AI Copilot**.

---

## 📂 Directory Contents

```
frontend/
├── index.html       # Complete Single-Page Application (HTML5, CSS3, Vanilla JS, Vis.js)
└── README.md        # This guide
```

---

## 🌟 Key Capabilities & Components

### 1. Interactive ForceAtlas2 Graph Explorer
- **Physics-Driven Topology**: Powered by [Vis.js Network](https://visjs.github.io/vis-network/docs/network/), utilizing the ForceAtlas2 physics solver for anti-clustering and clear spatial layout of complex crime networks.
- **Dynamic Node & Edge Styling**:
  - **Suspects / Persons**: Red circular nodes with threat badges.
  - **Vehicles**: Orange nodes with registration identifiers.
  - **Bank Accounts**: Green nodes with financial transaction volumes.
  - **Locations / Cell Towers**: Blue nodes with geocoordinates and antenna sectors.
  - **Cases / FIRs**: Purple central hub nodes connecting multi-jurisdictional incidents.
  - **Edges**: Color-coded directional relations (`TRANSFERRED_FUNDS`, `CALLED`, `ASSOCIATE_OF`, `LOCATED_AT`, `REGISTERED_TO`).
- **Interactive Multi-Hop Traversal**: Click any node to inspect metadata, double-click to expand immediate neighbors, or use the context menu to trace shortest paths.

### 2. Docked Gemini AI Copilot Panel
- **Conversational Intelligence**: A docked AI drawer (`#graphAiPanel`) designed specifically for analysts who need instant natural language explanations of complex graph topologies.
- **Quick-Prompt Chips**:
  - 🔍 *Summarize Key Suspects*
  - 💸 *Find Financial Laundering Rings*
  - 🌐 *Explain Cross-Case Connections*
  - 👑 *Who is the Central Kingpin?*
- **Offline & Rate-Limit Resilience**: Fully integrated with the backend's `POST /api/graph/ai-query`. If the Gemini API key is missing or quota/credits are exceeded, the UI seamlessly receives rich topological graph heuristic analysis without any downtime or error alerts.

### 3. Automated Pattern & Centrality Panels
- **10 Pattern Insight Detectors**: Real-time sidebar listing flagged patterns (Hawala rings, mule accounts, SIM burner swaps, shared IMEI/location clusters) with threat severity badges (HIGH, MEDIUM, LOW).
- **Centrality Rankings Table**: Displays PageRank, Betweenness Centrality, and In/Out Degree metrics to pinpoint criminal network kingpins and logistics coordinators.
- **Ambiguity-Safe Pathfinding**: Calculate the shortest path between any two entities. If an ambiguous name is entered, the UI renders interactive candidate resolution cards.

### 4. Forensic Evidence & Blockchain Inspector
- **Chain-of-Custody Modal**: View the immutable cryptographic blockchain ledger.
- **Integrity Status**: Live SHA-256 Merkle root verification indicator confirming that ingested evidence has not been tampered with.

---

## 🚀 How It Works & API Integration

The frontend communicates with the FastAPI backend over REST:

| Endpoint | Purpose in Frontend |
| :--- | :--- |
| `GET /` | Serves this `frontend/index.html` dashboard directly |
| `POST /api/graph/ai-query` | Dispatches natural language queries to Gemini Copilot |
| `GET /api/graph/data` | Fetches filtered nodes and edges for Vis.js canvas |
| `POST /api/cases/ingest` | Uploads forensic JSON case payloads |
| `POST /api/events` | Streams real-time operational delta events |
| `GET /api/rankings` | Pulls PageRank & betweenness centrality metrics |
| `GET /api/insights` | Fetches 10 suspicious pattern detector results |
| `GET /api/path/shortest` | Calculates path between two entities with ambiguity checks |
| `GET /api/blockchain/verify` | Verifies cryptographic integrity of evidence ledger |

---

## 🛠️ Tech Stack
- **HTML5 & CSS3**: Pure modern CSS variables, dark forensic theme, responsive grid/flexbox.
- **Vanilla JavaScript (ES6+)**: Zero external bundle dependencies for instant loading and zero build-step overhead.
- **Vis.js Network**: Canvas-rendered dynamic graph simulation.
- **Chart.js**: Temporal activity and transaction volume histograms.
