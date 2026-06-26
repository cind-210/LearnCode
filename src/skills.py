"""
Skills module - load and manage skill definitions.

Mirrors src/skills.ts from the TypeScript version.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SkillDefinition:
    name: str
    description: str
    prompt: str
    source: str = ""


@dataclass
class SkillRegistry:
    skills: dict[str, SkillDefinition] = field(default_factory=dict)

    def register(self, skill: SkillDefinition) -> None:
        self.skills[skill.name] = skill

    def get(self, name: str) -> Optional[SkillDefinition]:
        return self.skills.get(name)

    def list(self) -> list[SkillDefinition]:
        return list(self.skills.values())

    def names(self) -> list[str]:
        return list(self.skills.keys())

    def build_system_prompt(self) -> str:
        if not self.skills:
            return ""
        lines = ["<available_skills>"]
        for skill in self.skills.values():
            lines.append("<skill>")
            lines.append("<name>")
            lines.append(skill.name)
            lines.append("</name>")
            lines.append("<description>")
            lines.append(skill.description)
            lines.append("</description>")
            lines.append("</skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)


def parse_skill_file(path: str) -> Optional[SkillDefinition]:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        content = p.read_text(encoding="utf-8")
    except Exception:
        return None

    name = p.stem
    description = ""
    prompt = content

    for line in content.split("\n"):
        if line.startswith("# name:"):
            name = line[len("# name:"):].strip()
        elif line.startswith("# description:"):
            description = line[len("# description:"):].strip()

    return SkillDefinition(
        name=name,
        description=description,
        prompt=prompt,
        source=str(p),
    )


def load_skills_from_directory(directory: str) -> SkillRegistry:
    registry = SkillRegistry()
    try:
        for entry in os.scandir(directory):
            if entry.is_file() and (entry.name.endswith(".md") or entry.name.endswith(".txt")):
                skill = parse_skill_file(entry.path)
                if skill:
                    registry.register(skill)
    except FileNotFoundError:
        pass
    return registry


DEFAULT_SKILL_REGISTRY = SkillRegistry()


def get_default_skill_registry() -> SkillRegistry:
    return DEFAULT_SKILL_REGISTRY


def reset_default_skill_registry() -> None:
    global DEFAULT_SKILL_REGISTRY
    DEFAULT_SKILL_REGISTRY = SkillRegistry()