# 上下文过长时的缩短流程

模型每次请求都有长度限制。LearnCode 会在每次请求模型前检查上下文，必要时缩短发给模型的内容。相关代码主要在 `src/context` 和 `src/loop/runner.py`。

本文里的“上下文”指 agent loop 传给模型参考的对话内容，在代码里主要对应 `state.messages`。它**包含**用户输入、模型回复、工具调用、工具结果、摘要和边界；**不包含**模型名、工具 schema、`max_tokens`、base url 这些请求配置。adapter 会把上下文转换成 Anthropic 或 OpenAI-compatible API 需要的请求格式。

## 自动检查时机

自动检查发生在每次请求模型前。只要 agent loop 准备调用模型 adapter，就会先执行一次压缩检查。

```text
用户输入
压缩检查
请求模型
模型返回工具调用
执行工具
工具结果加入上下文
压缩检查
继续请求模型
```

如果模型直接回答，本次回答前已经做过一次压缩检查。

手动 Compact 是另一条路径：前端发送 `{"action": "compact"}` 后，后端才执行手动压缩。

## 工具结果限长

工具结果限长不属于 `_apply_compression()` 的四个步骤。它发生在工具执行之后、工具结果加入上下文之前。

工具执行完后，结果会先变成 `tool_result`：

```json
{
  "role": "tool_result",
  "tool_use_id": "call_xxx",
  "tool_name": "read_file",
  "content": "工具输出",
  "is_error": false
}
```

如果单个工具输出超过 50000 字符，会把完整输出写到本地文件，并在 `tool_result` 里只保留预览。

```text
~/.learncode/tool-results
```

如果一次模型返回多个工具调用，多个工具结果加起来也会被限制长度。限制完成后仍然是 `tool_result`，只是 `content` 可能变成预览和完整文件路径。

这一步不改变工具是否执行成功，只改变下一次发给模型的文本长度。

## 自动压缩顺序

每次请求模型前，`src/loop/runner.py` 会调用 `_apply_compression()`。函数调用顺序是：

```text
snip compact
    ↓
microcompact
    ↓
context collapse
    ↓
auto compact
```

这些函数都会被调用，但是否真的修改上下文由函数内部条件决定。

| 步骤 | 修改条件 | 主要影响 |
| --- | --- | --- |
| snip compact | 上下文占用率 `>= 70%`，并且能找到可移除的一段上下文 | 把这段上下文换成 `snip_boundary` |
| microcompact | 上下文占用率 `>= 50%`，并且可压缩工具结果超过 3 条 | 清空部分工具结果正文 |
| context collapse | 上下文占用率 `>= 75%`，并且能找到可总结的一段上下文 | 用 `context_summary` 替换这段上下文 |
| auto compact | 上下文占用率 `>= 85%`，并且模型摘要成功 | 用模型生成的摘要缩短上下文 |

这个顺序和 MiniCode 对齐：先尝试局部删除或局部摘要，最后才用模型做整体 auto compact。

## snip compact

如果当前上下文达到 `70%`，并且能找到一段安全的上下文，`src/context/compact/snip_compact.py` 会把这段上下文从后续模型上下文里移除，换成 `snip_boundary`。

裁剪后的上下文大致是：

```text
被裁剪段开始下标之前的上下文
snip_boundary
被裁剪段结束下标之后的上下文
```

`snip_boundary` 大致是：

```json
{
  "role": "snip_boundary",
  "content": "中间一段上下文被裁掉",
  "removed_message_ids": ["message id"],
  "removed_count": 12,
  "tokens_freed": 5000
}
```

snip compact 不请求模型。它会尽量保护文件修改相关内容、重要错误、还没闭合的工具调用和上下文最后 `12` 项内容。

触发还需要满足：

```text
至少能移除 6 项内容
预计至少节省 2000 token
上下文最后 12 项内容不进入候选裁剪范围
```

## microcompact

`src/context/compact/microcompact.py` 只处理工具结果。具体条件是：

1. 当前上下文占用率达到 `50%`。
2. 工具名在可压缩白名单里：`read_file`、`run_command`、`grep_files`、`list_files`、`web_fetch`。
3. 符合条件的工具结果数量超过 `3` 条。

保留标准按上下文顺序计算。位置越靠后，表示越接近这次模型请求。microcompact 会保留最后 `3` 条符合条件的工具结果正文，把它们之前的符合条件工具结果正文替换成简短说明。

替换前：

```json
{
  "role": "tool_result",
  "tool_name": "read_file",
  "content": "会被清空的工具输出正文"
}
```

替换后：

```json
{
  "role": "tool_result",
  "tool_name": "read_file",
  "content": "[Output cleared for context space]"
}
```

它不请求模型，也不写 `compact_boundary`。它只改变本次 agent loop 内存里的上下文，以及本次结束后追加保存出来的内容。

## context collapse

如果执行到 context collapse 时上下文仍达到 `75%`，`src/context/compact/context_collapse.py` 会请求模型总结一段上下文，然后在后续发给模型的上下文里，用 `context_summary` 替换这段内容。

替换前后的形状大致是：

```text
A
B
C
D
E
```

如果 `B`、`C`、`D` 是被 context collapse 替换的上下文，后续发给模型的上下文会变成：

```text
A
context_summary
E
```

候选范围同样不包含上下文最后 `12` 项内容。还没闭合的工具调用、已有边界、已经被 `context_summary` 替换过的内容不会进入候选范围。

具体机制是：

1. 从上下文里找一段符合条件的内容。
2. 把这段内容转成文本，发给模型生成摘要。
3. 摘要成功后，记录这段内容的 id 列表和摘要内容。
4. 后续准备发给模型时，根据这些 id，把这段内容替换成 `context_summary`。

context collapse 主要影响模型接下来要看的上下文，不会像手动 Compact 那样写入 `compact_boundary`。完整事件和会话结构见 [sessions.md](sessions.md)。

## auto compact

如果执行到 auto compact 时上下文仍达到 `85%`，`src/context/compact/auto_compact.py` 会请求模型，把一段上下文总结成摘要。

摘要大致是：

```json
{
  "role": "context_summary",
  "content": "被压缩上下文的摘要",
  "compressed_count": 20
}
```

自动 compact 当前主要影响这次运行中的内存上下文。它不像手动 Compact 那样由前端明确触发，也不在这里追加 `compact_boundary`。

## 手动 Compact

前端点 Compact 后，会通过 WebSocket 发送：

```json
{"action": "compact"}
```

后端请求模型把一段上下文压成摘要。压缩成功后，session 文件里会追加一个 `compact_boundary` 事件。

压缩成功后还会把摘要和保留内容继续追加到文件里。下次加载这个 session 时，只恢复最后一个 `compact_boundary` 之后的事件。完整事件结构看 [sessions.md](sessions.md)。

## 长度计算规则

`src/context/token_estimator.py` 用简单规则估算上下文长度。它看的结构大致是：

```json
{
  "role": "user",
  "content": "用户输入文本",
  "tool_name": "",
  "input": null,
  "provider_usage": null
}
```

如果模型 API 返回了真实 usage，相关内容里可能带上：

```json
{
  "provider_usage": {
    "input_tokens": 1000,
    "output_tokens": 200,
    "total_tokens": 1200,
    "source": "anthropic"
  }
}
```

`src/context/model_context.py` 根据模型名返回上下文窗口估算值。压缩代码用下面的比例判断是否达到阈值：

```text
上下文占用率 = 当前上下文估算 token / 模型可用输入窗口
```

## 保存和恢复

snip compact 不是把 session 文件里的原始事件物理删除。session 仍然是追加写入的 JSONL，原始内容还留在文件里，文件末尾追加一条 `snip_boundary` 事件记录哪些内容被移出了后续上下文。

下次加载 session 时，`src/sessions/store.py` 会根据 `snip_boundary` 里的 `removed_message_ids` 重建上下文：`uuid` 命中这些 id 的原始内容不会进入恢复后的上下文，而是由 `snip_boundary` 代替。

```text
JSONL 文件：原始内容仍在
模型上下文：原始内容被 snip_boundary 代替
session 恢复视图：原始内容被 snip_boundary 代替
```

## 当前边界

- 自动检查发生在每次请求模型前，不只发生在工具执行后。
- 手动 Compact 会写入 `compact_boundary`，自动压缩主要影响内存里的上下文。
- 大工具结果目录使用 `.learncode`。
- token 是估算值，不等于模型 API 的精确计数。
