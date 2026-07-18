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
    content = p.read_text(encoding="utf-8")

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


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---"):
        return {}, content

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        return {}, content

    data: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            data[key] = value

    body = "\n".join(lines[end_index + 1:])
    if content.endswith("\n"):
        body += "\n"
    return data, body


def parse_skill_md(path: str, fallback_name: str = "") -> Optional[SkillDefinition]:
    p = Path(path)
    if not p.is_file():
        return None

    content = p.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(content)
    disabled = frontmatter.get("disable-model-invocation", "").lower()
    if disabled in ("true", "yes", "1"):
        return None

    name = frontmatter.get("name") or fallback_name or p.parent.name
    description = frontmatter.get("description") or frontmatter.get("whenToUse") or ""
    prompt = body if frontmatter else content

    return SkillDefinition(
        name=name.strip(),
        description=description.strip(),
        prompt=prompt,
        source=str(p),
    )


def load_skills_from_claude_directory(directory: str) -> SkillRegistry:
    registry = SkillRegistry()
    root = Path(directory)
    if not root.is_dir():
        return registry

    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        skill_path = entry / "SKILL.md"
        if not skill_path.is_file():
            continue
        skill = parse_skill_md(str(skill_path), fallback_name=entry.name)
        if skill:
            registry.register(skill)
    return registry


def discover_skills(workspace: str) -> SkillRegistry:
    registry = SkillRegistry()
    roots = [
        Path.home() / ".learncode" / "skills",
        Path.home() / ".claude" / "skills",
        Path(workspace) / ".claude" / "skills",
    ]
    for root in roots:
        discovered = load_skills_from_claude_directory(str(root))
        for skill in discovered.list():
            registry.register(skill)
    return registry


def load_skills_from_directory(directory: str) -> SkillRegistry:
    registry = SkillRegistry()
    if not os.path.isdir(directory):
        return registry
    for entry in os.scandir(directory):
        if entry.is_file() and (entry.name.endswith(".md") or entry.name.endswith(".txt")):
            skill = parse_skill_file(entry.path)
            if skill:
                registry.register(skill)
    return registry


DEFAULT_SKILL_REGISTRY = SkillRegistry()


def get_default_skill_registry() -> SkillRegistry:
    return DEFAULT_SKILL_REGISTRY


def reset_default_skill_registry() -> None:
    global DEFAULT_SKILL_REGISTRY
    DEFAULT_SKILL_REGISTRY = SkillRegistry()


def refresh_default_skill_registry(workspace: str) -> SkillRegistry:
    global DEFAULT_SKILL_REGISTRY
    DEFAULT_SKILL_REGISTRY = discover_skills(workspace)
    return DEFAULT_SKILL_REGISTRY
