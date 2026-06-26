# Skills

Skills 实现在 `src/skills.py`。

## 数据模型

`SkillDefinition`：

- `name`
- `description`
- `prompt`
- `source`

`SkillRegistry` 按名称存储 skills，并能渲染一个小型系统提示词段落。

## Skill 解析

`parse_skill_file(path)` 会读取 UTF-8 文本，支持 `.md` 或 `.txt` 文件。

支持的元数据行：

```text
# name: frontend
# description: frontend workflow
```

如果没有 `# name:` 行，文件名 stem 会成为 skill name。

完整文件内容会成为 `prompt`。

## 目录加载

`load_skills_from_directory(directory)` 扫描一级目录，并注册 `.md` 或 `.txt` 文件。

## 系统提示词渲染

`SkillRegistry.build_system_prompt()` 返回：

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

Agent loop 当前使用 `get_default_skill_registry()`。

## 与 MiniCode 相比的当前限制

- 不会自动从 `.mini-code/skills`、`.claude/skills` 或用户 home 目录发现 skills。
- 没有实现 `load_skill` 工具。
- 没有 `skills add/list/remove` 管理命令。
- 没有完整支持 `SKILL.md` package-style 目录。
- Skill prompt 内容虽然被保存，但还不能由工具按需加载。
