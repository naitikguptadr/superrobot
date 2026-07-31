"""The Migration IR: the semantic model a source agent is recompiled from.

`model` holds the schema, `ledger` holds the accounting that makes a
silently-incomplete migration impossible.
"""

from superrobot.ir.ledger import CoverageLedger, LedgerError
from superrobot.ir.model import (
    ConfigVar,
    Coverage,
    CoverageEntry,
    Disposition,
    EntryPoint,
    Evidence,
    ExternalIO,
    IRElement,
    LlmCall,
    MigrationIR,
    Orchestration,
    OrchestrationEdge,
    OrchestrationNode,
    Residue,
    Severity,
    SourceFact,
    StateItem,
    Tool,
    ToolParam,
    TopologyKind,
)

__all__ = [
    "ConfigVar",
    "Coverage",
    "CoverageEntry",
    "CoverageLedger",
    "Disposition",
    "EntryPoint",
    "Evidence",
    "ExternalIO",
    "IRElement",
    "LedgerError",
    "LlmCall",
    "MigrationIR",
    "Orchestration",
    "OrchestrationEdge",
    "OrchestrationNode",
    "Residue",
    "Severity",
    "SourceFact",
    "StateItem",
    "Tool",
    "ToolParam",
    "TopologyKind",
]
