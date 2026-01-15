# 深入分析：Pytest异步测试中的数据库会话事件循环问题

## 问题概述

在运行异步数据库测试时，我们遇到了一个典型的 "Event loop is closed" 或 "got Future attached to a different loop" 错误。具体表现为：

- 第一个异步测试用例成功通过
- 第二个及后续异步测试用例立即失败，抛出 `RuntimeError: Task got Future attached to a different loop`
- 错误发生在数据库连接尝试建立时，具体在 `asyncpg` 协议层

## 错误堆栈分析

```python
RuntimeError: Task <Task pending name='Task-3' coro=<test_create_and_get_conversations() running at /workspace/test/dao/test_conversation_dao.py:39> cb=[_run_until_complete_cb() at /usr/lib/python3.12/asyncio/base_events.py:182]> got Future <Future pending cb=[Protocol._on_waiter_completed()]> attached to a different loop

asyncpg/protocol/protocol.pyx:374: RuntimeError
```

错误堆栈显示问题发生在 `asyncpg` 协议层，表明数据库连接尝试使用不同的事件循环。

## 技术背景

### 1. Pytest-Asyncio 的事件循环管理

`pytest-asyncio` 是 Pytest 的异步测试插件，它管理异步测试的事件循环。关键配置选项：

- `asyncio_mode`: 控制异步测试的模式（auto/strict）
- `asyncio_default_fixture_loop_scope`: 控制事件循环的作用域（function/session）

### 2. SQLAlchemy 异步引擎缓存

在我们的代码中，数据库引擎使用 `@lru_cache` 装饰器进行缓存：

```python
# src/configs/db.py
from functools import lru_cache

@lru_cache()
def get_async_engine():
    """
    Returns a cached async engine instance.
    The engine is created on the first call and reused on subsequent calls
    within the same event loop.
    """
    logger.info("Creating new async engine instance.")
    return create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        echo=False,
    )
```

### 3. AsyncSessionFactory 的模块级初始化

`AsyncSessionFactory` 在模块导入时就被创建：

```python
# Create a sessionmaker for creating AsyncSession instances
# The bind is deferred until the engine is created.
AsyncSessionFactory = async_sessionmaker(
    bind=get_async_engine(),
    expire_on_commit=False,
)
```

## 根本原因分析

### 问题发生序列

1. **测试 1 开始**：
   - Pytest 创建 **Loop A**
   - `get_async_engine()` 被调用，创建 **Engine 1** 并绑定到 **Loop A**
   - `AsyncSessionFactory` 绑定到 **Engine 1**
   - 测试 1 结束，Pytest 可能关闭 **Loop A**（取决于配置）

2. **测试 2 开始**：
   - Pytest 创建 **Loop B**
   - 测试代码尝试使用数据库连接
   - 由于 `get_async_engine()` 有缓存，返回 **Engine 1**
   - **Engine 1** 仍然绑定在已关闭的 **Loop A** 上
   - 当 SQLAlchemy 尝试使用 **Engine 1** 在 **Loop B** 中连接数据库时，失败

### 关键冲突点

1. **缓存与事件循环生命周期的冲突**：
   - `@lru_cache` 导致引擎在第一次创建后被缓存
   - 缓存的引擎绑定到创建时的事件循环
   - 当新测试使用新事件循环时，缓存的引擎仍然绑定到旧循环

2. **模块级初始化与测试隔离的冲突**：
   - `AsyncSessionFactory` 在模块导入时初始化
   - 即使清除引擎缓存，`AsyncSessionFactory` 仍然引用旧的引擎实例

3. **Pytest 配置与应用程序设计的冲突**：
   - 默认的 `asyncio_default_fixture_loop_scope = session` 意味着所有测试共享同一个事件循环
   - 但某些情况下，pytest-asyncio 可能为每个测试创建新的事件循环

## 排查过程

### 第一步：分析错误模式

首先确认错误是否可重现：
```bash
pytest test/dao/test_conversation_dao.py::test_create_and_get_conversations -v
```

错误确实发生，且堆栈指向数据库连接层。

### 第二步：检查相关配置

1. **检查 pytest.ini**：
```ini
[pytest]
addopts = -s
pythonpath = .
asyncio_mode = auto
asyncio_default_fixture_loop_scope = session
```

2. **检查 conftest.py**：
```python
@pytest.fixture(scope="session")
def event_loop():
    """
    Creates an instance of the default event loop for the session.
    This fixes the 'Event loop is closed' error in asyncpg during tests.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    
    yield loop
    loop.close()
```

### 第三步：分析数据库配置

检查 `src/configs/db.py`，发现关键问题：
- `get_async_engine` 使用 `@lru_cache`
- `AsyncSessionFactory` 在模块级别创建

### 第四步：验证假设

创建简单测试验证事件循环问题：
```python
# test/test_simple_async.py
import pytest
import asyncio

@pytest.mark.asyncio
async def test_simple_async():
    await asyncio.sleep(0.1)
    assert True

@pytest.mark.asyncio  
async def test_another_simple_async():
    await asyncio.sleep(0.1)
    assert True
```

简单测试通过，确认问题特定于数据库操作。

## 解决方案

### 方案一：清除引擎缓存（初步尝试）

在测试 fixture 中清除 `get_async_engine` 缓存：

```python
# test/services/test_chat_service_e2e.py
@pytest.fixture
async def db_session():
    """Fixture to provide a real database session."""
    # Clear the cache to ensure we get a new engine bound to the current event loop
    get_async_engine.cache_clear()
    
    session_generator = get_db_session()
    session = await anext(session_generator)
    try:
        yield session
    finally:
        await session.close()
```

**结果**：部分缓解，但问题仍然存在，因为 `AsyncSessionFactory` 仍然引用旧的引擎。

### 方案二：直接创建引擎（改进方案）

绕过 `get_async_engine` 和 `AsyncSessionFactory`，直接创建引擎：

```python
# test/services/test_chat_service_e2e.py
@pytest.fixture
async def db_session():
    """Fixture to provide a real database session."""
    # Clear the cache to ensure we get a new engine bound to the current event loop
    get_async_engine.cache_clear()
    
    # Create a new engine bound to the current event loop
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
    
    # Create a new session factory with the new engine
    AsyncSessionFactory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            await session.close()
    
    # Dispose the engine after use
    await engine.dispose()
```

### 方案三：调整 Pytest 配置（关键修复）

修改 `pytest.ini`，改变事件循环的作用域：

```ini
[pytest]
addopts = -s
pythonpath = .
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function  # 从 session 改为 function
```

### 方案四：全局缓存管理（最终方案）

在 `conftest.py` 中添加全局缓存管理 fixture：

```python
# test/conftest.py
@pytest.fixture(autouse=True)
async def reset_db_cache():
    """
    Fixture to reset the database engine cache before each test.
    This fixes the "Event loop is closed" error when running multiple async tests.
    """
    # Clear the cache of get_async_engine
    import src.configs.db
    if hasattr(src.configs.db.get_async_engine, 'cache_clear'):
        src.configs.db.get_async_engine.cache_clear()
    
    # Also try to reinitialize the AsyncSessionFactory
    # by forcing a re-import of the module
    importlib.reload(src.configs.db)
    
    yield
```

## 完整修复代码

### 1. pytest.ini 修改
```ini
[pytest]
addopts = -s
pythonpath = .
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function  # 关键修改
```

### 2. conftest.py 更新
```python
import pytest
import asyncio
import sys
import importlib
from loguru import logger

@pytest.fixture(scope="session")
def event_loop():
    """
    Creates an instance of the default event loop for the session.
    This fixture is required by pytest-asyncio to avoid event loop issues.
    """
    # Try to get the running loop first
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    
    yield loop
    
    # Don't close the loop here - let pytest-asyncio handle it
    # Closing the loop can cause "Event loop is closed" errors

@pytest.fixture(scope="session", autouse=True)
def configure_logging():
    """
    Configures Loguru logger for pytest execution.
    """
    logger.remove()
    logger.add(sys.stdout, level="DEBUG")

@pytest.fixture(autouse=True)
async def reset_db_cache():
    """
    Fixture to reset the database engine cache before each test.
    This fixes the "Event loop is closed" error when running multiple async tests.
    """
    # Clear the cache of get_async_engine
    import src.configs.db
    if hasattr(src.configs.db.get_async_engine, 'cache_clear'):
        src.configs.db.get_async_engine.cache_clear()
    
    # Also try to reinitialize the AsyncSessionFactory
    # by forcing a re-import of the module
    importlib.reload(src.configs.db)
    
    yield
```

### 3. 测试文件优化
```python
# test/services/test_chat_service_e2e.py
@pytest.fixture
async def db_session():
    """Fixture to provide a real database session."""
    # Clear the cache to ensure we get a new engine bound to the current event loop
    get_async_engine.cache_clear()
    
    # Create a new engine bound to the current event loop
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
    
    # Create a new session factory with the new engine
    AsyncSessionFactory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            await session.close()
    
    # Dispose the engine after use
    await engine.dispose()
```

## 修复原理总结

### 1. 事件循环隔离
将 `asyncio_default_fixture_loop_scope` 从 `session` 改为 `function` 确保：
- 每个测试函数获得独立的事件循环
- 避免测试间的事件循环污染
- 符合测试隔离的最佳实践

### 2. 缓存一致性
通过 `reset_db_cache` fixture 确保：
- 每个测试前清除引擎缓存
- 重新加载数据库模块，刷新 `AsyncSessionFactory`
- 保证引擎绑定到当前测试的事件循环

### 3. 资源管理
在测试 fixture 中：
- 显式创建和销毁引擎
- 确保引擎与当前事件循环匹配
- 避免资源泄漏

## 测试验证

修复后运行测试验证：

```bash
# 验证数据库DAO测试
pytest test/dao/test_conversation_dao.py::test_create_and_get_conversations -v

# 验证端到端聊天服务测试
pytest test/services/test_chat_service_e2e.py::test_stream_chat_response_gemini_e2e -v
pytest test/services/test_chat_service_e2e.py::test_stream_chat_response_deepseek_e2e -v
```

所有测试均通过，不再出现事件循环错误。

## 经验教训

### 1. 异步测试设计原则
- 避免在模块级别初始化有状态的对象
- 确保资源生命周期与测试生命周期匹配
- 使用适当的测试隔离策略

### 2. SQLAlchemy 最佳实践
- 谨慎使用缓存，特别是在测试环境中
- 考虑测试专用的数据库配置
- 确保引擎和会话的事件循环一致性

### 3. Pytest 配置管理
- 理解 `pytest-asyncio` 的事件循环管理策略
- 根据测试需求调整作用域配置
- 使用 fixture 进行资源管理和清理

## 真正原因深度分析

### 核心问题：事件循环绑定与缓存的生命周期不匹配

问题的本质是 **资源生命周期管理** 的冲突：

1. **SQLAlchemy 引擎的缓存策略**：
   ```python
   @lru_cache()
   def get_async_engine():
       # 引擎创建时绑定到当前事件循环
       return create_async_engine(DATABASE_URL)
   ```
   - `@lru_cache` 导致引擎在第一次调用后被缓存
   - 引擎在创建时绑定到 **创建时的事件循环**
   - 缓存机制假设事件循环在整个应用生命周期中保持不变

2. **Pytest-Asyncio 的测试执行模型**：
   - 默认配置 (`asyncio_default_fixture_loop_scope = session`)：所有测试共享同一个事件循环
   - 但实际行为可能因版本、配置或测试结构而变化
   - 某些情况下，pytest-asyncio 可能为每个测试创建新的事件循环

3. **冲突发生时机**：
   ```
   测试1开始 → 创建Loop A → 创建Engine 1 → 绑定到Loop A → 测试1结束
   测试2开始 → 创建Loop B → 获取缓存的Engine 1 → Engine 1仍绑定到Loop A → 冲突！
   ```

### 根本原因总结

**缓存的对象（引擎）与其依赖的资源（事件循环）具有不同的生命周期**：
- 引擎被缓存，生命周期跨越多个测试
- 事件循环可能为每个测试重新创建，生命周期仅限于单个测试
- 当缓存的引擎尝试在"错误"的事件循环中操作时，发生 `RuntimeError: got Future attached to a different loop`

## 最佳实践指南

### 1. 异步测试配置最佳实践

#### ✅ 推荐配置
```ini
# pytest.ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function  # 关键：每个测试独立的事件循环
```

#### ❌ 避免的配置
```ini
asyncio_default_fixture_loop_scope = session  # 可能导致事件循环冲突
```

#### 📝 配置说明
- `function` 作用域：每个测试函数获得独立的事件循环，提供最好的隔离性
- `session` 作用域：所有测试共享事件循环，性能更好但容易出问题
- 对于数据库测试，优先选择 `function` 作用域以确保稳定性

### 2. SQLAlchemy 异步引擎管理最佳实践

#### ✅ 推荐模式：测试专用引擎
```python
# 在测试fixture中创建专用引擎
@pytest.fixture
async def db_session():
    # 清除可能存在的缓存
    get_async_engine.cache_clear()
    
    # 创建新的引擎，确保绑定到当前事件循环
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    
    # 创建会话工厂
    AsyncSessionFactory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    
    async with AsyncSessionFactory() as session:
        yield session
    
    # 测试结束后清理
    await engine.dispose()
```

#### ❌ 避免的模式：直接使用缓存的引擎
```python
# 不要这样使用
session = AsyncSessionFactory()  # 可能使用绑定到旧事件循环的引擎
```

#### 📝 引擎管理原则
1. **测试隔离**：每个测试应该使用独立的引擎实例
2. **生命周期管理**：引擎的创建和销毁应该与测试的生命周期匹配
3. **事件循环一致性**：确保引擎绑定到当前测试的事件循环

### 3. 测试Fixture设计最佳实践

#### ✅ 推荐：显式资源管理
```python
@pytest.fixture
async def db_session():
    """提供数据库会话，确保事件循环一致性"""
    # 1. 清理旧状态
    get_async_engine.cache_clear()
    
    # 2. 创建新资源
    engine = create_async_engine(DATABASE_URL)
    
    # 3. 使用资源
    async with async_sessionmaker(bind=engine)() as session:
        yield session
    
    # 4. 清理资源
    await engine.dispose()
```

#### ✅ 推荐：使用上下文管理器
```python
@pytest.fixture
async def db_engine():
    """提供数据库引擎，自动清理"""
    engine = create_async_engine(DATABASE_URL)
    try:
        yield engine
    finally:
        await engine.dispose()

@pytest.fixture  
async def db_session(db_engine):
    """基于引擎创建会话"""
    async with async_sessionmaker(bind=db_engine)() as session:
        yield session
```

### 4. 生产代码与测试代码的分离

#### ✅ 推荐：为测试环境提供专用配置
```python
# src/configs/db.py
def get_async_engine(use_cache: bool = True):
    """获取异步引擎，支持禁用缓存（用于测试）"""
    if not use_cache:
        return create_async_engine(DATABASE_URL)
    
    @lru_cache()
    def _cached_engine():
        return create_async_engine(DATABASE_URL)
    
    return _cached_engine()

# 测试代码
@pytest.fixture
async def db_session():
    # 使用无缓存的引擎
    engine = get_async_engine(use_cache=False)
    # ...
```

#### ✅ 推荐：环境感知的缓存策略
```python
# src/configs/db.py
import os

def get_async_engine():
    """根据环境决定是否使用缓存"""
    # 测试环境禁用缓存
    if os.getenv("PYTEST_CURRENT_TEST"):
        return create_async_engine(DATABASE_URL)
    
    # 生产环境使用缓存
    @lru_cache()
    def _cached_engine():
        return create_async_engine(DATABASE_URL)
    
    return _cached_engine()
```

### 5. 调试与诊断最佳实践

#### ✅ 推荐：添加诊断日志
```python
@pytest.fixture(autouse=True)
async def event_loop_debug():
    """诊断事件循环问题"""
    import asyncio
    loop = asyncio.get_event_loop()
    print(f"Test using event loop: {id(loop)}")
    yield
```

#### ✅ 推荐：验证事件循环一致性
```python
def assert_event_loop_consistency(engine):
    """验证引擎绑定的事件循环与当前事件循环一致"""
    import asyncio
    current_loop = asyncio.get_event_loop()
    # 检查引擎是否绑定到当前循环
    # （具体实现取决于SQLAlchemy版本）
```

## 经验教训总结

### 1. 异步资源管理的黄金法则
**"谁创建，谁管理，谁清理"**
- 创建资源的代码应该负责管理其生命周期
- 测试代码应该创建自己专用的资源
- 避免跨测试共享有状态的资源

### 2. 缓存使用的注意事项
- 缓存适合无状态或状态不变的对象
- 避免缓存绑定到特定上下文（如事件循环）的对象
- 在测试环境中考虑禁用或限制缓存

### 3. 测试隔离的重要性
- 每个测试应该尽可能独立
- 避免测试间的隐式依赖
- 使用适当的fixture作用域（function > class > module > session）

### 4. 配置明确化
- 显式配置优于隐式行为
- 文档化配置的假设和影响
- 为不同环境提供适当的默认配置

## 结论

数据库会话事件循环问题的根本原因是 **缓存对象的生命周期与其依赖资源（事件循环）的生命周期不匹配**。通过以下措施可以彻底解决和预防此类问题：

1. **配置层面**：使用 `asyncio_default_fixture_loop_scope = function` 确保测试隔离
2. **代码层面**：在测试中创建专用的数据库引擎，避免使用缓存的引擎
3. **架构层面**：分离生产代码和测试代码的资源管理策略

遵循这些最佳实践，可以构建稳定、可靠的异步测试套件，避免事件循环相关的难以调试的问题。

**核心洞察**：在异步编程中，资源的生命周期管理比同步编程更加关键。必须确保每个异步资源（如数据库引擎）与其执行上下文（事件循环）的生命周期保持一致。
