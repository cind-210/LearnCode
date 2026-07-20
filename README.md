<p align="center">
  <img src="asset/learncode.png" alt="LearnCode" width="300">
</p>

[English](README.en.md)

LearnCode 是一个自进化的 Agent 编程助手系统，也可以作为学习 agent 原理的 Python 项目。

这里的 Learn 有两层含义：一是相同角色的智能体共用配置、权限、技能范围和经验，并在真实对话中持续沉淀和更新经验，让角色随着使用自我演化；二是项目保留了 agent loop、工具调用、上下文压缩、session 持久化和多 agent 编排等机制，方便学习 agent 系统如何运行。

浏览器把用户消息发给 Python 后端，后端请求模型，按模型要求执行工具，保存 session，并通过 WebSocket 把中间步骤、工具结果和回复流式推送回页面。

![LearnCode Web 界面](asset/example.png)

![Agent Loop 和过程中的工具调用](asset/example-2.png)

## 启动方法

- Python 3.10 或更新版本。
- 可用的模型接口和 API key。

安装依赖：

```powershell
cd LearnCode
pip install -r requirements.txt
```

在 `.env` 里配置模型。

Anthropic 风格接口：

```env
LEARN_CODE_MODEL=claude-3-5-sonnet-latest
LEARN_CODE_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1/messages
LEARN_CODE_API_KEY=your-api-key
```

OpenAI-compatible 接口：

```env
LEARN_CODE_MODEL=deepseek-chat
LEARN_CODE_OPENAI_BASE_URL=https://api.deepseek.com/v1/chat/completions
LEARN_CODE_API_KEY=your-api-key
```

其他可选配置：

```env
LEARN_CODE_MAX_OUTPUT_TOKENS=4096
LEARN_CODE_MAX_RETRIES=4
WORKSPACE=C:\path\to\your\workspace
HOST=127.0.0.1
PORT=8080
```

如果没有设置 `WORKSPACE`，LearnCode 会使用启动服务器时所在的目录。

启动服务：

```powershell
python -m src.main
```

打开浏览器访问：

```text
http://127.0.0.1:8080
```

## 使用方式

在网页输入消息并发送。LearnCode 会创建或复用 session，请求已配置的模型，把思考、工具调用和工具结果显示在 Agent Loop 区块里，并把对话保存到 `.sessions`。

同一角色的 agent 会共享角色配置和经验。父 session 可以创建、fork、查看和向子 session 发送消息，用多 agent 的方式拆分任务；前端侧边栏会展示 session 树，并按当前打开的 session 接收流式更新。

Compact 按钮会把当前会话较早的上下文总结成摘要，减少后续模型请求需要携带的内容长度。这个按钮只是可选的手动压缩；即使不点击，后端也会根据上下文长度按规则自动压缩。

## 更新日志

<details>
<summary>展开查看</summary>

### 2026-07-20

- 重构 session 流式显示：只向当前打开的 session/subsession 推送 delta，加载中的运行会话会继续显示后续增量。
- Agent Loop 改为显式 `loop_end` 标识结束，不再依赖单独的 `assistant_final` 角色。
- Stop 和正常结束都会写入 loop end，前端据此稳定恢复 Agent Loop 区块。
- 修复父 session 通过 `SendMessage` 发给子 session 的消息持久化和前端加载。
- 优化 Agent Loop 自动滚动：只在 loop 区块内部跟随最新内容，不强制滚动整个页面。

### 2026-07-19

- 引入多 agent、character 和角色权限系统：角色可共享配置、技能范围、权限和经验。
- 支持创建、fork、命名、查看和向子 session 发送消息。
- 支持 regex 和 workspace 级权限规则，并完善 run command、沙箱权限和并发工具执行。
- Agent Loop 支持工具结果挂载、折叠展示和去重渲染。
- 统一第一条用户消息触发的自动命名逻辑，并支持手动创建 session 后自动命名。

### 2026-07-18

- 实现沙箱权限配置和工具并发执行。
- 优化 `run_command` 权限机制。

### 2026-07-13

- Thinking block 会作为上下文保存，并在前端 Agent Loop 中显示。
- Stop 功能会立即停止当前轮，并确保已经开始的工具调用都有闭合的 tool result。
- Anthropic 模型请求改为流式，以降低非流式请求下判断网络连接问题的成本。

</details>

## 已实现功能

- 基于 FastAPI 的 Web 对话页面。
- 基于 WebSocket 的 session 事件、agent step、delta、工具调用、工具结果和回复推送。
- Anthropic 风格和 OpenAI-compatible 两类模型适配器。
- 内置编码工具：读取文件、列出文件、搜索文件、编辑文件、写入文件、运行命令。
- agent loop：支持 thinking/progress 消息、工具调用、工具结果、流式回复和显式 loop end。
- character 系统：同一角色共享角色配置、权限、技能范围和经验，并能在使用中更新经验。
- 多 agent/subsession：支持创建子 session、fork 子 session、父子 session 树、查看子 session、向子 session 发送消息。
- JSONL event log 形式的 session 系统，支持列表、加载、保存、删除、重命名、fork、第一条消息自动生成标题、compact boundary 和过期清理。
- 上下文管理：工具结果限长、microcompact、snip compact、context collapse、auto compact 和 Compact 按钮摘要。
- 权限系统：支持 allow/ask/deny、workspace 规则、regex 规则、session/character 级权限配置和沙箱命令控制。
- Todo 管理和工具并发执行。
- 通过 `SKILL.md` 发现本地 skill。
- 基础 MCP server 加载和 MCP 工具执行。
- 根据启动目录或 `WORKSPACE` 确定 workspace。

## 致谢

 [LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode)   

 [QUSETIONS/MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python)。
