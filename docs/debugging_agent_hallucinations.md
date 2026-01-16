# 深入 ReAct Agent 的灵魂拷问：从幻觉到精准执行的调试实录

在构建基于 LLM 的智能体（Agent）系统时，我们经常会遇到“幻觉”（Hallucination）和“指令遵循失效”的问题。特别是在多轮对话和分步提问的复杂场景下，这些问题会变得尤为棘手。

本文将复盘一次真实的生产环境故障排查过程。我们遇到了一个典型的 **Main Agent** 在分步提问场景下的重复提问和幻觉问题。我们将详细拆解如何利用 **LangSmith Trace** 定位问题根源，并通过精细的代码修复（System Prompt 增强与历史记录去重）彻底解决它。

## 1. 故障现场：幽灵般的重复与幻觉

### 场景描述
用户试图通过对话式 UI 让 Agent 列出 GitHub 仓库。交互过程如下：

**Turn 1:**
*   **User**: "帮我列出 github repo list"
*   **Agent**: (正确调用 `delegate_to_github`) "好的，请问您想看哪个GitHub用户或组织的仓库列表？"

**Turn 2:**
*   **User**: "nvd11"
*   **Agent (故障)**: 
    *   首先输出了：`[System: Handing off to GitHub Agent...]` (这是我们预期的)
    *   **紧接着**，居然自己编造了一段回复："好的，正在为您查找... 以下是 nvd11 的部分公开仓库... nvd11/deeplearning..." (这是纯粹的幻觉！)
    *   **最后**，才真正调用了工具，并输出了真实结果。

**问题总结：**
1.  **重复执行**：Agent 似乎被触发了两次，一次产生了幻觉，一次执行了真实逻辑。
2.  **幻觉内容**：在没有调用工具的情况下，Agent 凭空捏造了仓库列表。
3.  **参数异常**：在某些 Trace 中，我们甚至观察到了 `nvd11nvd11` 这种参数重复拼接的怪异现象。

## 2. 抽丝剥茧：利用 LangSmith Trace 定位根源

面对这种非确定性的 LLM 行为，传统的日志调试往往力不从心。我们需要“打开 LLM 的黑盒”。

### 2.1 部署 LangSmith 探针
首先，我们在 GKE 环境中注入了 LangSmith 的环境变量，确保线上的每一次思考（Reasoning）都能被捕获。

```yaml
# cloudbuild-helm-dev.yaml
--from-literal=LANGCHAIN_TRACING_V2="true" \
--from-literal=LANGCHAIN_PROJECT="askc-backend-dev" \
--from-literal=LANGCHAIN_ENDPOINT="https://eu.api.smith.langchain.com" \
```

### 2.2 Trace 深度分析
通过 `LangSmithClient` 拉取最新的 Trace，我们看到了令人震惊的真相。

#### 罪魁祸首一：历史记录的“回声”
我们在 Trace 的 `Inputs` 中发现，传给 LLM 的 `chat_history` 竟然包含了**两条**内容完全一样的 User Message。

*   **原因**：`ChatService` 的逻辑是“先存后读”。
    1.  User 发送 "nvd11"。
    2.  Service 将 "nvd11" 写入数据库。
    3.  Service 从数据库读取最近历史 -> 此时历史里已经包含了最新的 "nvd11"。
    4.  Service 将历史传给 Agent。
    5.  Agent 内部逻辑（`_astream_impl`）又习惯性地把当前 User Input ("nvd11") append 到了由历史构建的 `messages` 列表末尾。

    **结果**：LLM 看到了 `[Human: nvd11, Human: nvd11]`。这导致 LLM 困惑，进而导致了参数提取的错误（`nvd11nvd11`）。

#### 罪魁祸首二：Main Agent 的“抢答”
在 Turn 2 中，Main Agent 接收到了 "nvd11"。虽然它的 System Prompt 定义了 `delegate_to_github` 工具，但 Gemini 2.5 Pro 模型过于“聪明”和“热心”。

它在输出 Tool Call JSON 的同时，**顺便**把 Tool Call 之后可能发生的事情（Based on 它的训练数据）也预测并输出了。这就导致了用户先看到一段“瞎编”的回复，然后才看到真正的工具执行结果。

## 3. 雷霆手段：修复方案

定位了病灶，修复就变得有的放矢。

### 3.1 斩断回声：历史记录去重
我们在 `src/services/chat_service.py` 中引入了防御性编程逻辑。在读取数据库历史后，强制检查最后一条消息。如果它就是当前正在处理的消息，立即移除。

```python
    # 2. Load conversation history from DB
    history_from_db = await message_dao.get_messages_by_conversation(...)
    history_from_db.reverse()

    # 关键修复：Remove the last message if it matches the current request
    if history_from_db and history_from_db[-1]['content'] == request.message:
        history_from_db.pop()
```

为此，我们还编写了基于 `AsyncMock` 的单元测试 `test/services/test_chat_service_history.py`，确保这种去重逻辑在各种边界条件下都能正确工作。

### 3.2 禁言咒：System Prompt 强化
针对 Main Agent 的抢答行为，我们在 `src/agents/main_agent.py` 中实施了最严厉的 Prompt 工程（Prompt Engineering）。

**Before:**
```text
You are a helpful assistant... Use tools...
```

**After (Dynamic & Strict):**
```python
        return f"""...
CRITICAL INSTRUCTIONS:
1. You MUST ONLY use the tools listed above: {tool_names_str}.
2. Do NOT invent or hallucinate new tools.
3. Select the most appropriate tool based on the user's request.
4. Pass the user's ORIGINAL request as the argument to the tool.

To use a tool, output a JSON blob...
If you can answer directly, just reply.
"""
```
同时，我们将 Tool 的描述修改为 **MANDATORY**（强制性），明确告诉模型：“凡是涉及 GitHub 的，必须且只能用这个工具，不准自己瞎编。”

### 3.3 代码级拦截：JSON 输出抑制
为了彻底杜绝“JSON + 废话”的混合输出，我们在代码层面做了一层拦截。

在 `_astream_impl` 中，我们监测流式输出。一旦检测到 ` ```json ` 标记，我们不仅开始收集 JSON，而且**停止向用户 Yield 后续的文本内容**。取而代之的是，我们在成功解析 JSON 后，手动向用户发送一条系统级的友好提示：

```python
            # Manually yield a friendly message instead of the raw JSON
            if tool_name == "delegate_to_github":
                 yield AIMessageChunk(content=f"\n[System: I will ask the GitHub Agent to help with: '{query}']\n")
```

这一改动极大地提升了用户体验，让 Agent 的思考过程既透明又受控。

## 4. 总结

这次排查过程完美展示了 **可观测性 (Observability)** 在 LLM 应用开发中的核心地位。没有 LangSmith 的 Trace，我们将很难发现“双重历史记录”这种隐蔽的逻辑 Bug，也很难理解模型“先幻觉后真实”的行为模式。

通过 **Data-Driven Debugging**，结合 **Prompt Engineering** 和 **Defensive Coding**，我们成功将一个“偶尔发疯”的 Agent 驯化成了精准执行指令的专家。
