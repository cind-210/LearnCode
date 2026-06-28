# Agent Loop

代码位置：`src/loop/runner.py`

agent loop 只处理一条用户输入。它把用户输入加入消息列表，请求模型；如果模型要调用工具，就执行工具并把结果交回模型；如果模型给出普通回答，就结束。

## 入口

浏览器发送 `chat` 后，后端会先创建或加载 session，再把这些信息交给 agent loop：

- 用户这次输入的文本。
- session 里恢复出来的历史消息。
- 当前 workspace。
- 权限模式。
- 模型 adapter。

WebSocket 的完整输入输出结构看 [api.md](api.md)。

## 准备发给模型的消息

agent loop 会把当前用户输入追加到消息列表。如果这是没有历史消息的新 session，会先放入一条 system message。

system message 是给模型看的规则，内容包括：

- 当前 workspace。
- 工具调用格式。
- 权限模式。
- skills 提供的提示文本。

skills 提示文本会被拼成类似下面的结构：

```xml
<available_skills>
<skill>
<name>frontend</name>
<description>frontend workflow</description>
</skill>
</available_skills>
```

如果没有 skills，会写入没有可用 skills 的说明。skill 文件怎么解析看 [skills.md](skills.md)。

新 session 第一次请求模型时，消息列表大致是：

```json
[
  {"role": "system", "content": "给模型看的规则"},
  {"role": "user", "content": "用户输入"}
]
```

已有 session 会带上历史消息：

```json
[
  {"role": "system", "content": "给模型看的规则"},
  {"role": "user", "content": "上一条用户消息"},
  {"role": "assistant", "content": "上一条模型回复"},
  {"role": "user", "content": "当前用户输入"}
]
```

## 请求模型

每次请求模型前，代码会先检查内存里的消息列表是否太长。需要时会先缩短这份消息列表，再生成实际发送内容。压缩细节看 [context.md](context.md)。

然后调用模型 adapter。adapter 负责把内部消息转成 Anthropic 或 OpenAI-compatible API 需要的格式。请求体细节看 [models-config.md](models-config.md)。

## 模型返回普通回答

如果模型已经能回答用户，adapter 会返回普通 assistant step：

```json
{
  "type": "assistant",
  "content": "最终回答",
  "kind": "final",
  "calls": null
}
```

后端接着会：

1. 把这条 assistant 消息加入消息列表。
2. 把这一步结果发给前端显示。
3. 结束 agent loop。
4. 保存 session。
5. 发送 `done`，前端恢复发送按钮。

## 模型返回工具调用

如果模型需要读文件、搜索或执行命令，adapter 会返回工具调用 step：

```json
{
  "type": "tool_calls",
  "content": "我先读取 README。",
  "calls": [
    {
      "id": "call_xxx",
      "tool_name": "read_file",
      "input": {"path": "README.md"}
    }
  ]
}
```

后端接着会：

1. 把模型在工具调用前输出的文字发给前端显示。
2. 保存每个工具调用的工具名和输入。
3. 执行工具。
4. 把工具结果加入消息列表。
5. 回到“请求模型”步骤。下一次请求模型前会再次检查消息长度。

所以一次用户输入里，模型可能会被请求多次：

```text
用户：总结 README
模型：需要读取 README，所以调用 read_file
工具：返回 README 内容
模型：根据 README 内容生成总结
```

工具定义、工具执行输入、工具结果结构看 [tools.md](tools.md)。

## 循环次数

代码里有一个计数器，记录这次用户输入里模型决策了几次。

- 用户输入进入 agent loop 时记一次。
- 每次工具执行完、准备再次请求模型时再记一次。

它只用于限制循环次数，避免模型一直调用工具停不下来。

## 结束

本次处理会在这些情况下结束：

- 模型给出最终回答。
- 模型返回的内容无法处理。
- 循环次数超过限制。

结束后，后端把这次用户输入产生的消息追加到 session 文件，然后通过 WebSocket 发 `done`。session 文件格式看 [sessions.md](sessions.md)。

## 当前边界

- 权限检查存在，前端没有完整审批弹窗。
- 自动压缩主要影响当前运行中的消息列表；保存 session 时只追加当前能保存出来的消息和事件。
- memory 模块已经初始化，但当前循环没有实际使用它。
