"""Normalized references for Onshape documents and model resources."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OnshapeTarget(BaseModel):
    """A document target with exactly one workspace/version selector."""

    document_id: str = Field(alias="documentId", min_length=1)
    workspace_id: Optional[str] = Field(default=None, alias="workspaceId")
    version_id: Optional[str] = Field(default=None, alias="versionId")
    microversion_id: Optional[str] = Field(default=None, alias="microversionId")
    element_id: Optional[str] = Field(default=None, alias="elementId")
    part_id: Optional[str] = Field(default=None, alias="partId")
    feature_id: Optional[str] = Field(default=None, alias="featureId")
    instance_id: Optional[str] = Field(default=None, alias="instanceId")
    mate_id: Optional[str] = Field(default=None, alias="mateId")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def validate_version_selector(self) -> "OnshapeTarget":
        selectors = [self.workspace_id, self.version_id, self.microversion_id]
        if sum(value is not None for value in selectors) != 1:
            raise ValueError("exactly one of workspaceId, versionId, or microversionId is required")
        return self

    def as_api_params(self) -> dict[str, str]:
        return {
            key: value for key, value in self.model_dump(by_alias=True).items() if value is not None
        }

    def child(self, **updates: Any) -> "OnshapeTarget":
        values = self.model_dump(by_alias=True)
        values.update(updates)
        return type(self).model_validate(values)


class ResourceRef(BaseModel):
    """Small stable reference returned by discovery and inspection services."""

    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    name: Optional[str] = None
    target: Optional[OnshapeTarget] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DocumentRef(ResourceRef):
    kind: str = "document"


class ElementRef(ResourceRef):
    kind: str = "element"


class FeatureRef(ResourceRef):
    kind: str = "feature"


class InstanceRef(ResourceRef):
    kind: str = "instance"


class MateRef(ResourceRef):
    kind: str = "mate"


class TranslationRef(ResourceRef):
    kind: str = "translation"


class GeometryRef(ResourceRef):
    kind: str = "geometry"
