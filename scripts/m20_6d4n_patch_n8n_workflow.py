"""
M20.6D.4N — Normalize Revisions patch for n8n SQLite volume.

Inserts the `Normalize Revisions` Code node between `Get Revisions` and
`SET Latest Inbound` in the CRM workflow (id: DaFqDIzVi1f92Hvz).

Root cause: when GET /leads/{id}/revisions returns N items, n8n's HTTP
Request node fans out N execution items.  All downstream nodes then run N
times.  Nodes that reference $node["Webhook"].json fail on items 1..N-1
because the Webhook node only produced 1 item:

    ExpressionError: "Webhook" node has 1 item(s) but you're trying to
    access item 1

Fix: a Code node set to runOnceForAllItems collapses N items into exactly
1 item before the fanout can propagate downstream.

Usage:
    python3 scripts/m20_6d4n_patch_n8n_workflow.py /path/to/database.sqlite

The script is idempotent — running it a second time is a no-op if
Normalize Revisions already exists.

Validated: 4/4 cases (0 / 1 / 2 / 3+ revisions) — see forensic report
M20_6D4N_n8n_multi_revision_fanout_20260705.md.
"""

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

WORKFLOW_ID = "DaFqDIzVi1f92Hvz"

# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------

NORMALIZE_NODE = {
    "parameters": {
        "mode": "runOnceForAllItems",
        "jsCode": (
            "// Normalize Revisions — collapse N revision items into exactly 1 item.\n"
            "// Prevents paired-item ExpressionError when a lead has 2+ revisions.\n"
            "const allItems = $input.all();\n"
            "const revisions = allItems\n"
            "  .map(item => item.json)\n"
            "  .filter(r => r && typeof r.id !== 'undefined');\n"
            "\n"
            "// Sort newest-first by id (API already returns newest-first, but be explicit).\n"
            "revisions.sort((a, b) => (b.id || 0) - (a.id || 0));\n"
            "\n"
            "return [{\n"
            "  json: {\n"
            "    revisions: revisions,\n"
            "    latest_revision: revisions.length > 0 ? revisions[0] : null,\n"
            "    revision_count: revisions.length\n"
            "  }\n"
            "}];\n"
        ),
    },
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [-13700, 2400],
    "id": "1fe7b9f6-4240-4f03-8144-726192a7bb44",
    "name": "Normalize Revisions",
}

# ---------------------------------------------------------------------------
# Connection change
# ---------------------------------------------------------------------------
# Before:  Get Revisions -> SET Latest Inbound
# After:   Get Revisions -> Normalize Revisions -> SET Latest Inbound
#
# The direct Get Revisions -> SET Latest Inbound edge is removed.
# All other connections are unchanged.


def apply(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── 1. Load current latest version ──────────────────────────────────────
    history = conn.execute(
        """
        SELECT wh.versionId, wh.nodes, wh.connections
        FROM workflow_history wh
        WHERE wh.workflowId = ?
        ORDER BY wh.createdAt DESC
        LIMIT 1
        """,
        [WORKFLOW_ID],
    ).fetchone()

    if history is None:
        raise RuntimeError(f"No workflow_history rows found for workflow {WORKFLOW_ID}")

    prev_version_id = history["versionId"]
    nodes = json.loads(history["nodes"])
    conns = json.loads(history["connections"])

    # ── 2. Idempotency guard ─────────────────────────────────────────────────
    existing_names = {n["name"] for n in nodes}
    if "Normalize Revisions" in existing_names:
        print(f"[SKIP] Normalize Revisions already present (version {prev_version_id}). Nothing to do.")
        conn.close()
        return

    # ── 3. Verify preconditions ──────────────────────────────────────────────
    required_nodes = {"Get Revisions", "SET Latest Inbound"}
    missing = required_nodes - existing_names
    if missing:
        raise RuntimeError(f"Required nodes not found in workflow: {missing}")

    # Verify the direct edge we're about to remove exists
    gr_conn = conns.get("Get Revisions", {}).get("main", [[]])
    direct_edge_exists = any(
        e.get("node") == "SET Latest Inbound"
        for port in gr_conn
        for e in port
    )
    if not direct_edge_exists:
        raise RuntimeError(
            "Expected direct edge Get Revisions -> SET Latest Inbound not found. "
            "Workflow may already be in an unexpected state."
        )

    # ── 4. Apply changes ─────────────────────────────────────────────────────
    # 4a. Add new node
    nodes.append(NORMALIZE_NODE)

    # 4b. Remove direct Get Revisions -> SET Latest Inbound edge
    #     and add Get Revisions -> Normalize Revisions
    new_gr_port0 = [
        e for e in (gr_conn[0] if gr_conn else [])
        if e.get("node") != "SET Latest Inbound"
    ]
    new_gr_port0.append({"node": "Normalize Revisions", "type": "main", "index": 0})
    conns["Get Revisions"] = {"main": [new_gr_port0]}

    # 4c. Add Normalize Revisions -> SET Latest Inbound
    conns["Normalize Revisions"] = {
        "main": [[{"node": "SET Latest Inbound", "type": "main", "index": 0}]]
    }

    # ── 5. Write new workflow_history row ────────────────────────────────────
    new_version_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")

    conn.execute(
        """
        INSERT INTO workflow_history
            (versionId, workflowId, authors, createdAt, nodes, connections)
        SELECT ?, ?, authors, ?, ?, ?
        FROM workflow_history
        WHERE versionId = ?
        """,
        [
            new_version_id,
            WORKFLOW_ID,
            now_iso,
            json.dumps(nodes),
            json.dumps(conns),
            prev_version_id,
        ],
    )

    # ── 6. Update workflow_entity ────────────────────────────────────────────
    conn.execute(
        "UPDATE workflow_entity SET versionId = ?, updatedAt = ? WHERE id = ?",
        [new_version_id, now_iso, WORKFLOW_ID],
    )

    conn.commit()
    conn.close()

    print(f"[OK] Normalize Revisions node inserted.")
    print(f"     Previous version : {prev_version_id}")
    print(f"     New version       : {new_version_id}")
    print(f"     Node count        : {len(nodes)} (was {len(nodes) - 1})")
    print(f"     Edge removed      : Get Revisions -> SET Latest Inbound")
    print(f"     Edges added       : Get Revisions -> Normalize Revisions")
    print(f"                         Normalize Revisions -> SET Latest Inbound")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} /path/to/database.sqlite")
        sys.exit(1)
    apply(sys.argv[1])
