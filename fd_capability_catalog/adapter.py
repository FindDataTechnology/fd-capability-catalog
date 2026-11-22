"""Capability Catalog — an ontology domain over the six kernel ports.

The catalog is a **federated projection**: one record per semantic capability
(concept / indicator / graph / dataset) drawn from each semantic system's own
list endpoint. It never copies their data — it holds only the index. Governance
runs under ``fd-ontology-kernel`` (one write path, closed vocabulary, identity =
scope + kind + external key, append-only events), so this module is an *adapter*,
not a second mechanism.

Storage: SQLite (one file). The catalog is small (hundreds to low thousands of
rows) and read-mostly; SQLite keeps it self-contained and in-repo like the other
daas sinks.

Change: establish-capability-plane (task 1.1).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from fd_ontology_kernel.contracts import (
    Consumer,
    Event,
    Proposal,
    Publication,
    PullEvent,
    Unit,
)
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel
from fd_ontology_kernel.vocab import Vocabulary

#: What this system claims to speak. Shape 2 = entries addressed by semantic key
#: (capabilities are addressed by ``federated_id``, a semantic key, not a row id).
DECLARATION = AdapterDeclaration(
    system="fd-capability-catalog",
    kernel_contract="1.0",
    manifest_shape=2,
)

#: The closed set of capability kinds. A value outside this tuple is refused by
#: the kernel's vocabulary gate before anything is written (spec:
#: capability-catalog / "目录跑在本体内核纪律下").
CAPABILITY_KINDS: tuple[str, ...] = ("concept", "indicator", "graph", "dataset")

#: Relationship kinds the catalog uses (e.g. served-by links, equivalence edges
#: once crosswalk alignment is wired). Kept minimal for S0.
RELATIONSHIP_KINDS: tuple[str, ...] = ("served_by", "equivalent_to")


class CapabilityVocabularySource:
    """Supplies the closed vocabularies per scope. Same sets for every scope.

    The kernel gates writes against the ``entity_types`` set by default; our
    capability kinds live there. ``relationship_kinds`` backs relationship
    writes (served_by / equivalent_to).
    """

    def load(self, *, scope_id: str) -> Vocabulary:
        return Vocabulary.from_values(
            release="capability-1",
            values={
                "entity_types": CAPABILITY_KINDS,
                "relationship_kinds": RELATIONSHIP_KINDS,
            },
        )


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS units (
            scope_id     TEXT NOT NULL,
            kind         TEXT NOT NULL,
            external_key TEXT NOT NULL,
            body         TEXT NOT NULL DEFAULT '',
            tier         TEXT NOT NULL DEFAULT 'raw',
            tags         TEXT NOT NULL DEFAULT '{}',
            attrs        TEXT NOT NULL DEFAULT '{}',
            source_path  TEXT,
            provenance   TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (scope_id, kind, external_key)
        );
        CREATE TABLE IF NOT EXISTS events (
            seq         INTEGER,
            scope_id    TEXT NOT NULL,
            op          TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_key  TEXT NOT NULL,
            actor       TEXT,
            via         TEXT,
            before      TEXT,
            after       TEXT,
            target_ref  TEXT,
            source_ref  TEXT,
            source_instance_id TEXT,
            occurred_at TEXT
        );
        CREATE TABLE IF NOT EXISTS publications (
            scope_id TEXT NOT NULL,
            version  INTEGER NOT NULL,
            digest   TEXT NOT NULL,
            payload  TEXT NOT NULL,
            PRIMARY KEY (scope_id, version)
        );
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id      TEXT NOT NULL,
            source        TEXT NOT NULL,
            kind          TEXT NOT NULL,
            external_key  TEXT NOT NULL,
            body          TEXT NOT NULL DEFAULT '',
            evidence      TEXT NOT NULL DEFAULT '{}',
            evidence_digest TEXT NOT NULL,
            submitted_by  TEXT NOT NULL,
            state         TEXT NOT NULL DEFAULT 'pending',
            sources       TEXT NOT NULL DEFAULT '[]',
            impact        TEXT NOT NULL DEFAULT '',
            decided_by    TEXT,
            decision_note TEXT
        );
        """
    )
    conn.commit()


class SQLiteUnitStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, *, scope_id: str, kind: str, external_key: str) -> Optional[Unit]:
        row = self._conn.execute(
            "SELECT * FROM units WHERE scope_id=? AND kind=? AND external_key=?",
            (scope_id, kind, external_key),
        ).fetchone()
        return self._row_to_unit(row) if row else None

    def upsert(self, unit: Unit) -> Unit:
        self._conn.execute(
            """INSERT INTO units (scope_id,kind,external_key,body,tier,tags,attrs,source_path,provenance)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(scope_id,kind,external_key) DO UPDATE SET
                 body=excluded.body, tier=excluded.tier, tags=excluded.tags,
                 attrs=excluded.attrs, source_path=excluded.source_path,
                 provenance=excluded.provenance""",
            (
                unit.scope_id, unit.kind, unit.external_key, unit.body, unit.tier,
                json.dumps(dict(unit.tags), sort_keys=True),
                json.dumps(dict(unit.attrs), sort_keys=True),
                unit.source_path,
                json.dumps(dict(unit.provenance), sort_keys=True),
            ),
        )
        self._conn.commit()
        return unit

    def list_live(self, *, scope_id: str) -> list[Unit]:
        rows = self._conn.execute(
            "SELECT * FROM units WHERE scope_id=? ORDER BY kind, external_key",
            (scope_id,),
        ).fetchall()
        return [self._row_to_unit(r) for r in rows]

    def live_key_kinds(self, *, scope_id: str) -> dict[str, str]:
        return {u.external_key: u.kind for u in self.list_live(scope_id=scope_id)}

    def raw_count(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM units").fetchone()[0])

    @staticmethod
    def _row_to_unit(row: sqlite3.Row) -> Unit:
        return Unit(
            scope_id=row["scope_id"],
            kind=row["kind"],
            external_key=row["external_key"],
            body=row["body"],
            tier=row["tier"],
            tags=json.loads(row["tags"]),
            attrs=json.loads(row["attrs"]),
            source_path=row["source_path"],
            provenance=json.loads(row["provenance"]),
        )


class SQLiteJournal:
    """Append-only. No update/delete path exists, by contract."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def next_seq(self, scope_id: str) -> int:
        row = self._conn.execute(
            "SELECT max(seq) FROM events WHERE scope_id=?", (scope_id,)
        ).fetchone()
        return int(row[0] or 0) + 1

    def append(self, event: Event) -> Event:
        self._conn.execute(
            """INSERT INTO events (seq,scope_id,op,target_kind,target_key,actor,via,
               before,after,target_ref,source_ref,source_instance_id,occurred_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event.seq, event.scope_id, event.op, event.target_kind,
                event.target_key, event.actor, event.via,
                json.dumps(event.before, sort_keys=True) if event.before is not None else None,
                json.dumps(event.after, sort_keys=True) if event.after is not None else None,
                event.target_ref, event.source_ref, event.source_instance_id,
                event.occurred_at,
            ),
        )
        self._conn.commit()
        return event

    def fold(self, *, scope_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE scope_id=? ORDER BY seq, target_key", (scope_id,)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count_since(
        self, *, scope_id: str, seq: Optional[int] = None, since: Optional[str] = None
    ) -> int:
        return len(
            [e for e in self.fold(scope_id=scope_id) if seq is None or (e.seq or 0) > int(seq)]
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            scope_id=row["scope_id"], op=row["op"], target_kind=row["target_kind"],
            target_key=row["target_key"], actor=row["actor"], via=row["via"],
            before=json.loads(row["before"]) if row["before"] else None,
            after=json.loads(row["after"]) if row["after"] else None,
            target_ref=row["target_ref"], seq=row["seq"],
            source_ref=row["source_ref"], source_instance_id=row["source_instance_id"],
            occurred_at=row["occurred_at"],
        )


class SQLitePublicationStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def latest(self, *, scope_id: str) -> Optional[Publication]:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE scope_id=? ORDER BY version DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        return self._row_to_pub(row) if row else None

    def get(self, *, scope_id: str, version: Optional[int] = None,
            digest: Optional[str] = None) -> Optional[Publication]:
        if version is not None:
            row = self._conn.execute(
                "SELECT * FROM publications WHERE scope_id=? AND version=?",
                (scope_id, version),
            ).fetchone()
        elif digest is not None:
            row = self._conn.execute(
                "SELECT * FROM publications WHERE scope_id=? AND digest=?",
                (scope_id, digest),
            ).fetchone()
        else:
            return None
        return self._row_to_pub(row) if row else None

    def next_version(self, *, scope_id: str) -> int:
        row = self._conn.execute(
            "SELECT max(version) FROM publications WHERE scope_id=?", (scope_id,)
        ).fetchone()
        return int(row[0] or 0) + 1

    def insert(self, publication: Publication) -> Publication:
        self._conn.execute(
            "INSERT INTO publications (scope_id,version,digest,payload) VALUES (?,?,?,?)",
            (publication.scope_id, publication.version, publication.digest,
             json.dumps(publication.payload, sort_keys=True)),
        )
        self._conn.commit()
        return publication

    def count(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM publications").fetchone()[0])

    @staticmethod
    def _row_to_pub(row: sqlite3.Row) -> Publication:
        return Publication(
            scope_id=row["scope_id"], version=row["version"], digest=row["digest"],
            payload=json.loads(row["payload"]),
        )


class SQLiteProposalBox:
    """Nominations awaiting a human decision. Seven methods, per the contract.

    A proposal never writes to units — only ``Kernel.confirm`` does, via
    ``Kernel.write``. Evidence is validated by ``proposals.submit`` before it
    reaches this port; this class only stores.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def find_claim(self, *, scope_id: str, external_key: str,
                   evidence_digest: str) -> Optional[Proposal]:
        row = self._conn.execute(
            """SELECT * FROM proposals WHERE scope_id=? AND external_key=?
               AND evidence_digest=? AND state='pending'""",
            (scope_id, external_key, evidence_digest),
        ).fetchone()
        return self._row_to_proposal(row) if row else None

    def insert(self, proposal: Proposal) -> Proposal:
        cur = self._conn.execute(
            """INSERT INTO proposals (scope_id,source,kind,external_key,body,evidence,
               evidence_digest,submitted_by,state,sources,impact,decided_by,decision_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                proposal.scope_id, proposal.source, proposal.kind, proposal.external_key,
                proposal.body, json.dumps(dict(proposal.evidence), sort_keys=True),
                proposal.evidence_digest, proposal.submitted_by, proposal.state,
                json.dumps(list(proposal.sources), ensure_ascii=False), proposal.impact,
                proposal.decided_by, proposal.decision_note,
            ),
        )
        self._conn.commit()
        from dataclasses import replace
        return replace(proposal, proposal_id=cur.lastrowid)

    def merge_submitters(self, *, proposal_id: int, submitters) -> Proposal:
        self._conn.execute(
            "UPDATE proposals SET sources=? WHERE proposal_id=?",
            (json.dumps(list(submitters), ensure_ascii=False), int(proposal_id)),
        )
        self._conn.commit()
        return self.get(scope_id=self._scope_of(proposal_id), proposal_id=proposal_id)

    def find_by_key(self, *, scope_id: str, external_key: str,
                    state: Optional[str] = None) -> Optional[Proposal]:
        if state is None:
            row = self._conn.execute(
                "SELECT * FROM proposals WHERE scope_id=? AND external_key=? ORDER BY proposal_id DESC LIMIT 1",
                (scope_id, external_key),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM proposals WHERE scope_id=? AND external_key=? AND state=? ORDER BY proposal_id DESC LIMIT 1",
                (scope_id, external_key, state),
            ).fetchone()
        return self._row_to_proposal(row) if row else None

    def get(self, *, scope_id: str, proposal_id: int) -> Optional[Proposal]:
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE scope_id=? AND proposal_id=?",
            (scope_id, int(proposal_id)),
        ).fetchone()
        return self._row_to_proposal(row) if row else None

    def decide(self, *, proposal_id: int, state: str, decided_by: str,
               note: str = "") -> Proposal:
        self._conn.execute(
            "UPDATE proposals SET state=?, decided_by=?, decision_note=? WHERE proposal_id=?",
            (state, decided_by, note, int(proposal_id)),
        )
        self._conn.commit()
        return self.get(scope_id=self._scope_of(proposal_id), proposal_id=proposal_id)

    def list_pending(self, *, scope_id: str, batch: Optional[str] = None,
                     limit: int = 200) -> list[Proposal]:
        rows = self._conn.execute(
            "SELECT * FROM proposals WHERE scope_id=? AND state='pending' ORDER BY proposal_id LIMIT ?",
            (scope_id, limit),
        ).fetchall()
        return [self._row_to_proposal(r) for r in rows]

    def _scope_of(self, proposal_id: int) -> str:
        row = self._conn.execute(
            "SELECT scope_id FROM proposals WHERE proposal_id=?", (int(proposal_id),)
        ).fetchone()
        return row["scope_id"] if row else ""

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> Proposal:
        return Proposal(
            scope_id=row["scope_id"], source=row["source"], kind=row["kind"],
            external_key=row["external_key"], body=row["body"],
            evidence=json.loads(row["evidence"]), evidence_digest=row["evidence_digest"],
            submitted_by=row["submitted_by"], state=row["state"],
            sources=tuple(json.loads(row["sources"])), impact=row["impact"],
            proposal_id=row["proposal_id"], decided_by=row["decided_by"],
            decision_note=row["decision_note"],
        )


def build_kernel(db_path: str) -> Kernel:
    """Wire the six-port adapter into a Kernel bound to one SQLite file.

    Only the three required ports (units, events, vocabulary) plus publications
    are supplied; the catalog's S0 slice does not need proposals, distribution,
    or relationships-with-decisions yet. Kernel refuses to construct without the
    required ports and owns no storage of its own — this is exactly the shape the
    kernel contract asks for.
    """
    conn = _connect(db_path)
    _init_schema(conn)
    return Kernel(
        units=SQLiteUnitStore(conn),
        events=SQLiteJournal(conn),
        vocabulary=CapabilityVocabularySource(),
        publications=SQLitePublicationStore(conn),
        proposals=SQLiteProposalBox(conn),
        relationship_kinds=RELATIONSHIP_KINDS,
        clause_kinds=(),
        manifest_shape=2,
        declaration=DECLARATION,
    )