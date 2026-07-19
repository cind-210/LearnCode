"""Global character definitions for session initialization."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config.runtime import LEARN_CODE_CHARACTERS_DIR
from ..tools.permissions import PermissionMode, PermissionRules


DEFAULT_EXPERIENCE_COMPACT_THRESHOLD = 12000


@dataclass
class Character:
    name: str
    description: str = ""
    prompt: str = ""
    experiences: list[str] = field(default_factory=list)
    skills: Optional[list[str]] = None
    tools: Optional[list[str]] = None
    permissions: PermissionRules = field(default_factory=PermissionRules)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: Optional[PermissionMode] = None
    max_turns: Optional[int] = None
    experience_compact_threshold: int = DEFAULT_EXPERIENCE_COMPACT_THRESHOLD
    source: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "") -> Character:
        raw_mode = data.get("permissionMode")
        return cls(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            prompt=str(data.get("prompt") or ""),
            experiences=_string_list(data.get("experiences")),
            skills=_string_list(data["skills"]) if isinstance(data.get("skills"), list) else None,
            tools=_string_list(data["tools"]) if isinstance(data.get("tools"), list) else None,
            permissions=PermissionRules.from_dict(data.get("permissions", {})),
            disallowed_tools=_string_list(data.get("disallowedTools")),
            permission_mode=PermissionMode(raw_mode) if raw_mode else None,
            max_turns=int(data["maxTurns"]) if data.get("maxTurns") is not None else None,
            experience_compact_threshold=int(data.get(
                "experienceCompactThreshold",
                DEFAULT_EXPERIENCE_COMPACT_THRESHOLD,
            )),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "experiences": list(self.experiences),
            "skills": list(self.skills) if self.skills is not None else None,
            "tools": list(self.tools) if self.tools is not None else None,
            "permissions": self.permissions.to_dict(),
            "disallowedTools": list(self.disallowed_tools),
            "permissionMode": self.permission_mode.value if self.permission_mode else None,
            "maxTurns": self.max_turns,
            "experienceCompactThreshold": self.experience_compact_threshold,
        }

    def system_prompt(self) -> str:
        parts = [self.prompt.strip()]
        if self.experiences:
            parts.extend([
                "",
                "<character_experiences>",
                "\n".join(f"- {item}" for item in self.experiences if item.strip()),
                "</character_experiences>",
            ])
        return "\n".join(part for part in parts if part is not None).strip()


GENERAL_PURPOSE_CHARACTER = Character(
    name="general-purpose",
    description=(
        "General-purpose character for researching complex questions, searching code, "
        "and executing multi-step tasks."
    ),
    prompt=(
        "You are a focused child session for LearnCode. Complete the delegated task fully, "
        "using the available tools when helpful. You start with only the task prompt, "
        "not the parent conversation, so rely on the provided instructions and inspect "
        "the workspace as needed. When done, respond with a concise report covering "
        "what you did and any key findings."
    ),
    skills=["*"],
    tools=["*"],
)


def load_characters() -> list[Character]:
    return _dedupe_by_name(_load_character_dirs(LEARN_CODE_CHARACTERS_DIR))


def ensure_general_purpose_character() -> None:
    path = _character_path(GENERAL_PURPOSE_CHARACTER.name)
    if path.is_file():
        return
    save_character(GENERAL_PURPOSE_CHARACTER)


def find_character(characters: list[Character], name: str | None) -> Character | None:
    if not name:
        return None
    target = name
    for character in characters:
        if character.name == target:
            return character
    return None


def append_character_experiences(character: Character, experiences: list[str]) -> Character:
    if not experiences or not character.source:
        return character
    merged = _dedupe_lines(character.experiences + experiences)
    character.experiences = merged
    save_character(character)
    return character


def replace_character_experiences(character: Character, experiences: list[str]) -> Character:
    if not character.source:
        return character
    character.experiences = _dedupe_lines(experiences)
    save_character(character)
    return character


def save_character(character: Character) -> None:
    path = Path(character.source) if character.source else _character_path(character.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(character.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _character_path(name: str) -> Path:
    return LEARN_CODE_CHARACTERS_DIR / name / "character.json"


def _load_character_dirs(path: Path) -> list[Character]:
    if not path.is_dir():
        return []
    characters: list[Character] = []
    for entry in sorted(path.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        char_path = entry / "character.json"
        if char_path.is_file():
            characters.append(_load_character_file(char_path))
    return characters


def _load_character_file(path: Path) -> Character:
    data = json.loads(path.read_text(encoding="utf-8"))
    character = Character.from_dict(data if isinstance(data, dict) else {}, source=str(path))
    if not character.name:
        character.name = path.parent.name
    return character


def _dedupe_by_name(characters: list[Character]) -> list[Character]:
    by_name: dict[str, Character] = {}
    for character in characters:
        if character.name:
            by_name[character.name] = character
    return list(by_name.values())


def _dedupe_lines(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        normalized = " ".join(text.lower().split())
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]
