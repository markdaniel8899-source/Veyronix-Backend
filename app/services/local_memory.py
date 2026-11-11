"""
Local SQLite Memory Store — Zep Cloud replacement.

Optimized for Oracle Cloud Always Free VM (1 GB RAM).
Uses Python's built-in sqlite3 with WAL mode for concurrent read performance.
JSON1 is used for graph filtering and FTS5 powers full-text agent search.
All queries use parameterized statements to prevent SQL injection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Veyronix.local_memory")


def _resolve_db_path(db_path: str) -> str:
    """Resolve a relative database path and make sure its directory exists."""

    expanded = os.path.expanduser(db_path or "./uploads/veyronix.db")
    absolute = os.path.abspath(expanded)
    directory = os.path.dirname(absolute)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return absolute


class LocalMemory:
    """
    Local SQLite-based memory store for agents, interactions, and summaries.

    Tables:
        agents           - id TEXT PRIMARY KEY, persona TEXT, state TEXT
        agents_fts       - FTS5 index over agent id + persona
        interactions     - id INTEGER PRIMARY KEY AUTOINCREMENT,
                           round_num INT, agent_id TEXT, action TEXT,
                           content TEXT, timestamp DATETIME
        memory_summaries - round_block INT PRIMARY KEY, summary_text TEXT
        market_shocks    - injected mid-simulation events
    """

    def __init__(self, db_path: str = "./uploads/veyronix.db"):
        self.db_path = _resolve_db_path(db_path)
        self._init_db()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Create a new SQLite connection with WAL mode and NORMAL sync."""
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they do not already exist."""
        conn = self._connect()
        try:
            # Migrate databases created before the thread columns existed.
            # This has to run before the schema script, because the index on
            # parent_post_id needs the column to exist.
            for ddl in (
                "ALTER TABLE interactions ADD COLUMN parent_post_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE interactions ADD COLUMN interaction_type TEXT NOT NULL DEFAULT 'CREATE_POST'",
                "ALTER TABLE interactions ADD COLUMN post_id TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    # The table is new or the column already exists.
                    pass

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    persona TEXT NOT NULL,
                    state TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_num INT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    content TEXT NOT NULL,
                    parent_post_id TEXT NOT NULL DEFAULT '',
                    interaction_type TEXT NOT NULL DEFAULT 'CREATE_POST',
                    post_id TEXT NOT NULL DEFAULT '',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS memory_summaries (
                    round_block INT PRIMARY KEY,
                    summary_text TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_shocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    simulation_id TEXT NOT NULL,
                    round_num INT,
                    event TEXT NOT NULL,
                    consumed INT NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS graph_edges (
                    id TEXT PRIMARY KEY,
                    graph_id TEXT NOT NULL,
                    source_node_uuid TEXT NOT NULL,
                    target_node_uuid TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT 'RELATED_TO',
                    fact TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_interactions_round
                    ON interactions(round_num);
                CREATE INDEX IF NOT EXISTS idx_interactions_parent
                    ON interactions(parent_post_id);
                CREATE INDEX IF NOT EXISTS idx_interactions_agent
                    ON interactions(agent_id);
                CREATE INDEX IF NOT EXISTS idx_market_shocks_simulation
                    ON market_shocks(simulation_id, consumed);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_graph
                    ON graph_edges(graph_id);
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS agents_fts
                    USING fts5(agent_id, persona)
                    """
                )
                self._fts_available = True
            except sqlite3.OperationalError:
                # Some slim SQLite builds omit FTS5. LIKE fallback keeps the
                # service functional on those systems.
                self._fts_available = False
                logger.warning("SQLite FTS5 unavailable; using LIKE search")
            conn.commit()
            logger.info("LocalMemory database initialized at %s", self.db_path)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_persona(persona: Any) -> str:
        if isinstance(persona, str):
            return persona
        return json.dumps(persona, ensure_ascii=False)

    @staticmethod
    def _deserialize_row(row: sqlite3.Row) -> Dict[str, Any]:
        raw_persona = row["persona"]
        try:
            persona = json.loads(raw_persona)
        except (TypeError, ValueError):
            persona = {"content": raw_persona}
        if not isinstance(persona, dict):
            persona = {"content": persona}
        return {
            "id": row["id"],
            "persona": persona,
            "state": row["state"],
        }

    def save_agent(self, agent_id: str, persona: Any, state: str = "created") -> None:
        """Insert or replace an agent record. ``persona`` may be dict or JSON."""
        persona_json = self._serialize_persona(persona)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO agents (id, persona, state)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    persona = excluded.persona,
                    state = excluded.state
                """,
                (agent_id, persona_json, state),
            )
            if self._fts_available:
                conn.execute("DELETE FROM agents_fts WHERE agent_id = ?", (agent_id,))
                conn.execute(
                    "INSERT INTO agents_fts (agent_id, persona) VALUES (?, ?)",
                    (agent_id, persona_json),
                )
            conn.commit()
        finally:
            conn.close()

    def save_agents(self, agents: List[Dict[str, Any]]) -> None:
        """Bulk insert/update agents in a single transaction."""

        conn = self._connect()
        try:
            for agent in agents:
                agent_id = str(agent["id"])
                persona_json = self._serialize_persona(agent.get("persona", {}))
                state = str(agent.get("state", "created"))
                conn.execute(
                    """
                    INSERT INTO agents (id, persona, state)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        persona = excluded.persona,
                        state = excluded.state
                    """,
                    (agent_id, persona_json, state),
                )
                if self._fts_available:
                    conn.execute("DELETE FROM agents_fts WHERE agent_id = ?", (agent_id,))
                    conn.execute(
                        "INSERT INTO agents_fts (agent_id, persona) VALUES (?, ?)",
                        (agent_id, persona_json),
                    )
            conn.commit()
        finally:
            conn.close()

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single agent by ID. Returns None if not found."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, persona, state FROM agents WHERE id = ?",
                (agent_id,),
            )
            row = cur.fetchone()
            return self._deserialize_row(row) if row else None
        finally:
            conn.close()

    def list_agents(self, graph_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return agents, optionally restricted to one graph."""

        conn = self._connect()
        try:
            if graph_id:
                try:
                    cur = conn.execute(
                        "SELECT id, persona, state FROM agents "
                        "WHERE json_extract(persona, '$.graph_id') = ? "
                        "ORDER BY id",
                        (graph_id,),
                    )
                    return [self._deserialize_row(row) for row in cur.fetchall()]
                except sqlite3.OperationalError:
                    cur = conn.execute(
                        "SELECT id, persona, state FROM agents ORDER BY id"
                    )
                    return [
                        record
                        for record in (self._deserialize_row(row) for row in cur.fetchall())
                        if record["persona"].get("graph_id") == graph_id
                    ]
            cur = conn.execute("SELECT id, persona, state FROM agents ORDER BY id")
            return [self._deserialize_row(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def count_agents(self, graph_id: Optional[str] = None) -> int:
        conn = self._connect()
        try:
            if graph_id:
                try:
                    cur = conn.execute(
                        "SELECT COUNT(*) AS n FROM agents "
                        "WHERE json_extract(persona, '$.graph_id') = ?",
                        (graph_id,),
                    )
                    return int(cur.fetchone()["n"])
                except sqlite3.OperationalError:
                    return len(self.list_agents(graph_id))
            cur = conn.execute("SELECT COUNT(*) AS n FROM agents")
            return int(cur.fetchone()["n"])
        finally:
            conn.close()

    def delete_agents_by_graph(self, graph_id: str) -> int:
        conn = self._connect()
        try:
            try:
                cur = conn.execute(
                    "DELETE FROM agents WHERE json_extract(persona, '$.graph_id') = ?",
                    (graph_id,),
                )
            except sqlite3.OperationalError:
                cur = conn.execute(
                    "DELETE FROM agents WHERE persona LIKE ?",
                    (f'%"{graph_id}"%',),
                )
            if self._fts_available:
                conn.execute("DELETE FROM agents_fts")
                for row in conn.execute("SELECT id, persona FROM agents"):
                    conn.execute(
                        "INSERT INTO agents_fts (agent_id, persona) VALUES (?, ?)",
                        (row["id"], row["persona"]),
                    )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Graph edge CRUD
    # ------------------------------------------------------------------

    def save_edges(self, edges: List[Dict[str, Any]]) -> None:
        """Bulk insert/update graph edges in one transaction."""

        if not edges:
            return
        conn = self._connect()
        try:
            for edge in edges:
                edge_id = str(edge.get("uuid") or edge.get("id") or "")
                if not edge_id:
                    continue
                metadata = edge.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                conn.execute(
                    """
                    INSERT INTO graph_edges (
                        id, graph_id, source_node_uuid, target_node_uuid,
                        name, fact, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        graph_id = excluded.graph_id,
                        source_node_uuid = excluded.source_node_uuid,
                        target_node_uuid = excluded.target_node_uuid,
                        name = excluded.name,
                        fact = excluded.fact,
                        metadata = excluded.metadata
                    """,
                    (
                        edge_id,
                        str(edge.get("graph_id") or ""),
                        str(edge.get("source_node_uuid") or ""),
                        str(edge.get("target_node_uuid") or ""),
                        str(edge.get("name") or edge.get("relation") or "RELATED_TO"),
                        str(edge.get("fact") or ""),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def list_edges(self, graph_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            if graph_id:
                cur = conn.execute(
                    "SELECT * FROM graph_edges WHERE graph_id = ? ORDER BY id",
                    (graph_id,),
                )
            else:
                cur = conn.execute("SELECT * FROM graph_edges ORDER BY id")
            results = []
            for row in cur.fetchall():
                try:
                    metadata = json.loads(row["metadata"] or "{}")
                except (TypeError, ValueError):
                    metadata = {}
                results.append(
                    {
                        "uuid": row["id"],
                        "id": row["id"],
                        "graph_id": row["graph_id"],
                        "source_node_uuid": row["source_node_uuid"],
                        "target_node_uuid": row["target_node_uuid"],
                        "name": row["name"],
                        "fact": row["fact"],
                        "metadata": metadata,
                        "created_at": row["created_at"],
                    }
                )
            return results
        finally:
            conn.close()

    def count_edges(self, graph_id: Optional[str] = None) -> int:
        conn = self._connect()
        try:
            if graph_id:
                cur = conn.execute(
                    "SELECT COUNT(*) AS n FROM graph_edges WHERE graph_id = ?",
                    (graph_id,),
                )
            else:
                cur = conn.execute("SELECT COUNT(*) AS n FROM graph_edges")
            return int(cur.fetchone()["n"])
        finally:
            conn.close()

    def delete_edges_by_graph(self, graph_id: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM graph_edges WHERE graph_id = ?", (graph_id,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def search_agents(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Full-text search over agent personas (FTS5 with LIKE fallback)."""

        if not query:
            return []
        conn = self._connect()
        try:
            if self._fts_available:
                # Quote the query so user input cannot break FTS5 syntax.
                safe_query = '"' + query.replace('"', '""') + '"'
                try:
                    cur = conn.execute(
                        """
                        SELECT a.id, a.persona, a.state
                        FROM agents_fts f
                        JOIN agents a ON a.id = f.agent_id
                        WHERE agents_fts MATCH ?
                        LIMIT ?
                        """,
                        (safe_query, limit),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return [self._deserialize_row(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            like = f"%{query}%"
            cur = conn.execute(
                "SELECT id, persona, state FROM agents "
                "WHERE persona LIKE ? LIMIT ?",
                (like, limit),
            )
            return [self._deserialize_row(row) for row in cur.fetchall()]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Interaction CRUD
    # ------------------------------------------------------------------

    def add_interaction(
        self,
        round_num: int,
        agent_id: str,
        action: str,
        content: str,
        parent_post_id: str = "",
        interaction_type: str = "CREATE_POST",
        post_id: str = "",
    ) -> int:
        """Insert a new interaction record and return its row ID.

        ``parent_post_id`` links a post to the one it answers, which is what
        builds the conversation threads.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO interactions (
                    round_num, agent_id, action, content,
                    parent_post_id, interaction_type, post_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_num,
                    agent_id,
                    action,
                    content,
                    parent_post_id or "",
                    interaction_type or "CREATE_POST",
                    post_id or "",
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_recent_interactions(
        self,
        round_num: Optional[int] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch recent interactions with optional filters."""
        conn = self._connect()
        try:
            query = (
                "SELECT id, round_num, agent_id, action, content, "
                "parent_post_id, interaction_type, post_id, timestamp "
                "FROM interactions"
            )
            params: List[Any] = []
            conditions: List[str] = []

            if round_num is not None:
                conditions.append("round_num = ?")
                params.append(round_num)
            if agent_id is not None:
                conditions.append("agent_id = ?")
                params.append(agent_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY id DESC LIMIT ?"
            params.append(int(limit))

            cur = conn.execute(query, params)
            return [
                {
                    "id": row["id"],
                    "round_num": row["round_num"],
                    "agent_id": row["agent_id"],
                    "action": row["action"],
                    "content": row["content"],
                    "parent_post_id": row["parent_post_id"],
                    "interaction_type": row["interaction_type"],
                    "post_id": row["post_id"],
                    "timestamp": row["timestamp"],
                }
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def search_interactions(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """LIKE search over interaction content, newest first."""

        if not query:
            return []
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT id, round_num, agent_id, action, content, timestamp
                FROM interactions
                WHERE content LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (f"%{query}%", int(limit)),
            )
            return [
                {
                    "id": row["id"],
                    "round_num": row["round_num"],
                    "agent_id": row["agent_id"],
                    "action": row["action"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def get_interactions_for_block(
        self, start_round: int, end_round: int
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT id, round_num, agent_id, action, content, timestamp
                FROM interactions
                WHERE round_num BETWEEN ? AND ?
                ORDER BY id
                """,
                (start_round, end_round),
            )
            return [
                {
                    "id": row["id"],
                    "round_num": row["round_num"],
                    "agent_id": row["agent_id"],
                    "action": row["action"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def delete_interactions_for_round(self, round_num: int) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM interactions WHERE round_num = ?",
                (round_num,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def delete_interactions_for_block(self, start_round: int, end_round: int) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM interactions WHERE round_num BETWEEN ? AND ?",
                (start_round, end_round),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Memory Summaries
    # ------------------------------------------------------------------

    def save_summary(self, round_block: int, summary_text: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO memory_summaries (round_block, summary_text)
                VALUES (?, ?)
                ON CONFLICT(round_block) DO UPDATE SET
                    summary_text = excluded.summary_text
                """,
                (round_block, summary_text),
            )
            conn.commit()
        finally:
            conn.close()

    def get_summary(self, round_block: int) -> Optional[str]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT summary_text FROM memory_summaries WHERE round_block = ?",
                (round_block,),
            )
            row = cur.fetchone()
            return row["summary_text"] if row else None
        finally:
            conn.close()

    def list_summaries(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT round_block, summary_text FROM memory_summaries "
                "ORDER BY round_block"
            )
            return [
                {"round_block": row["round_block"], "summary_text": row["summary_text"]}
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Market shocks
    # ------------------------------------------------------------------

    def add_market_shock(
        self,
        simulation_id: str,
        event: str,
        round_num: Optional[int] = None,
    ) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO market_shocks (simulation_id, round_num, event)
                VALUES (?, ?, ?)
                """,
                (simulation_id, round_num, event),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_pending_market_shocks(
        self, simulation_id: str, round_num: int
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT id, event, round_num FROM market_shocks
                WHERE simulation_id = ? AND consumed = 0
                  AND (round_num IS NULL OR round_num <= ?)
                ORDER BY id
                """,
                (simulation_id, round_num),
            )
            return [
                {"id": row["id"], "event": row["event"], "round_num": row["round_num"]}
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def consume_market_shock(self, shock_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE market_shocks SET consumed = 1 WHERE id = ?",
                (shock_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def list_market_shocks(self, simulation_id: str) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT id, round_num, event, consumed, created_at
                FROM market_shocks WHERE simulation_id = ? ORDER BY id
                """,
                (simulation_id,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Memory Consolidation (Async)
    # ------------------------------------------------------------------

    async def consolidate_memory(
        self,
        round_number: int,
        llm_client: Any,
        interval: int = 5,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """
        Consolidate raw interactions into a 2-sentence LLM summary.

        Triggered every ``interval`` rounds. Raw interactions for the block are
        replaced by the summary so storage stays bounded on small VMs.
        """
        if interval <= 0 or round_number % interval != 0:
            return None

        block_start = round_number - interval + 1
        block_end = round_number
        block_id = round_number // interval

        existing = await asyncio.to_thread(self.get_summary, block_id)
        if existing is not None:
            return existing

        interactions = await asyncio.to_thread(
            self.get_interactions_for_block, block_start, block_end
        )
        if not interactions:
            return None

        context_lines = [
            (
                f"Round {it['round_num']} | Agent {it['agent_id']} | "
                f"{it['action']}: {it['content']}"
            )
            for it in interactions
        ]
        context_text = "\n".join(context_lines[:200])

        prompt = (
            "You are a memory consolidation assistant. Summarize the following "
            "simulation events into exactly 2 sentences. Focus on key outcomes, "
            "agent decisions, and state changes. Be concise.\n\n"
            f"Events:\n{context_text}\n\n"
            "Two-sentence summary:"
        )

        from ..config import Config

        resolved_model = model or Config.LLM_ROUTER_SMALL
        messages = [{"role": "user", "content": prompt}]

        try:
            if llm_client is None:
                raise ValueError("llm_client is required")
            if hasattr(llm_client, "acreate"):
                response = await llm_client.acreate(
                    model=resolved_model,
                    messages=messages,
                )
            elif hasattr(llm_client, "create"):
                response = await asyncio.to_thread(
                    llm_client.create,
                    model=resolved_model,
                    messages=messages,
                )
            elif hasattr(llm_client, "chat"):
                text = await asyncio.to_thread(
                    llm_client.chat,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=256,
                )
                summary_text = text.strip()
                response = None
            else:
                response = await llm_client(
                    model=resolved_model,
                    messages=messages,
                )

            if response is not None:
                summary_text = response.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("Memory consolidation LLM call failed: %s", exc)
            summary_text = (
                f"Rounds {block_start}-{block_end}: "
                f"{len(interactions)} interaction(s) recorded."
            )

        await asyncio.to_thread(
            self._save_and_cleanup,
            block_id,
            summary_text,
            block_start,
            block_end,
        )
        logger.info(
            "Memory consolidation complete | block=%d rounds=%d-%d interactions=%d",
            block_id,
            block_start,
            block_end,
            len(interactions),
        )
        return summary_text

    def _save_and_cleanup(
        self,
        block_id: int,
        summary_text: str,
        block_start: int,
        block_end: int,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute(
                """
                INSERT INTO memory_summaries (round_block, summary_text)
                VALUES (?, ?)
                ON CONFLICT(round_block) DO UPDATE SET
                    summary_text = excluded.summary_text
                """,
                (block_id, summary_text),
            )
            conn.execute(
                "DELETE FROM interactions WHERE round_num BETWEEN ? AND ?",
                (block_start, block_end),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def vacuum(self) -> None:
        """Run VACUUM to reclaim disk space after heavy deletions."""
        conn = self._connect()
        try:
            conn.execute("VACUUM;")
            logger.info("SQLite VACUUM completed")
        finally:
            conn.close()

    def close(self) -> None:
        """Placeholder for API consistency (connections are per-call)."""
        pass


__all__ = ["LocalMemory"]
