"""Canonical profile schema (all fields optional so partial profiles validate)."""

import uuid

from pydantic import BaseModel, Field


class Basics(BaseModel):
    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list)


class Achievement(BaseModel):
    id: str = ""
    text: str = ""


class Experience(BaseModel):
    id: str = ""
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    location: str = ""
    description: str = ""
    achievements: list[Achievement] = Field(default_factory=list)


class Education(BaseModel):
    id: str = ""
    institution: str = ""
    degree: str = ""
    field: str = ""
    start: str = ""
    end: str = ""


class Skill(BaseModel):
    id: str = ""
    name: str = ""
    level: str | None = None


class Language(BaseModel):
    id: str = ""
    name: str = ""
    level: str = ""


class Certification(BaseModel):
    id: str = ""
    name: str = ""
    issuer: str = ""
    date: str = ""


class Project(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    url: str = ""


class CanonicalProfile(BaseModel):
    basics: Basics = Field(default_factory=Basics)
    summary: str = ""
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def assign_ids(profile: CanonicalProfile) -> CanonicalProfile:
    """Return a copy with missing ids filled; existing ids are preserved."""
    out = profile.model_copy(deep=True)
    for name in CanonicalProfile.model_fields:
        value = getattr(out, name)
        if not isinstance(value, list):
            continue
        for item in value:
            if not item.id:
                item.id = _new_id()
            for ach in getattr(item, "achievements", []):
                if not ach.id:
                    ach.id = _new_id()
    return out
