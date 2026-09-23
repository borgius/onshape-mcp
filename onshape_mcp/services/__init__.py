"""Domain services used by the consolidated MCP surface."""

from .destructive import DestructiveOperationGate
from .discovery import DiscoveryService
from .featurescript import (
    FeatureScriptDocument,
    FeatureScriptDocumentationIndex,
    FeatureScriptWorkflowService,
)
from .gateway import OpenAPIGateway
from .inspection import (
    AssemblyInspectionService,
    GeometryInspectionService,
    PartStudioInspectionService,
)
from .translations import TranslationService
from .writes import (
    AssemblyWriteService,
    ObjectWriteService,
    PartFeatureWriteService,
    SketchWriteService,
    VariableWriteService,
)

__all__ = [
    "AssemblyInspectionService",
    "AssemblyWriteService",
    "DestructiveOperationGate",
    "DiscoveryService",
    "FeatureScriptDocument",
    "FeatureScriptDocumentationIndex",
    "FeatureScriptWorkflowService",
    "GeometryInspectionService",
    "ObjectWriteService",
    "OpenAPIGateway",
    "PartFeatureWriteService",
    "PartStudioInspectionService",
    "SketchWriteService",
    "TranslationService",
    "VariableWriteService",
]
