"""Conformance adapter for the capability catalog (establish-capability-plane).

The gate wants a throwaway sandbox: everything written vanishes with it, never
touching a real catalog file. So ``_sandbox()`` builds a fresh in-memory SQLite
engine + the six ports each call, and the gate checks OUR port wiring — the
catalog's real storage mapping — not the kernel's in-memory sample.

The catalog's S0 slice uses units / events / vocabulary / publications only; it
has no writable proposal box and no distribution registry yet. Declaring those
absent turns the related assertions into reported skips *with this reason* —
leaving them undeclared would make the gate check as if we had everything and go
red on what we deliberately don't do yet.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fd_ontology_kernel.conformance.spec import AdapterSpec
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel

from .adapter import (
    CAPABILITY_KINDS,
    RELATIONSHIP_KINDS,
    CapabilityVocabularySource,
    SQLiteJournal,
    SQLiteProposalBox,
    SQLitePublicationStore,
    SQLiteUnitStore,
    _init_schema,
)

DECLARATION = AdapterDeclaration(
    system="fd-capability-catalog",
    kernel_contract="1.0",
    manifest_shape=2,
)


def _sandbox() -> Kernel:
    """Fresh in-memory catalog, wired through the real SQLite ports."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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


def build() -> AdapterSpec:
    """``adapters.json`` factory."""
    return AdapterSpec(
        name="fd-capability-catalog",
        system=DECLARATION.system,
        declaration=DECLARATION,
        sandbox=_sandbox,
        notes=(
            "federated capability index (concept/indicator/graph/dataset); "
            "SQLite sandbox over the real six-port adapter"
        ),
        capabilities={"proposals": "writable", "distribution": "absent"},
    )