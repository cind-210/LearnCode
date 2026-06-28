# Skills 进入提示词

Skills 代码在 `src/skills/registry.py`。当前实现只做一件事：读取本地 `.md` 或 `.txt` 文件，把 skill 名称和描述放进 system message。

## skill 文件

一个 skill 文件可以这样写：

```text
# name: frontend
# description: frontend workflow

这里是 skill 的完整提示文本。
```

`# name:` 和 `# description:` 是可选的。

读取后的结构大致是：

```json
{
  "name": "frontend",
  "description": "frontend workflow",
  "prompt": "文件完整内容",
  "source": "C:/path/to/frontend.md"
}
```

如果没有 `# name:`，代码使用文件名去掉后缀作为 name。如果没有 `# description:`，description 是空字符串。

## 读取目录

`load_skills_from_directory(directory)` 会扫描一个目录的直接子文件，只读取：

```text
*.md
*.txt
```

每个文件都会交给 skill 解析函数。解析成功后注册进 `SkillRegistry`。

注册表内部形状大致是：

```json
{
  "skills": {
    "frontend": {
      "name": "frontend",
      "description": "frontend workflow",
      "prompt": "文件完整内容",
      "source": "C:/path/to/frontend.md"
    }
  }
}
```

同名 skill 会被后注册的覆盖。

## 放进 system message

agent loop 第一次准备 system message 时，会读取默认 skill 注册表，然后生成一段 XML。

生成的结构大致是：

```xml
<available_skills>
<skill>
<name>
frontend
</name>
<description>
frontend workflow
</description>
</skill>
</available_skills>
```

这段 XML 会拼进 system message，和 workspace、工具调用格式、权限模式一起发给模型。

模型实际看到的是 skill 名称和描述，不会自动看到完整 `prompt`。

这一步只影响发给模型的 system message，不会注册工具，也不会启动外部进程。

## 没有 skills 时

如果注册表为空，`build_system_prompt()` 返回空字符串。system prompt 里会写没有可用 skills 的说明。

## 没有实现的 skill 功能

当前 skills 不是完整插件系统。还没有：

- 自动从多个目录发现 skills。
- `load_skill` 工具。
- skills 的 add/list/remove 命令。
- `SKILL.md` 包目录支持。
- 模型按需读取某个 skill 完整内容。

## 当前边界

- skills 现在只是 system message 里的提示信息。
- `prompt` 字段会保存完整文件内容，但当前不会自动注入模型。
- 默认 skill 注册表初始为空，需要代码主动注册或加载。
