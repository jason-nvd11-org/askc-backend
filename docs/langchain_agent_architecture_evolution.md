# LangChain Agent 架构演进深度解析：从 AgentExecutor 到 LangGraph 与 LCEL

在过去的一年中，LangChain 的 Agent 架构经历了翻天覆地的变化。对于许多开发者来说，从 `create_openai_tools_agent` 和 `AgentExecutor` 迁移到现代化的架构不仅是 API 的替换，更是思维模式的根本转变。

本文将以极其详尽的代码实例，深度剖析 LangChain Agent 架构的“旧范式”与“新范式”，揭示它们背后的设计哲学差异，并指导你如何在企业级生产环境中做出正确的架构选择。

## 1. 旧范式：黑盒化的 AgentExecutor

### 1.1 核心组件
在 LangChain v0.1.0 时代（及更早），构建一个具备 Tool Calling 能力的 Agent 通常涉及两个核心组件：
1.  **Factory Function (`create_openai_tools_agent`)**: 负责将 LLM、Prompt 和 Tools 组装成一个 `Runnable`（Agent 定义）。
2.  **Runtime Engine (`AgentExecutor`)**: 负责执行 Agent 的思考-行动循环（Think-Act Loop）。

### 1.2 代码解剖
让我们看一个典型的旧范式实现：

```python
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain import hub

# 1. 准备工具
tools = [get_weather_tool, search_tool]

# 2. 准备 Prompt (通常从 LangSmith Hub 拉取)
# 这个 Prompt 包含了复杂的 {agent_scratchpad} 占位符，用于存放中间步骤
prompt = hub.pull("hwchase17/openai-tools-agent")

# 3. 初始化 LLM
llm = ChatOpenAI(model="gpt-4-turbo", temperature=0)

# 4. 创建 Agent (The Brain)
# 这是一个 Runnable，输入是 {input, chat_history}，输出是 AgentAction 或 AgentFinish
agent = create_openai_tools_agent(llm, tools, prompt)

# 5. 创建 Executor (The Body)
# 这是一个循环控制器，负责解析 Agent 输出，执行工具，并将结果喂回给 Agent
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=5
)

# 6. 执行
result = agent_executor.invoke({"input": "What is the weather in SF?"})
```

### 1.3 致命缺陷
尽管 `AgentExecutor` 让 demo 跑得很快，但它在生产环境中暴露出了严重的问题：
*   **黑盒循环**: 你无法控制循环的内部逻辑。比如，你想在 Tool 执行前人工审批？很难。你想在 Tool 报错时执行特定的重试策略？非常麻烦。
*   **流式输出困难**: `AgentExecutor` 的流式输出粒度非常粗（Step 级别），很难实现 Token 级别的平滑流式体验，尤其是在前端需要区分“思考内容”和“最终答案”时。
*   **Prompt 强耦合**: 它严重依赖特定的 Prompt 结构（如 `agent_scratchpad`），导致切换模型或自定义 Prompt 变得异常痛苦。

## 2. 新范式：LCEL 与 LangGraph 的崛起

为了解决上述问题，LangChain 推出了两套互相配合的“新范式”：
1.  **LCEL (LangChain Expression Language)**: 提供底层的、原子的组件组合能力。
2.  **LangGraph**: 提供状态机（State Machine）级别的循环控制能力。

### 2.1 方案 A：轻量级 LCEL (`bind_tools`)
如果你只需要一个简单的 Tool Calling 流程，不需要复杂的循环，LCEL 是最佳选择。

**取代对象**: `create_openai_tools_agent`

```python
# 新范式：纯 LCEL 实现
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

# 1. 绑定工具 (Native Tool Calling)
# 不再需要复杂的 create_xxx_agent，直接用 bind_tools
llm = ChatOpenAI(model="gpt-4-turbo")
llm_with_tools = llm.bind_tools(tools)

# 2. 定义简单的 Prompt
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("user", "{input}"),
])

# 3. 组合链
chain = prompt | llm_with_tools

# 4. 执行
# 结果是原生的 AIMessage，包含 .tool_calls 属性
msg = chain.invoke({"input": "What is the weather in SF?"})

if msg.tool_calls:
    # 开发者自己决定如何执行工具，拥有完全的控制权
    for tool_call in msg.tool_calls:
        print(f"Calling {tool_call['name']} with {tool_call['args']}")
```

**优势**:
*   **透明**: 没有黑盒，每一步都是标准的 Runnable。
*   **原生**: 直接利用模型原生的 Tool Calling API，不再需要 Prompt Hacking (`agent_scratchpad`)。

### 2.2 方案 B：LangGraph (`prebuilt.create_react_agent`)
如果你需要一个具备完整循环、记忆、流式输出能力的 Agent（即替代 `AgentExecutor`），LangGraph 是标准答案。

**取代对象**: `AgentExecutor`

```python
# 新范式：LangGraph 实现
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# 1. 初始化模型和工具
model = ChatOpenAI(model="gpt-4-turbo")
tools = [get_weather_tool]

# 2. 状态持久化 (可选)
checkpointer = MemorySaver()

# 3. 创建 Graph
# 这取代了 AgentExecutor，构建了一个标准的状态机图
app = create_react_agent(model, tools, checkpointer=checkpointer)

# 4. 执行 (支持细粒度流式)
inputs = {"messages": [("user", "What is the weather in SF?")]}
config = {"configurable": {"thread_id": "thread-1"}}

# stream_mode="values" 可以实时获取消息列表的更新
for event in app.stream(inputs, config=config, stream_mode="values"):
    event["messages"][-1].pretty_print()
```

**LangGraph 的核心优势**:
*   **状态机架构**: Agent 的逻辑被显式定义为图（Nodes & Edges）。你可以清晰地看到数据如何在 `Agent` 节点和 `Tools` 节点之间流转。
*   **完全可控的循环**: 你可以插入 `Human-in-the-loop`（人工介入）节点，可以在任何步骤暂停、修改状态、然后继续。
*   **原生持久化**: 内置 Checkpointer 机制，完美解决长对话的记忆问题。

## 3. 深度对比总结

| 特性 | 旧范式 (`AgentExecutor`) | 新范式 (`LangGraph` / `LCEL`) |
| :--- | :--- | :--- |
| **控制流** | 隐式、硬编码的 Python `while` 循环 | 显式的图结构 (Graph)，可定制 Edge |
| **Prompt** | 依赖 `agent_scratchpad` 等魔术变量 | 标准的消息列表 (`list[BaseMessage]`) |
| **流式能力** | 弱，只能流式输出 Callback 事件 | 强，支持 Token 级、消息级、更新级流式 |
| **调试难度** | 困难，内部状态不可见 | 容易，状态 (`State`) 是显式定义的字典 |
| **工具调用** | 依赖 OutputParser 解析文本 | 依赖模型原生的 `bind_tools` API |

## 4. 迁移建议

1.  **对于简单任务**: 如果你只是想让 LLM 调一个工具并返回结果，不要用 Agent。直接使用 **LCEL (`llm.bind_tools`)**。它更快、更便宜、更稳定。
2.  **对于复杂 Agent**: 立即迁移到 **LangGraph**。`AgentExecutor` 已经被标记为 Legacy，且在复杂场景下（如多 Agent 协作）几乎不可用。LangGraph 提供了构建生产级 Agent 所需的一切原语。

拥抱新范式，意味着你不再是框架的“使用者”，而是 Agent 逻辑的“编排者”。
