# 🔒 Blockchain Evidence Ledger

The `data/` directory contains the immutable, cryptographic evidence ledger used to maintain a verifiable **Chain of Custody** for forensic data ingested into the system.

---

## 📁 Directory Contents

```
data/
├── blockchain_ledger.json    # Cryptographic JSON chain-of-custody blocks
└── README.md                 # This guide
```

---

## 🛡️ Architecture & Security Model

In legal proceedings and criminal trials, digital evidence must satisfy strict non-repudiation and chain-of-custody standards (e.g., Section 65B of the Indian Evidence Act / BSA guidelines).

This system implements a local cryptographic blockchain to guarantee that once a case payload (FIR, CDR, Bank transactions) is ingested into the graph database, it cannot be modified or deleted without invalidating the chain.

### 1. Block Structure

```json
{
  "index": 1,
  "timestamp": "2026-09-04T12:00:00Z",
  "case_id": "CASE-2024-001",
  "previous_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "merkle_root": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "data_summary": {
    "entity_count": 42,
    "relationship_count": 87
  },
  "block_hash": "a1b2c3d4e5f6..."
}
```

### 2. Merkle Root Computation
- Every entity (Person, Vehicle, Account) and every relationship (CALL, TRANSFER) in a case is normalized and hashed using **SHA-256**.
- A deterministic Merkle Tree is constructed from these hashes, and the resulting `merkle_root` is stored in the block header.
- Any modification to even a single phone number or transaction amount immediately changes the Merkle root and invalidates the block.

### 3. Cryptographic Chaining
- Each block contains the `previous_hash` of the preceding block.
- Modifying a past block breaks the cryptographic chain for all subsequent blocks.

---

## 🔍 How to Verify Ledger Integrity

### Via REST API
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/blockchain/verify" -Method Get
```

**Expected Response**:
```json
{
  "status": "VALID",
  "total_blocks": 3,
  "verified_at": "2026-09-04T23:00:00Z",
  "message": "Cryptographic chain of custody is intact. No tampering detected."
}
```

### Via Analyst Dashboard UI
Click the **"Blockchain"** tab in the top navigation bar to inspect each block's Merkle root, timestamp, and verification badge.
