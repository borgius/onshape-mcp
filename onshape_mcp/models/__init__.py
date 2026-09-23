"""Shared models used by consolidated Onshape services."""

from .references import OnshapeTarget, ResourceRef
from .results import Diagnostic, ToolResult

__all__ = ["Diagnostic", "OnshapeTarget", "ResourceRef", "ToolResult"]
