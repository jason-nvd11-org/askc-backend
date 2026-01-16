# Pytest 异步数据库测试实战：基于 AsyncMock 的无副作用打桩方案

在现代异步 Python 后端开发中（基于 FastAPI + SQLAlchemy Async），数据库操作往往是测试的痛点。集成测试需要真实的数据库环境，不仅速度慢，而且状态管理复杂。而在单元测试阶段，我们更希望隔离数据库依赖，专注于验证业务逻辑。

本文将深入探讨如何利用 `unittest.mock` 框架中的 `AsyncMock` 和 `patch`，优雅地对异步数据库层（DAO）进行打桩（Mocking），实现高效、无副作用的单元测试。

## 1. 痛点分析：为什么需要 Mock 数据库？

在一个典型的异步服务层函数中，代码通常长这样：

```python
async def stream_chat_response(request, agent, db):
    # 1. 数据库写操作
    await message_dao.create_message(db, message=user_msg)
    
    # 2. 数据库读操作
    history = await message_dao.get_messages_by_conversation(db, conversation_id=...)
    
    # 3. 业务逻辑...
```

如果要测试这个函数，传统做法是起一个 Docker 容器跑 Postgres，或者用 SQLite 内存库。但这带来了新问题：
- **执行慢**：I/O 操作拖慢测试套件。
- **环境依赖**：CI/CD 流水线需要额外的服务配置。
- **脏数据**：测试用例之间可能相互污染。

使用 Mock 技术，我们可以完全“欺骗”业务层，让它以为自己操作了数据库，实际上只是调用了我们预设的 Python 对象。

## 2. 核心武器：AsyncMock 与 patch

Python 3.8+ 的 `unittest.mock` 引入了 `AsyncMock`，专门处理 `async def` 函数。当被调用时，它无需 `await` 即可配置返回值，但必须被 `await` 才能执行，完美契合协程测试。

### 2.1 两种打桩风格

在实践中，我们通常使用 `patch` 来替换目标模块中的 DAO 对象。这里推荐一种**“先定义，后注入”**的模式，相比传统的内联模式，这种写法可读性更强。

#### 模式对比

**传统模式（隐式）：**
```python
@patch("src.dao.message_dao.create_message", new_callable=AsyncMock)
async def test_func(mock_create):
    mock_create.return_value = {...}
    ...
```

**推荐模式（显式 Define-then-Patch）：**
```python
# 1. 显式定义 Mock 对象
mock_dao = MagicMock()
mock_dao.create_message = AsyncMock(return_value={"id": 1})

# 2. 注入 Mock 对象
with patch("src.services.chat_service.message_dao", new=mock_dao):
    # 测试逻辑...
```

显式模式的优势在于：当一个模块有多个 DAO 方法被调用时，我们可以构建一个完整的 Mock 结构体，模拟整个 Data Access Layer 的行为，而不是管理一堆零散的 `patch` 装饰器。

## 3. 实战案例：测试历史记录去重逻辑

假设我们有一个业务需求：**在加载聊天历史时，如果最后一条消息与当前用户输入重复，必须将其移除**。我们需要编写单元测试来验证这一逻辑，且**不能**连接真实数据库。

### 3.1 待测业务代码 (`src/services/chat_service.py`)

```python
async def stream_chat_response(request, agent, db):
    # ... 省略部分代码 ...
    
    # 调用 DAO 获取历史记录
    history_from_db = await message_dao.get_messages_by_conversation(
        db, conversation_id=request.conversation_id, limit=20
    )
    
    # 业务逻辑：倒序并去重
    history_from_db.reverse()
    if history_from_db and history_from_db[-1]['content'] == request.message:
        history_from_db.pop() # 关键去重逻辑
        
    # ... 后续处理 ...
```

### 3.2 编写测试代码

我们需要模拟 `message_dao.get_messages_by_conversation` 的返回值，构造一个包含重复数据的场景，然后断言服务层是否正确剔除了该数据。

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.chat_service import stream_chat_response

@pytest.mark.asyncio
async def test_history_deduplication():
    # --- 1. 准备阶段 (Arrange) ---
    
    # 构造输入数据
    user_input = "Hello World"
    request = MagicMock(message=user_input, conversation_id=123)
    
    # 构造 Mock 数据库返回数据（模拟包含重复项的场景）
    # 假设 DAO 返回的是按时间倒序排列（最新的在最前）
    mock_db_history = [
        {"role": "user", "content": user_input},       # 重复项（最新消息）
        {"role": "assistant", "content": "Hi there!"}, # 历史消息
    ]
    
    # --- 2. Mock 定义与注入 (Mocking) ---
    
    # 定义 Mock DAO 及其行为
    mock_dao = MagicMock()
    # 模拟写操作：什么都不做，直接返回
    mock_dao.create_message = AsyncMock() 
    # 模拟读操作：返回我们预设的列表
    mock_dao.get_messages_by_conversation = AsyncMock(return_value=mock_db_history)
    
    # 使用 'new' 参数将整个 message_dao 模块替换为我们的 mock 对象
    # 注意路径必须是“被测试代码中导入 DAO 的路径”，而非 DAO 定义的路径
    # 这里的 patch 实际上完成了一种“动态依赖注入”，我们在运行时替换了 service 模块中的 dao 引用
    with patch("src.services.chat_service.message_dao", new=mock_dao):
        
        # --- 3. 执行阶段 (Act) ---
        
        # 传入一个假的 mock_db session，因为在 patched 的 DAO 中根本不会用到它
        mock_db_session = AsyncMock()
        mock_agent = MagicMock()
        
        # 触发生成器执行
        async for _ in stream_chat_response(request, mock_agent, mock_db_session):
            pass

        # --- 4. 验证阶段 (Assert) ---
        
        # 验证 1: 确保数据库写操作被调用了（逻辑完整性）
        mock_dao.create_message.assert_called_once()
        
        # 验证 2: 检查传递给 Agent 的历史记录是否已去重
        # 获取传给 agent.astream 的 chat_history 参数
        call_args = mock_agent.astream.call_args
        passed_history = call_args.kwargs.get('chat_history', [])
        
        # 断言：原本有2条记录，去重后应剩1条
        assert len(passed_history) == 1
        assert passed_history[0].content == "Hi there!"
        # 确保那条 "Hello World" 被移除了
        assert "Hello World" not in [m.content for m in passed_history]
```

## 4. 技术要点总结

1.  **Mock 路径的选择**：这是最容易踩坑的地方。`patch` 的目标必须是**消费者（Consumer）**模块中的引用。
    *   ✅ 正确：`patch("src.services.chat_service.message_dao")`
    *   ❌ 错误：`patch("src.dao.message_dao")`
2.  **AsyncMock 的返回值**：对于返回 Awaitable 的函数，必须使用 `AsyncMock`。如果需要模拟返回值，直接设置 `return_value` 即可。
3.  **依赖注入 vs 全局 Patch**：在 FastAPI 中，通常通过 `Dependency Overrides` 解决数据库 Session 的注入。但在测试具体的 Service 方法逻辑时，直接 `patch` 掉 DAO 层是最轻量级的方案，因为它切断了所有数据库交互，将测试范围严格限定在 Service 层的纯逻辑上。

## 5. JUnit 用户特别提示：为什么不需要显式注入？

如果你习惯了 Java 生态（JUnit + Mockito），可能会疑惑：**“为什么我看不到类似 `new ChatService(mockDao)` 这样的注入代码？”**

这就是 Python 动态语言特性的魔力所在：

*   **Java (静态注入)**：在 Java 中，依赖关系通常在构造函数中确立。为了测试，你必须设计类以支持依赖注入（DI），然后在测试中显式将 Mock 对象传给被测对象。
*   **Python (Patching)**：Python 允许我们在运行时修改模块属性。`patch("src.services.chat_service.message_dao", ...)` 做的事情是：**找到 `chat_service.py` 模块，强制将其内部引用的 `message_dao` 变量指向我们的 Mock 对象**。

因此，被测函数 `stream_chat_response` 甚至不知道自己被“Hack”了。它照常调用 `message_dao`，却不知道这个 `dao` 已经被悄悄替换成了我们的 Mock。这种**Monkey Patching**机制使得我们可以在不修改原有代码结构（不需要为了测试而专门重构代码）的情况下，轻松隔离依赖。

通过这种方式，我们构建了一个纯粹在内存中运行、毫秒级响应、且完全确定性的单元测试，为复杂的异步业务逻辑提供了坚实的质量保障。
