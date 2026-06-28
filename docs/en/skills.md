# Skills

Skills are implemented in `src/skills/registry.py`.

## Data Model

`SkillDefinition`:

- `name`
- `description`
- `prompt`
- `source`

`SkillRegistry` stores skills by name and can render a small system prompt section.

## Skill Parsing

`parse_skill_file(path)` reads UTF-8 text from `.md` or `.txt` files.

Supported metadata lines:

```text
# name: frontend
# description: frontend workflow
```

If no `# name:` line exists, the filename stem becomes the skill name.

The full file content becomes `prompt`.

## Directory Loading

`load_skills_from_directory(directory)` scans one directory level and registers `.md` or `.txt` files.

## System Prompt Rendering

`SkillRegistry.build_system_prompt()` returns:

```xml
<available_skills>
<skill>
<name>
...
</name>
<description>
...
</description>
</skill>
</available_skills>
```

The agent loop currently uses `get_default_skill_registry()`.

## Current Limitations Compared With MiniCode

- Skills are not automatically discovered from `.learncode/skills`, `.claude/skills`, or home directories.
- `load_skill` tool is not implemented.
- There are no `skills add/list/remove` management commands.
- `SKILL.md` package-style directories are not fully supported.
- Skill prompt content is stored but not loaded on demand by a tool.
