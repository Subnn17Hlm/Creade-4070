import argparse
import asyncio
import csv
import json
import threading
import traceback
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, AsyncIterable, AsyncGenerator, Optional
import cozeloop
import uvicorn
import time
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from coze_coding_utils.runtime_ctx.context import new_context, Context
from coze_coding_utils.helper import graph_helper
from coze_coding_utils.log.node_log import LOG_FILE
from coze_coding_utils.log.write_log import setup_logging, request_context
from coze_coding_utils.log.config import LOG_LEVEL
from coze_coding_utils.error.classifier import ErrorClassifier, classify_error
from coze_coding_utils.helper.stream_runner import AgentStreamRunner, WorkflowStreamRunner,agent_stream_handler,workflow_stream_handler, RunOpt
from storage.database.db import get_session, get_engine
from storage.memory.memory_saver import get_memory_saver
from storage.database.shared.model import Base
from graphs.run_trace_persistence import (
    register_run, update_run_status, get_trace, get_latest_run_by_script,
    persist_run_trace
)
from coze_coding_utils.async_tasks import (
    AsyncTaskRuntime,
    AsyncTaskStorageError,
    extract_biz_context,
    parse_deadline_sec,
)
from coze_coding_utils.async_tasks import config as async_task_config
from coze_coding_utils.async_tasks.headers import HEADER_X_RUN_ID as _ASYNC_HEADER_X_RUN_ID
from coze_coding_utils.runtime_ctx.context import new_context as _new_async_ctx
from sqlalchemy import event, select

setup_logging(
    log_file=LOG_FILE,
    max_bytes=100 * 1024 * 1024, # 100MB
    backup_count=5,
    log_level=LOG_LEVEL,
    use_json_format=True,
    console_output=True
)

logger = logging.getLogger(__name__)
from coze_coding_utils.helper.agent_helper import to_stream_input, to_client_message
from coze_coding_utils.openai.handler import OpenAIChatHandler
from coze_coding_utils.log.parser import LangGraphParser
from coze_coding_utils.log.err_trace import extract_core_stack
from coze_coding_utils.log.loop_trace import init_run_config, init_agent_config


# 超时配置常量
TIMEOUT_SECONDS = 900  # 15分钟

class GraphService:
    def __init__(self):
        # 用于跟踪正在运行的任务（使用asyncio.Task）
        self.running_tasks: Dict[str, asyncio.Task] = {}
        # 错误分类器
        self.error_classifier = ErrorClassifier()
        # stream runner
        self._agent_stream_runner = AgentStreamRunner()
        self._workflow_stream_runner = WorkflowStreamRunner()
        self._graph = None
        self._graph_lock = threading.Lock()

    def set_graph(self, graph) -> None:
        """Inject the compiled graph used by sync endpoints. Called once from
        lifespan with a no-checkpointer build, so /run /stream_run /node_run
        never hit the checkpoint DB."""
        self._graph = graph

    def _get_graph(self, ctx=Context):
        if self._graph is not None:
            return self._graph
        with self._graph_lock:
            if self._graph is not None:
                return self._graph
            if graph_helper.is_agent_proj():
                self._graph = graph_helper.get_agent_instance("agents.agent", ctx)
            else:
                self._graph = graph_helper.get_graph_instance("graphs.graph")
            return self._graph

    @staticmethod
    def _sse_event(data: Any, event_id: Any = None) -> str:
        id_line = f"id: {event_id}\n" if event_id else ""
        return f"{id_line}event: message\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    def _get_stream_runner(self):
        if graph_helper.is_agent_proj():
            return self._agent_stream_runner
        else:
            return self._workflow_stream_runner

    # 流式运行（原始迭代器）：本地调用使用
    def stream(self, payload: Dict[str, Any], run_config: RunnableConfig, ctx=Context) -> Iterable[Any]:
        graph = self._get_graph(ctx)
        stream_runner = self._get_stream_runner()
        for chunk in stream_runner.stream(payload, graph, run_config, ctx):
            yield chunk

    # 同步运行：本地/HTTP 通用
    async def run(self, payload: Dict[str, Any], ctx=None) -> Dict[str, Any]:
        if ctx is None:
            ctx = new_context("run")

        run_id = ctx.run_id
        logger.info(f"Starting run with run_id: {run_id}")

        try:
            graph = self._get_graph(ctx)
            # custom tracer
            run_config = init_run_config(graph, ctx)
            run_config.setdefault("configurable", {})["thread_id"] = ctx.run_id

            # 直接调用，LangGraph会在当前任务上下文中执行
            # 如果当前任务被取消，LangGraph的执行也会被取消
            return await graph.ainvoke(payload, config=run_config, context=ctx)

        except asyncio.CancelledError:
            logger.info(f"Run {run_id} was cancelled")
            return {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        except Exception as e:
            # 使用错误分类器分类错误
            err = self.error_classifier.classify(e, {"node_name": "run", "run_id": run_id})
            # 记录详细的错误信息和堆栈跟踪
            logger.error(
                f"Error in GraphService.run: [{err.code}] {err.message}\n"
                f"Category: {err.category.name}\n"
                f"Traceback:\n{extract_core_stack()}"
            )
            # 保留原始异常堆栈，便于上层返回真正的报错位置
            raise
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)

    # 流式运行（SSE 格式化）：HTTP 路由使用
    async def stream_sse(self, payload: Dict[str, Any], ctx=None, run_opt: Optional[RunOpt] = None) -> AsyncGenerator[str, None]:
        if ctx is None:
            ctx = new_context(method="stream_sse")
        if run_opt is None:
            run_opt = RunOpt()

        run_id = ctx.run_id
        logger.info(f"Starting stream with run_id: {run_id}")
        graph = self._get_graph(ctx)
        if graph_helper.is_agent_proj():
            run_config = init_agent_config(graph, ctx)
        else:
            run_config = init_run_config(graph, ctx)  # vibeflow

        is_workflow = not graph_helper.is_agent_proj()

        try:
            async for chunk in self.astream(payload, graph, run_config=run_config, ctx=ctx, run_opt=run_opt):
                if is_workflow and isinstance(chunk, tuple):
                    event_id, data = chunk
                    yield self._sse_event(data, event_id)
                else:
                    yield self._sse_event(chunk)
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)
            cozeloop.flush()

    # 取消执行 - 使用asyncio的标准方式
    def cancel_run(self, run_id: str, ctx: Optional[Context] = None) -> Dict[str, Any]:
        """
        取消指定run_id的执行

        使用asyncio.Task.cancel()来取消任务,这是标准的Python异步取消机制。
        LangGraph会在节点之间检查CancelledError,实现优雅的取消。
        """
        logger.info(f"Attempting to cancel run_id: {run_id}")

        # 查找对应的任务
        if run_id in self.running_tasks:
            task = self.running_tasks[run_id]
            if not task.done():
                # 使用asyncio的标准取消机制
                # 这会在下一个await点抛出CancelledError
                task.cancel()
                logger.info(f"Cancellation requested for run_id: {run_id}")
                return {
                    "status": "success",
                    "run_id": run_id,
                    "message": "Cancellation signal sent, task will be cancelled at next await point"
                }
            else:
                logger.info(f"Task already completed for run_id: {run_id}")
                return {
                    "status": "already_completed",
                    "run_id": run_id,
                    "message": "Task has already completed"
                }
        else:
            logger.warning(f"No active task found for run_id: {run_id}")
            return {
                "status": "not_found",
                "run_id": run_id,
                "message": "No active task found with this run_id. Task may have already completed or run_id is invalid."
            }

    # 运行指定节点：本地/HTTP 通用
    async def run_node(self, node_id: str, payload: Dict[str, Any], ctx=None) -> Any:
        if ctx is None or Context.run_id == "":
            ctx = new_context(method="node_run")

        _graph = self._get_graph()
        node_func, input_cls, output_cls = graph_helper.get_graph_node_func_with_inout(_graph.get_graph(), node_id)
        if node_func is None or input_cls is None:
            raise KeyError(f"node_id '{node_id}' not found")

        parser = LangGraphParser(_graph)
        metadata = parser.get_node_metadata(node_id) or {}

        _g = StateGraph(input_cls, input_schema=input_cls, output_schema=output_cls)
        _g.add_node("sn", node_func, metadata=metadata)
        _g.set_entry_point("sn")
        _g.add_edge("sn", END)
        _graph = _g.compile()

        run_config = init_run_config(_graph, ctx)
        return await _graph.ainvoke(payload, config=run_config)

    def graph_inout_schema(self) -> Any:
        if graph_helper.is_agent_proj():
            return {"input_schema": {}, "output_schema": {}}
        builder = getattr(self._get_graph(), 'builder', None)
        if builder is not None:
            input_cls = getattr(builder, 'input_schema', None) or self.graph.get_input_schema()
            output_cls = getattr(builder, 'output_schema', None) or self.graph.get_output_schema()
        else:
            logger.warning(f"No builder input schema found for graph_inout_schema, using graph input schema instead")
            input_cls = self.graph.get_input_schema()
            output_cls = self.graph.get_output_schema()

        return {
            "input_schema": input_cls.model_json_schema(), 
            "output_schema": output_cls.model_json_schema(),
            "code":0,
            "msg":""
        }

    async def astream(self, payload: Dict[str, Any], graph: CompiledStateGraph, run_config: RunnableConfig, ctx=Context, run_opt: Optional[RunOpt] = None) -> AsyncIterable[Any]:
        stream_runner = self._get_stream_runner()
        async for chunk in stream_runner.astream(payload, graph, run_config, ctx, run_opt):
            yield chunk


service = GraphService()

async_runtime: Optional[AsyncTaskRuntime] = None
async_graph: Optional[CompiledStateGraph] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global async_graph, async_runtime
    
    # imageio-ffmpeg 导入检查
    try:
        import imageio_ffmpeg
        imageio_ffmpeg_version = getattr(imageio_ffmpeg, "__version__", "unknown")
        imageio_ffmpeg_module_path = getattr(imageio_ffmpeg, "__file__", "unknown")
        resolved_ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        resolved_ffmpeg_exists = os.path.isfile(resolved_ffmpeg_path) if resolved_ffmpeg_path else False
        logger.info("[lifespan] imageio_ffmpeg_version=%s, module_path=%s, resolved_ffmpeg_path=%s, exists=%s",
                   imageio_ffmpeg_version, imageio_ffmpeg_module_path, resolved_ffmpeg_path, resolved_ffmpeg_exists)
    except ImportError as e:
        logger.error("[lifespan] imageio-ffmpeg 未安装: %s", e)
    except Exception as e:
        logger.warning("[lifespan] imageio-ffmpeg 检查失败: %s", e)
    
    # FFmpeg 诊断信息
    try:
        from utils.ffmpeg_utils import get_ffmpeg_info
        ffmpeg_info = get_ffmpeg_info()
        logger.info("[lifespan] FFmpeg 诊断: %s", json.dumps(ffmpeg_info, ensure_ascii=False))
    except Exception as e:
        logger.warning("[lifespan] FFmpeg 诊断失败: %s", e)
    
    # Check if database is configured
    from storage.database.db import get_db_url
    db_url = get_db_url()
    
    if not db_url:
        # Database not configured - skip DB initialization
        logger.info("[lifespan] PGDATABASE_URL not configured, skipping database initialization")
        # Still compile graph without checkpointer for sync endpoints
        if graph_helper.is_agent_proj():
            base = graph_helper.get_agent_instance("agents.agent", None)
            sync_graph = base.builder.compile()
        else:
            base = graph_helper.get_graph_instance("graphs.graph")
            sync_graph = base.builder.compile()
        service.set_graph(sync_graph)
        yield
        return
    
    # Database configured - full initialization
    engine = get_engine()
    @event.listens_for(engine, "connect")
    def _set_utc(dbapi_conn, _):
        with dbapi_conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
    checkpointer = get_memory_saver()
    if graph_helper.is_agent_proj():
        base = graph_helper.get_agent_instance("agents.agent", None)
        sync_graph = base.builder.compile(checkpointer=checkpointer)
    else:
        base = graph_helper.get_graph_instance("graphs.graph")
        sync_graph = base.builder.compile()
    async_graph = base.builder.compile(checkpointer=checkpointer)
    service.set_graph(sync_graph)
    async_runtime = AsyncTaskRuntime(
        session_factory=get_session, engine=engine,
        graph=async_graph, checkpointer=checkpointer,
    )
    yield
    if async_runtime is not None:
        await async_runtime.shutdown()

app = FastAPI(lifespan=lifespan)

# 批量任务 API
from api.batch_routes import router as batch_router
app.include_router(batch_router)

# OpenAI 兼容接口处理器
openai_handler = OpenAIChatHandler(service)


@app.post("/async_run")
async def http_async_run(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_async_run: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {extract_core_stack()}")
    try:
        deadline_sec = parse_deadline_sec(request.headers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 一个 ID 走到底：task_id == run_id == thread_id == ctx.run_id == coze_run_id。
    # 优先用上游 x-run-id；没传就生成 UUID。
    run_id = request.headers.get(_ASYNC_HEADER_X_RUN_ID) or uuid.uuid4().hex

    # ctx 在 handler scope 构造，与同步 /run 路径一致；后面 new_context 默认会
    # 给 run_id 一个新 UUID，同步路径也是显式覆盖（main.py /run 处），这里同理。
    ctx = _new_async_ctx(method="async_run", headers=request.headers)
    ctx.run_id = run_id
    request_context.set(ctx)  # 与其他 HTTP endpoint 一致：让日志组件拿到 run_id 等信息
    run_config = init_run_config(async_graph, ctx)
    run_config["recursion_limit"] = async_task_config.RECURSION_LIMIT
    run_config.setdefault("configurable", {})["thread_id"] = run_id

    biz_context = extract_biz_context(request.headers) or {}
    if graph_helper.is_agent_proj() and not (isinstance(payload, dict) and payload.get("messages")):
        try:
            client_msg, _ = to_client_message(payload)
            payload = to_stream_input(client_msg)
        except Exception as e:
            error_response = service.error_classifier.get_error_response(
                e, {"node_name": "http_async_run", "run_id": run_id})
            logger.error(
                f"failed to convert agent payload in http_async_run: "
                f"[{error_response['error_code']}] {error_response['error_message']}, "
                f"traceback: {traceback.format_exc()}", exc_info=True
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": error_response["error_code"],
                    "error_message": error_response["error_message"],
                    "stack_trace": extract_core_stack(),
                },
            )

    try:
        return await async_runtime.submit(
            task_id=run_id,
            payload=payload,
            biz_context=biz_context,
            deadline_sec=deadline_sec,
            run_config=run_config,
            ctx=ctx,
        )
    except AsyncTaskStorageError as e:
        raise HTTPException(status_code=503,
                            detail=f"async-task storage unavailable: {e}")


@app.get("/task/{task_id}")
async def http_get_task(task_id: str) -> dict:
    try:
        row = await async_runtime.get(task_id)
    except AsyncTaskStorageError as e:
        raise HTTPException(status_code=503,
                            detail=f"async-task storage unavailable: {e}")
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return row


HEADER_X_RUN_ID = "x-run-id"
@app.post("/run")
async def http_run(request: Request) -> Dict[str, Any]:
    """
    异步提交单任务运行（基于持久化批次执行机制）。
    
    创建仅含 1 个任务的内部批次记录，持久化 run_id 和 queued 状态后返回。
    由 BatchExecutor 执行，前端通过 GET /api/run/{run_id}/status 轮询结果。
    禁止依赖 HTTP 请求生命周期内的临时后台协程。
    """
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {traceback.format_exc()}, error: {e}")

    ctx = new_context(method="run", headers=request.headers)
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    # 确保 run_id 是字符串类型，避免 UUID 对象与字符串不匹配
    run_id = str(ctx.run_id)
    ctx.run_id = run_id  # 同步更新 context 中的 run_id
    request_context.set(ctx)

    logger.info(
        f"Received request for /run: "
        f"run_id={run_id}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )

    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format, {extract_core_stack()}")

    # 提取 script_text
    script_text = payload.get("script_text", "")
    if not script_text:
        raise HTTPException(status_code=400, detail="script_text is required")

    # 创建内部批次任务（持久化到数据库）
    from storage.database.db import get_async_sessionmaker
    from storage.database.batch_models import BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus
    from api.batch_executor import BatchExecutor

    try:
        async_session_maker = get_async_sessionmaker()
        async with async_session_maker() as db:
            # 创建批次任务（仅含 1 个任务）
            batch_id = uuid.uuid4()
            batch = BatchJob(
                batch_id=batch_id,
                status=BatchJobStatus.CREATED,
                total_count=1,
                pending_count=1,
                running_count=0,
                success_count=0,
                failed_count=0,
                concurrency=1,
                idempotency_key=f"single-run-{run_id}",  # 幂等键
                source_filename="single-task",
            )
            db.add(batch)

            # 创建任务项
            task_id = uuid.uuid4()
            task = BatchTask(
                task_id=task_id,
                batch_id=batch_id,
                row_number=1,
                external_task_id=run_id,  # 使用 run_id 作为 external_task_id
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": script_text},
            )
            db.add(task)
            await db.commit()

            logger.info(
                f"[POST /run] Committed: batch_id={batch_id} (type={type(batch_id).__name__}), "
                f"task_id={task_id} (type={type(task_id).__name__}), "
                f"run_id={run_id} (type={type(run_id).__name__}), "
                f"external_task_id={task.external_task_id} (type={type(task.external_task_id).__name__})"
            )

            # 提交后立即验证：使用新 session 查询刚写入的记录
            async with async_session_maker() as verify_db:
                verify_result = await verify_db.execute(
                    select(BatchTask).where(BatchTask.external_task_id == run_id)
                )
                verify_task = verify_result.scalar_one_or_none()
                if verify_task is None:
                    logger.error(
                        f"[POST /run] CRITICAL: Post-commit verification failed! "
                        f"run_id={run_id} not found in database after commit. "
                        f"Checking by task_id={task_id}..."
                    )
                    # 尝试按 task_id 查询
                    verify_result2 = await verify_db.execute(
                        select(BatchTask).where(BatchTask.task_id == task_id)
                    )
                    verify_task2 = verify_result2.scalar_one_or_none()
                    if verify_task2 is not None:
                        logger.error(
                            f"[POST /run] Found by task_id but not by external_task_id! "
                            f"Stored external_task_id={verify_task2.external_task_id} "
                            f"(type={type(verify_task2.external_task_id).__name__}), "
                            f"queried run_id={run_id} (type={type(run_id).__name__})"
                        )
                        # 修正 external_task_id
                        verify_task2.external_task_id = run_id
                        await verify_db.commit()
                        logger.info(f"[POST /run] Fixed external_task_id to {run_id}")
                    else:
                        logger.error(
                            f"[POST /run] CRITICAL: Record not found by task_id either! "
                            f"Transaction may not have been committed properly."
                        )
                        raise HTTPException(
                            status_code=500,
                            detail="Database write verification failed: record not visible after commit"
                        )
                else:
                    logger.info(
                        f"[POST /run] Post-commit verification passed: "
                        f"found task with external_task_id={verify_task.external_task_id}"
                    )

            logger.info(f"Created single-task batch: batch_id={batch_id}, task_id={task_id}, run_id={run_id}")

            # 启动批次执行器（后台运行）
            executor = BatchExecutor(service)

            async def _execute_batch_background():
                """后台执行批次任务"""
                try:
                    async with async_session_maker() as exec_db:
                        await executor.start_batch(exec_db, batch_id)
                except Exception as e:
                    logger.error(f"Background batch execution failed: {e}", exc_info=True)

            # 创建后台任务执行批次
            bg_task = asyncio.create_task(_execute_batch_background())
            service.running_tasks[run_id] = bg_task

    except Exception as e:
        logger.error(f"Failed to create single-task batch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to submit task: {str(e)}")

    # 立即返回，不等待工作流完成
    return {
        "status": "submitted",
        "run_id": run_id,
        "batch_id": str(batch_id),
        "task_id": str(task_id),
        "message": "Workflow submitted successfully. Poll GET /api/run/{run_id}/status for result.",
    }


@app.get("/api/run/{run_id}/status")
async def http_get_run_status(
    run_id: str,
    batch_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    查询单任务运行状态（从持久化数据库读取）。
    
    返回 status: queued / running / success / failed / timeout
    以及完成后的完整 result（包含 final_video_url 等）。
    
    查询优先级：
    1. 通过真实 task_id 查询 BatchTask.task_id
    2. 再校验真实 batch_id（如果提供）
    3. external_task_id 仅用真实 run_id 兼容查询
    
    前端必须携带 POST /run 返回的真实 batch_id 和 task_id。
    """
    from storage.database.db import get_async_sessionmaker
    from storage.database.batch_models import BatchTask, BatchTaskStatus, BatchJob
    import uuid

    try:
        async_session_maker = get_async_sessionmaker()
        async with async_session_maker() as db:
            task = None
            query_method = None
            
            # 方式1: 通过真实 task_id 查询（最高优先级）
            if task_id:
                logger.info(f"[GET /status] Querying by task_id={task_id}")
                try:
                    task_uuid = uuid.UUID(task_id)
                    result = await db.execute(
                        select(BatchTask).where(BatchTask.task_id == task_uuid)
                    )
                    task = result.scalar_one_or_none()
                    if task:
                        query_method = "task_id"
                        # 如果提供了 batch_id，校验是否匹配
                        if batch_id:
                            try:
                                batch_uuid = uuid.UUID(batch_id)
                                if task.batch_id != batch_uuid:
                                    logger.warning(
                                        f"[GET /status] batch_id mismatch: "
                                        f"expected={batch_id}, actual={task.batch_id}"
                                    )
                                    raise HTTPException(
                                        status_code=404,
                                        detail=f"batch_id {batch_id} does not match task_id {task_id}"
                                    )
                            except ValueError:
                                pass  # batch_id 不是有效的 UUID
                        logger.info(f"[GET /status] Found by task_id={task_id}")
                except ValueError:
                    logger.warning(f"[GET /status] Invalid task_id format: {task_id}")
            
            # 方式2: 通过 external_task_id (run_id) 兼容查询
            if task is None:
                logger.info(f"[GET /status] Querying by external_task_id={run_id}")
                result = await db.execute(
                    select(BatchTask).where(BatchTask.external_task_id == run_id)
                )
                task = result.scalar_one_or_none()
                if task:
                    query_method = "external_task_id"
                    logger.info(f"[GET /status] Found by external_task_id={run_id}")
            
            if task is None:
                # 诊断：查询所有任务，查看实际存储的值
                logger.warning(f"[GET /status] Task not found. run_id={run_id}, batch_id={batch_id}, task_id={task_id}")
                all_result = await db.execute(select(BatchTask).limit(10))
                all_tasks = all_result.scalars().all()
                for t in all_tasks:
                    logger.warning(
                        f"[GET /status] Found task: task_id={t.task_id}, "
                        f"batch_id={t.batch_id}, "
                        f"external_task_id={t.external_task_id} (type={type(t.external_task_id).__name__}), "
                        f"status={t.status}"
                    )
                raise HTTPException(status_code=404, detail=f"run_id {run_id} not found")

            # 映射数据库状态到 API 状态
            status_map = {
                BatchTaskStatus.PENDING: "queued",
                BatchTaskStatus.RUNNING: "running",
                BatchTaskStatus.SUCCESS: "success",
                BatchTaskStatus.FAILED: "failed",
            }
            status = status_map.get(task.status, "unknown")

            response = {
                "run_id": task.external_task_id or run_id,
                "status": status,
                "task_id": str(task.task_id),
                "batch_id": str(task.batch_id),
                "query_method": query_method,
                "created_at": task.created_at.isoformat() if task.created_at else None,
            }

            # 如果已完成，返回完整结果
            if status in ("success", "failed", "timeout"):
                result_data = task.output_data or {}
                response["result"] = result_data
                response["completed_at"] = task.completed_at.isoformat() if task.completed_at else None
                if task.final_video_url:
                    response["final_video_url"] = task.final_video_url
                if task.error_message:
                    response["error"] = task.error_message
                    response["error_code"] = task.error_code

            # 检查是否超时（running 状态超过 30 分钟）
            if status == "running" and task.started_at:
                from datetime import datetime, timedelta
                running_duration = datetime.utcnow() - task.started_at
                if running_duration > timedelta(minutes=30):
                    response["status"] = "timeout"
                    response["message"] = "Task exceeded 30 minutes running time"

            return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get run status for {run_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


HEADER_X_WORKFLOW_STREAM_MODE = "x-workflow-stream-mode"


def _register_task(run_id: str, task: asyncio.Task):
    service.running_tasks[run_id] = task


@app.post("/stream_run")
async def http_stream_run(request: Request):
    ctx = new_context(method="stream_run", headers=request.headers)
    # 优先使用上游指定的 run_id，保证 cancel 能精确匹配
    upstream_run_id = request.headers.get(HEADER_X_RUN_ID)
    if upstream_run_id:
        ctx.run_id = upstream_run_id
    workflow_stream_mode = request.headers.get(HEADER_X_WORKFLOW_STREAM_MODE, "").lower()
    workflow_debug = workflow_stream_mode == "debug"
    request_context.set(ctx)
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {extract_core_stack()}, error: {e}")
    run_id = ctx.run_id
    is_agent = graph_helper.is_agent_proj()
    logger.info(
        f"Received request for /stream_run: "
        f"run_id={run_id}, "
        f"is_agent_project={is_agent}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_stream_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")

    if is_agent:
        stream_generator = agent_stream_handler(
            payload=payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
        )
    else:
        stream_generator = workflow_stream_handler(
            payload=payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
            run_opt=RunOpt(workflow_debug=workflow_debug),
        )

    response = StreamingResponse(stream_generator, media_type="text/event-stream")
    return response

@app.post("/cancel/{run_id}")
async def http_cancel(run_id: str, request: Request):
    """
    取消指定run_id的执行

    使用asyncio.Task.cancel()实现取消,这是Python标准的异步任务取消机制。
    LangGraph会在节点之间的await点检查CancelledError,实现优雅取消。
    """
    ctx = new_context(method="cancel", headers=request.headers)
    request_context.set(ctx)
    logger.info(f"Received cancel request for run_id: {run_id}")
    result = service.cancel_run(run_id, ctx)
    return result


@app.post(path="/node_run/{node_id}")
async def http_node_run(node_id: str, request: Request):
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        body_text = str(raw_body)
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {body_text}")
    ctx = new_context(method="node_run", headers=request.headers)
    request_context.set(ctx)
    logger.info(
        f"Received request for /node_run/{node_id}: "
        f"query={dict(request.query_params)}, "
        f"body={body_text}",
    )

    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_node_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")
    try:
        return await service.run_node(node_id, payload, ctx)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"node_id '{node_id}' not found or input miss required fields, traceback: {extract_core_stack()}")
    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": node_id})
        logger.error(
            f"Unexpected error in http_node_run: [{error_response['error_code']}] {error_response['error_message']}, "
            f"traceback: {traceback.format_exc()}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": extract_core_stack(),
            }
        )
    finally:
        cozeloop.flush()


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI Chat Completions API 兼容接口"""
    ctx = new_context(method="openai_chat", headers=request.headers)
    request_context.set(ctx)

    logger.info(f"Received request for /v1/chat/completions: run_id={ctx.run_id}")

    try:
        payload = await request.json()
        return await openai_handler.handle(payload, ctx)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in openai_chat_completions: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    finally:
        cozeloop.flush()


# ── 视频素材预览首页 ─────────────────────────────────────────────────────────

_MATERIALS_CSV = Path("assets/asset_manifest_v2_bound.csv")

_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>素材预览</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0f0f0f; color: #e0e0e0; padding: 20px; }
  h1 { font-size: 1.4rem; margin-bottom: 16px; color: #fff; }
  h2 { font-size: 1.1rem; margin: 28px 0 12px; color: #ccc; border-top: 1px solid #333; padding-top: 20px; }
  .status { padding: 12px; text-align: center; color: #888; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 16px; }
  .card { background: #1a1a1a; border-radius: 8px; overflow: hidden;
          cursor: pointer; transition: transform 0.15s; }
  .card:hover { transform: translateY(-2px); }
  .card.active { outline: 2px solid #4a9eff; }
  .card video { width: 100%; aspect-ratio: 16/9; object-fit: cover;
                background: #000; display: block; }
  .card .info { padding: 10px 12px; }
  .card .info .name { font-size: 0.85rem; font-weight: 600; color: #fff;
                      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .info .meta { font-size: 0.75rem; color: #888; margin-top: 4px; }
  .player { margin-top: 20px; background: #1a1a1a; border-radius: 8px;
            padding: 16px; display: none; }
  .player.show { display: block; }
  .player video { width: 100%; max-height: 60vh; background: #000;
                  border-radius: 4px; }
  .player .title { font-size: 1rem; margin-bottom: 10px; color: #fff; }
  .error { color: #f44; }
  .wf-section { background: #1a1a1a; border-radius: 8px; padding: 16px; }
  .wf-row { margin-bottom: 12px; }
  .wf-row label { display: block; font-size: 0.8rem; color: #888; margin-bottom: 4px; }
  .wf-row input, .wf-row textarea {
    width: 100%; background: #0f0f0f; border: 1px solid #333; border-radius: 4px;
    color: #e0e0e0; padding: 8px 10px; font-size: 0.85rem; font-family: inherit; }
  .wf-row textarea { min-height: 80px; resize: vertical; }
  .wf-row input:focus, .wf-row textarea:focus { outline: none; border-color: #4a9eff; }
  .wf-btn { background: #4a9eff; color: #fff; border: none; border-radius: 4px;
            padding: 8px 20px; font-size: 0.85rem; cursor: pointer; }
  .wf-btn:hover { background: #3a8eef; }
  .wf-btn:disabled { background: #555; cursor: not-allowed; }
  .wf-result { margin-top: 14px; background: #0f0f0f; border-radius: 4px;
               padding: 12px; font-size: 0.8rem; display: none; white-space: pre-wrap;
               word-break: break-all; max-height: 400px; overflow-y: auto; }
  .wf-result.show { display: block; }
  .wf-result .ok { color: #4caf50; }
  .wf-result .fail { color: #f44; }
  .wf-result .info { color: #4a9eff; }
  .wf-video { margin-top: 10px; }
  .wf-video video { width: 100%; max-height: 40vh; background: #000; border-radius: 4px; }
</style>
</head>
<body>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
<h1 style="margin:0">素材预览</h1>
<a href="/materials" style="color:#6ea8fe;text-decoration:none;font-size:13px">素材库 →</a>
</div>
<div id="status" class="status">加载中…</div>
<div id="grid" class="grid" style="display:none"></div>
<div id="player" class="player">
  <div id="player-title" class="title"></div>
  <video id="player-video" controls></video>
</div>

<h2>工作流测试</h2>
<div class="wf-section">
  <div class="wf-row">
    <label for="wf-script-id">script_id</label>
    <input id="wf-script-id" type="text" value="smoke_test">
  </div>
  <div class="wf-row">
    <label for="wf-script-text">script_text</label>
    <textarea id="wf-script-text">这款吹风机出差必备，折叠收纳超方便</textarea>
  </div>
  <div class="wf-row">
    <button id="wf-run-btn" class="wf-btn" onclick="runWorkflow()">运行工作流</button>
  </div>
  <div id="wf-result" class="wf-result"></div>
  <div id="wf-video" class="wf-video" style="display:none"></div>
</div>

<script>
const grid = document.getElementById('grid');
const status = document.getElementById('status');
const player = document.getElementById('player');
const playerTitle = document.getElementById('player-title');
const playerVideo = document.getElementById('player-video');

async function loadMaterials() {
  try {
    const res = await fetch('/api/materials');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (!data.materials || data.materials.length === 0) {
      status.textContent = '暂无可用素材';
      return;
    }
    status.style.display = 'none';
    grid.style.display = '';
    data.materials.forEach(m => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML =
        '<video muted preload="metadata" src="' + m.play_url + '"></video>' +
        '<div class="info"><div class="name">' + m.asset_id + '</div>' +
        '<div class="meta">' + (m.description || '') +
        (m.duration_sec ? ' · ' + m.duration_sec + 's' : '') + '</div></div>';
      card.addEventListener('click', () => playVideo(m, card));
      grid.appendChild(card);
    });
  } catch (e) {
    status.innerHTML = '<span class="error">加载失败: ' + e.message + '</span>';
  }
}

function playVideo(m, card) {
  document.querySelectorAll('.card.active').forEach(c => c.classList.remove('active'));
  card.classList.add('active');
  player.classList.add('show');
  playerTitle.textContent = m.asset_id + (m.description ? ' — ' + m.description : '');
  playerVideo.src = m.play_url;
  playerVideo.play().catch(() => {});
  player.scrollIntoView({ behavior: 'smooth' });
}

playerVideo.addEventListener('error', () => {
  playerTitle.innerHTML = '<span class="error">播放失败，预签名 URL 可能已过期</span>';
});

loadMaterials();

/* ── 工作流测试 ── */
async function runWorkflow() {
  const btn = document.getElementById('wf-run-btn');
  const resultDiv = document.getElementById('wf-result');
  const videoDiv = document.getElementById('wf-video');
  const scriptId = document.getElementById('wf-script-id').value.trim() || 'smoke_test';
  const scriptText = document.getElementById('wf-script-text').value.trim();

  if (!scriptText) {
    resultDiv.className = 'wf-result show';
    resultDiv.innerHTML = '<span class="fail">请输入 script_text</span>';
    return;
  }

  btn.disabled = true;
  btn.textContent = '运行中…';
  videoDiv.style.display = 'none';
  resultDiv.className = 'wf-result show';
  resultDiv.innerHTML = '<span class="info">正在提交工作流请求…</span>';

  const payload = {
    script_id: scriptId,
    script_source: 'manual',
    script_text: scriptText
  };

  try {
    const startTime = Date.now();
    const res = await fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    const raw = await res.text();
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) { parsed = null; }

    let html = '<span class="' + (res.ok ? 'ok' : 'fail') + '">HTTP ' + res.status +
               ' (' + elapsed + 's)</span>\\n\\n';

    if (parsed) {
      if (res.ok) {
        const d = parsed.data || parsed;
        if (d.final_video_url) {
          html += '<span class="ok">final_video_url:</span> ' + d.final_video_url + '\\n';
        }
        if (d.total_duration !== undefined) {
          html += '<span class="ok">total_duration:</span> ' + d.total_duration + 's\\n';
        }
        if (d.run_id) {
          html += '<span class="ok">run_id:</span> ' + d.run_id + '\\n';
        }
        if (d.batch_id) {
          html += '<span class="ok">batch_id:</span> ' + d.batch_id + '\\n';
        }
        if (d.task_id) {
          html += '<span class="ok">task_id:</span> ' + d.task_id + '\\n';
        }

        // 保存到 localStorage 并构建 status_url
        if (d.run_id && d.batch_id && d.task_id) {
          const statusUrl = `/api/run/${d.run_id}/status?batch_id=${d.batch_id}&task_id=${d.task_id}`;
          localStorage.setItem('workflow_run_id', d.run_id);
          localStorage.setItem('workflow_batch_id', d.batch_id);
          localStorage.setItem('workflow_task_id', d.task_id);
          localStorage.setItem('workflow_status_url', statusUrl);
          html += '<span class="ok">status_url:</span> ' + statusUrl + '\\n';
          html += '<span class="info">已保存到 localStorage，刷新页面后可继续轮询</span>\\n';
        }

        html += '\\n--- 完整响应 ---\\n' + JSON.stringify(parsed, null, 2);

        if (d.final_video_url) {
          videoDiv.style.display = 'block';
          videoDiv.innerHTML = '<video controls src="' + d.final_video_url + '"></video>';
        }
      } else {
        html += '<span class="fail">HTTP ' + res.status + ' 错误:</span>\\n';
        html += '<span class="fail">Request URL:</span> POST /run\\n';
        html += '<span class="fail">响应正文:</span>\\n' + JSON.stringify(parsed, null, 2);
      }
    } else {
      html += '<span class="fail">HTTP ' + res.status + '</span>\\n';
      html += '<span class="fail">Request URL:</span> POST /run\\n';
      html += '<span class="fail">响应正文:</span>\\n' + (raw || '(空响应)');
    }

    resultDiv.innerHTML = html;
  } catch (e) {
    resultDiv.innerHTML = '<span class="fail">请求失败: ' + e.message + '</span>';
  } finally {
    btn.disabled = false;
    btn.textContent = '运行工作流';
  }
}
</script>

<h2>工作流监控</h2>
<div class="wf-section">
  <div id="wm-root" style="margin-top: 16px;"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@babel/standalone/babel.min.js"></script>
<style>
  .wm-flow-container { position: relative; width: 100%; height: 500px; overflow: auto; background: #1a1a1a; border-radius: 8px; }
  .wm-flow-svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; }
  .wm-flow-svg line { stroke: #555; stroke-width: 2; }
  .wm-flow-svg line.running { stroke: #4a9eff; stroke-dasharray: 5,5; animation: dash 1s linear infinite; }
  @keyframes dash { to { stroke-dashoffset: -10; } }
  .wm-node { position: absolute; padding: 10px 14px; border-radius: 6px; font-size: 13px; min-width: 140px; text-align: center; border: 2px solid; cursor: pointer; transition: all 0.2s; }
  .wm-node:hover { transform: scale(1.05); }
  .wm-node.pending { background: #3a3a3a; border-color: #555; color: #999; }
  .wm-node.running { background: #1e3a5f; border-color: #4a9eff; color: #4a9eff; }
  .wm-node.success { background: #1e4d2e; border-color: #4caf50; color: #4caf50; }
  .wm-node.failed { background: #4d1e1e; border-color: #f44; color: #f44; }
  .wm-node.skipped { background: #2a2a2a; border-color: #444; color: #666; }
  .wm-node-label { font-weight: 600; }
  .wm-node-status { font-size: 11px; margin-top: 4px; opacity: 0.8; }
  .wm-node-duration { font-size: 10px; margin-top: 2px; opacity: 0.6; }
  .wm-detail-panel { margin-top: 16px; padding: 16px; background: #2a2a2a; border-radius: 8px; }
  .wm-detail-panel pre { font-size: 11px; margin: 4px 0; white-space: pre-wrap; word-break: break-all; }
</style>
<script type="text/babel">
const { useState, useEffect } = React;

let topologyData = null;

async function fetchTopology() {
  if (topologyData) return topologyData;
  const res = await fetch('/api/workflow/topology');
  topologyData = await res.json();
  return topologyData;
}

function getStatusColor(status) {
  const colors = { pending: '#999', running: '#4a9eff', success: '#4caf50', failed: '#f44', skipped: '#666' };
  return colors[status] || '#999';
}

function WorkflowTree({ nodes, edges, nodeStates, onNodeClick }) {
  const nodeWidth = 160;
  const nodeHeight = 70;
  const verticalGap = 30;
  const startX = 200;
  const startY = 40;

  const getNodePosition = (order) => ({
    x: startX,
    y: startY + (order - 1) * (nodeHeight + verticalGap)
  });

  const totalHeight = startY + nodes.length * (nodeHeight + verticalGap);

  return (
    <div className="wm-flow-container" style={{ height: `${totalHeight}px` }}>
      <svg className="wm-flow-svg" style={{ height: `${totalHeight}px` }}>
        {edges.map((edge, i) => {
          const sourceNode = nodes.find(n => n.id === edge.source);
          const targetNode = nodes.find(n => n.id === edge.target);
          if (!sourceNode || !targetNode) return null;
          const sourcePos = getNodePosition(sourceNode.order);
          const targetPos = getNodePosition(targetNode.order);
          const isRunning = nodeStates[edge.source]?.status === 'running';
          return (
            <line
              key={i}
              x1={sourcePos.x + nodeWidth / 2}
              y1={sourcePos.y + nodeHeight}
              x2={targetPos.x + nodeWidth / 2}
              y2={targetPos.y}
              className={isRunning ? 'running' : ''}
            />
          );
        })}
      </svg>
      {nodes.map(node => {
        const pos = getNodePosition(node.order);
        const state = nodeStates[node.id] || { status: 'pending' };
        return (
          <div
            key={node.id}
            className={`wm-node ${state.status}`}
            style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
            onClick={() => onNodeClick(node)}
          >
            <div className="wm-node-label">{node.label}</div>
            <div className="wm-node-status">{state.status}</div>
            {state.duration_ms && <div className="wm-node-duration">{state.duration_ms}ms</div>}
          </div>
        );
      })}
    </div>
  );
}

function WorkflowMonitor() {
  const [topology, setTopology] = useState(null);
  const [nodeStates, setNodeStates] = useState({});
  const [selectedNode, setSelectedNode] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [polling, setPolling] = useState(false);
  const [lastStatus, setLastStatus] = useState(null);
  const [requestInfo, setRequestInfo] = useState(null);

  useEffect(() => {
    fetchTopology().then(setTopology);
    // 从 localStorage 恢复状态
    const savedStatusUrl = localStorage.getItem('workflow_status_url');
    if (savedStatusUrl) {
      loadStatus(savedStatusUrl, true);
    }
  }, []);

  const loadStatus = async (statusUrl, isAutoLoad = false) => {
    if (!statusUrl) {
      setError('请输入 status_url');
      return;
    }
    setLoading(true);
    setError(null);
    setRequestInfo({ url: statusUrl, method: 'GET' });
    try {
      const res = await fetch(statusUrl);
      const raw = await res.text();
      let data;
      try { data = JSON.parse(raw); } catch (e) { data = null; }

      setRequestInfo({
        url: statusUrl,
        method: 'GET',
        status: res.status,
        statusText: res.statusText
      });

      if (!res.ok) {
        setError(`HTTP ${res.status} ${res.statusText}\\nRequest URL: ${statusUrl}\\n响应正文: ${raw}`);
        return;
      }

      if (data.error) {
        setError('加载失败: ' + (data.message || data.error));
        return;
      }

      setLastStatus(data);

      // 将状态映射到节点
      const stateMap = {};
      const status = data.status || 'pending';
      // 假设所有节点都是同一个状态（简化处理）
      if (topology) {
        topology.nodes.forEach(node => {
          stateMap[node.id] = {
            status: status,
            error_message: data.error_message,
            final_video_url: data.final_video_url
          };
        });
      }
      setNodeStates(stateMap);
      setError(null);

      // 如果状态是终态，停止轮询
      if (['success', 'failed', 'timeout', 'cancelled'].includes(status)) {
        setPolling(false);
      }
    } catch (e) {
      setError(`请求失败: ${e.message}\\nRequest URL: ${statusUrl}`);
    } finally {
      setLoading(false);
    }
  };

  const handleLoadClick = () => {
    const statusUrl = document.getElementById('wm-status-url').value.trim();
    if (statusUrl) {
      localStorage.setItem('workflow_status_url', statusUrl);
      loadStatus(statusUrl);
      setPolling(true);
    }
  };

  const handleNodeClick = (node) => {
    setSelectedNode(node);
  };

  // 轮询逻辑
  useEffect(() => {
    if (!polling) return;
    const statusUrl = localStorage.getItem('workflow_status_url');
    if (!statusUrl) return;

    const interval = setInterval(() => {
      loadStatus(statusUrl, true);
    }, 3000); // 每 3 秒轮询一次

    return () => clearInterval(interval);
  }, [polling, topology]);

  if (!topology) {
    return <div style={{ padding: '20px', color: '#999' }}>加载中...</div>;
  }

  const selectedState = selectedNode ? (nodeStates[selectedNode.id] || { status: 'pending' }) : null;

  return (
    <div>
      <div style={{ marginBottom: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
        <input
          id="wm-status-url"
          type="text"
          placeholder="输入 status_url (例如: /api/run/{run_id}/status?batch_id=...&task_id=...)"
          defaultValue={localStorage.getItem('workflow_status_url') || ''}
          style={{ flex: 1, padding: '8px', background: '#2a2a2a', border: '1px solid #444', borderRadius: '4px', color: '#fff' }}
        />
        <button
          onClick={handleLoadClick}
          disabled={loading}
          style={{ padding: '8px 16px', background: '#4a9eff', border: 'none', borderRadius: '4px', color: '#fff', cursor: 'pointer' }}
        >
          {loading ? '加载中...' : '加载运行记录'}
        </button>
        {polling && <span style={{ color: '#4caf50', fontSize: '12px' }}>轮询中...</span>}
      </div>
      {requestInfo && (
        <div style={{ padding: '8px', background: '#2a2a2a', borderRadius: '4px', marginBottom: '8px', fontSize: '11px', color: '#999' }}>
          <div>Request: {requestInfo.method} {requestInfo.url}</div>
          {requestInfo.status && <div>Response: HTTP {requestInfo.status} {requestInfo.statusText}</div>}
        </div>
      )}
      {error && <div style={{ padding: '12px', background: '#4d1e1e', border: '1px solid #f44', borderRadius: '4px', color: '#f44', marginBottom: '12px', whiteSpace: 'pre-wrap' }}>{error}</div>}
      <WorkflowTree
        nodes={topology.nodes}
        edges={topology.edges}
        nodeStates={nodeStates}
        onNodeClick={handleNodeClick}
      />
      {selectedNode && selectedState && (
        <div className="wm-detail-panel">
          <div><strong>节点:</strong> {selectedNode.label}</div>
          <div><strong>状态:</strong> <span style={{ color: getStatusColor(selectedState.status) }}>{selectedState.status}</span></div>
          {selectedState.started_at && <div><strong>开始:</strong> {new Date(selectedState.started_at * 1000).toLocaleTimeString()}</div>}
          {selectedState.completed_at && <div><strong>完成:</strong> {new Date(selectedState.completed_at * 1000).toLocaleTimeString()}</div>}
          {selectedState.duration_ms && <div><strong>耗时:</strong> {selectedState.duration_ms}ms</div>}
          {selectedState.error_message && <div style={{ color: '#f44', marginTop: '8px' }}><strong>错误:</strong> {selectedState.error_message}</div>}
          {selectedState.final_video_url && <div style={{ marginTop: '8px' }}><strong>视频:</strong> <a href={selectedState.final_video_url} target="_blank" style={{ color: '#4a9eff' }}>{selectedState.final_video_url}</a></div>}
          {selectedState.input_summary && Object.keys(selectedState.input_summary).length > 0 && (
            <div style={{ marginTop: '8px' }}><strong>输入:</strong><pre>{JSON.stringify(selectedState.input_summary, null, 2)}</pre></div>
          )}
          {selectedState.output_summary && Object.keys(selectedState.output_summary).length > 0 && (
            <div style={{ marginTop: '8px' }}><strong>输出:</strong><pre>{JSON.stringify(selectedState.output_summary, null, 2)}</pre></div>
          )}
        </div>
      )}
      {lastStatus && lastStatus.final_video_url && (
        <div style={{ marginTop: '16px', padding: '16px', background: '#1e4d2e', borderRadius: '8px' }}>
          <div style={{ color: '#4caf50', fontWeight: 'bold', marginBottom: '8px' }}>视频生成成功!</div>
          <video controls src={lastStatus.final_video_url} style={{ width: '100%', maxWidth: '400px', borderRadius: '4px' }}></video>
        </div>
      )}
    </div>
  );
}

// Auto-load status after workflow run
const originalRunWorkflow = window.runWorkflow;
window.runWorkflow = async function() {
  await originalRunWorkflow();
  const resultDiv = document.getElementById('wf-result');
  const text = resultDiv.textContent;
  const statusUrlMatch = text.match(/status_url[:\s]+(\/api\/run\/[^\s]+)/i);
  if (statusUrlMatch) {
    const statusUrl = statusUrlMatch[1];
    document.getElementById('wm-status-url').value = statusUrl;
    setTimeout(() => {
      // 点击"加载运行记录"按钮
      document.querySelectorAll('button').forEach(btn => {
        if (btn.textContent.includes('加载')) btn.click();
      });
    }, 500);
  }
};

ReactDOM.render(<WorkflowMonitor />, document.getElementById('wm-root'));
</script>
</body>
</html>"""


@app.get("/")
async def index_page():
    """视频素材预览首页。"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=_INDEX_HTML)


@app.get("/api/materials")
async def list_materials():
    """返回素材列表及预签名播放 URL。"""
    if not _MATERIALS_CSV.exists():
        return {"materials": [], "error": "素材清单文件不存在"}

    materials = []
    try:
        from storage.tos.tos_client import get_client
        client = get_client()
    except Exception:
        client = None

    with open(_MATERIALS_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            enabled = row.get("enabled", "true").strip().lower()
            if enabled not in ("true", "1", "yes"):
                continue
            bucket = row.get("bucket", "").strip()
            object_key = row.get("object_key", "").strip()
            if not bucket or not object_key:
                continue

            play_url = ""
            if client:
                try:
                    play_url = client.generate_presigned_url(
                        bucket=bucket, object_key=object_key, expires=300
                    )
                except Exception:
                    play_url = ""

            materials.append({
                "asset_id": row.get("asset_id", ""),
                "file_name": row.get("file_name", ""),
                "description": row.get("description", ""),
                "duration_sec": row.get("duration_sec", ""),
                "primary_scene_tag": row.get("primary_scene_tag", ""),
                "play_url": play_url,
            })

    return {"materials": materials, "count": len(materials)}


_MATERIALS_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>素材库</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f0f0f;color:#e0e0e0;padding:24px}
a{color:#6ea8fe;text-decoration:none}
a:hover{text-decoration:underline}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
h1{font-size:20px;color:#fff}
.status{padding:12px;text-align:center;color:#888;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.card{background:#1a1a1a;border-radius:8px;overflow:hidden;cursor:pointer;transition:transform .15s,box-shadow .15s;border:1px solid #2a2a2a}
.card:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.4)}
.card.selected{border-color:#4a9eff;box-shadow:0 0 0 2px rgba(74,158,255,.3)}
.card video{width:100%;aspect-ratio:9/16;object-fit:cover;display:block;background:#000}
.card-info{padding:10px 12px}
.card-title{font-size:13px;font-weight:600;color:#fff;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-desc{font-size:11px;color:#888;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-meta{font-size:10px;color:#666;margin-top:6px}
.player{margin-top:24px;background:#1a1a1a;border-radius:8px;overflow:hidden;border:1px solid #2a2a2a}
.player video{width:100%;max-height:70vh;display:block;background:#000}
.title{padding:12px 16px;font-size:14px;color:#ccc;border-bottom:1px solid #2a2a2a}
.error{color:#ff6b6b}
.ok{color:#4ecdc4}
</style>
</head>
<body>
<div class="header">
<h1>素材库</h1>
<a href="/">← 返回首页</a>
</div>
<div id="status" class="status">加载中…</div>
<div id="grid" class="grid" style="display:none"></div>
<div id="player" class="player" style="display:none">
  <div id="player-title" class="title"></div>
  <video id="player-video" controls></video>
</div>
<script>
const grid = document.getElementById('grid');
const status = document.getElementById('status');
const player = document.getElementById('player');
const playerTitle = document.getElementById('player-title');
const playerVideo = document.getElementById('player-video');

async function loadMaterials() {
  try {
    const res = await fetch('/api/materials');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (!data.materials || data.materials.length === 0) {
      status.textContent = '暂无可用素材';
      return;
    }
    status.style.display = 'none';
    grid.style.display = '';
    data.materials.forEach(m => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML =
        '<video src="' + m.play_url + '" muted preload="metadata"></video>' +
        '<div class="card-info">' +
          '<div class="card-title">' + (m.file_name || m.asset_id) + '</div>' +
          '<div class="card-desc">' + (m.description || '') + '</div>' +
          '<div class="card-meta">' + (m.primary_scene_tag || '') +
            (m.duration_sec ? ' · ' + m.duration_sec + 's' : '') + '</div>' +
        '</div>';
      card.addEventListener('click', () => playVideo(m, card));
      grid.appendChild(card);
    });
  } catch (e) {
    status.innerHTML = '<span class="error">加载失败: ' + e.message + '</span>';
  }
}

function playVideo(m, card) {
  document.querySelectorAll('.card.selected').forEach(c => c.classList.remove('selected'));
  card.classList.add('selected');
  playerTitle.textContent = (m.file_name || m.asset_id) + (m.description ? ' — ' + m.description : '');
  playerVideo.src = m.play_url;
  player.style.display = '';
  playerVideo.play().catch(() => {});
  player.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

playerVideo.addEventListener('error', () => {
  playerTitle.innerHTML = '<span class="error">视频加载失败（预签名 URL 可能已过期，请刷新页面）</span>';
});

loadMaterials();
</script>
</body>
</html>"""


@app.get("/materials")
async def materials_page():
    """素材库页面 - 仅展示素材浏览和播放，不含工作流测试。"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=_MATERIALS_HTML)


@app.get("/health")
async def health_check():
    try:
        # 这里可以添加更多的健康检查逻辑
        return {
            "status": "ok",
            "message": "Service is running",
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/internal/tos-health")
async def tos_health_check():
    """TOS 素材存储连接健康检查。
    
    仅返回连接状态摘要，不返回密钥、完整 URL 或请求签名。
    """
    import csv
    from storage.tos.tos_client import (
        check_env_configured,
        get_client,
        is_env_configured,
        MATERIAL_PREFIX,
    )

    result = {
        "env_configured": is_env_configured(),
        "env_details": check_env_configured(),
        "head_ok": False,
        "range_ok": False,
        "error_type": "",
    }

    if not is_env_configured():
        result["error_type"] = "env_not_configured"
        return result

    client = get_client()
    if client is None:
        result["error_type"] = "client_init_failed"
        return result

    # 从 CSV 获取第一个有效测试对象
    csv_path = "assets/asset_manifest_v2_bound.csv"
    test_bucket = ""
    test_key = ""
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bucket = row.get("bucket", "").strip()
                key = row.get("object_key", "").strip()
                if bucket and key and key.startswith(MATERIAL_PREFIX):
                    test_bucket = bucket
                    test_key = key
                    break
    except Exception as e:
        result["error_type"] = "csv_read_failed"
        return result

    if not test_bucket or not test_key:
        result["error_type"] = "no_valid_test_object"
        return result

    # HEAD 检查
    head_result = client.head_object(test_bucket, test_key)
    result["head_ok"] = head_result["exists"]
    if not head_result["exists"]:
        result["error_type"] = head_result.get("error_type", "head_failed")
        return result

    # Range 读取测试（仅读取前 1024 字节）
    try:
        presigned_url = client.generate_presigned_url(test_bucket, test_key, expires=60)
        # 只检查 URL 是否生成成功，不实际请求（避免在健康检查中暴露 URL）
        if presigned_url and presigned_url.startswith("http"):
            # 尝试 HTTP Range 请求
            import urllib.request
            req = urllib.request.Request(presigned_url, method="GET")
            req.add_header("Range", "bytes=0-1023")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status in (200, 206):
                        result["range_ok"] = True
                    else:
                        result["error_type"] = f"range_http_{resp.status}"
            except urllib.error.HTTPError as e:
                result["error_type"] = f"range_http_{e.code}"
            except Exception as e:
                result["error_type"] = "range_request_failed"
    except Exception as e:
        result["error_type"] = "presign_failed"

    return result


# =============================================================================
# 工作流拓扑和追踪 API
# =============================================================================

_WORKFLOW_TOPOLOGY = {
    "nodes": [
        {"id": "manual_script", "label": "文案输入", "order": 1},
        {"id": "input_normalization", "label": "输入规范化", "order": 2},
        {"id": "tts_generation", "label": "语音合成", "order": 3},
        {"id": "subtitle_timing", "label": "字幕时间轴", "order": 4},
        {"id": "material_source_audit", "label": "素材源审核", "order": 5},
        {"id": "material_matching", "label": "素材匹配", "order": 6},
        {"id": "clip_extraction", "label": "片段截取", "order": 7},
        {"id": "timeline_assembly", "label": "时间线组装", "order": 8},
        {"id": "final_composition", "label": "最终合成", "order": 9},
        {"id": "quality_check", "label": "质量检测", "order": 10},
    ],
    "edges": [
        {"source": "manual_script", "target": "input_normalization"},
        {"source": "input_normalization", "target": "tts_generation"},
        {"source": "tts_generation", "target": "subtitle_timing"},
        {"source": "subtitle_timing", "target": "material_source_audit"},
        {"source": "material_source_audit", "target": "material_matching"},
        {"source": "material_matching", "target": "clip_extraction"},
        {"source": "clip_extraction", "target": "timeline_assembly"},
        {"source": "timeline_assembly", "target": "final_composition"},
        {"source": "final_composition", "target": "quality_check"},
    ],
}


@app.get("/api/workflow/topology")
async def get_workflow_topology():
    """返回工作流拓扑结构。"""
    return _WORKFLOW_TOPOLOGY


def _sanitize_trace_entry(entry: dict) -> dict:
    """清理追踪条目，移除敏感信息。"""
    sanitized = {}
    for key, value in entry.items():
        # 跳过敏感字段
        if key in ("signed_url", "presigned_url", "source_url"):
            if value and isinstance(value, str):
                # 只保留 URL 的域名部分
                if "://" in value:
                    domain = value.split("://")[1].split("/")[0].split("?")[0]
                    sanitized[key] = f"[REDACTED:{domain}]"
                else:
                    sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value
        elif isinstance(value, str) and ("signature" in value.lower() or "access_key" in value.lower()):
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    return sanitized


@app.get("/api/runs/{run_id}/trace")
async def get_run_trace(run_id: str):
    """返回指定运行的追踪信息。"""
    # 使用持久化模块查询
    trace_data = get_trace(run_id)
    
    if not trace_data:
        return JSONResponse(
            status_code=404,
            content={
                "error": "trace_not_found",
                "run_id": run_id,
                "message": "未找到该运行记录，可能是旧版本运行或记录尚未持久化"
            }
        )
    
    # 清理敏感信息
    if "nodes" in trace_data:
        for node in trace_data["nodes"]:
            if "input_summary" in node:
                node["input_summary"] = _sanitize_trace_entry(node["input_summary"])
            if "output_summary" in node:
                node["output_summary"] = _sanitize_trace_entry(node["output_summary"])
    
    return trace_data


@app.get("/api/runs/by-script/{script_id}/latest")
async def get_latest_run_by_script_id(script_id: str):
    """获取指定 script_id 的最新 run_id。"""
    latest_run_id = get_latest_run_by_script(script_id)
    
    if not latest_run_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": "no_runs_found",
                "script_id": script_id,
                "message": "未找到该脚本的运行记录"
            }
        )
    
    return {
        "script_id": script_id,
        "latest_run_id": latest_run_id,
        "mapping": get_run_mapping(latest_run_id)
    }


@app.get(path="/graph_parameter")
async def http_graph_inout_parameter(request: Request):
    return service.graph_inout_schema()

def parse_args():
    parser = argparse.ArgumentParser(description="Start FastAPI server")
    parser.add_argument("-m", type=str, default="http", help="Run mode, support http,flow,node")
    parser.add_argument("-n", type=str, default="", help="Node ID for single node run")
    parser.add_argument("-p", type=int, default=5000, help="HTTP server port")
    parser.add_argument("-i", type=str, default="", help="Input JSON string for flow/node mode")
    return parser.parse_args()


def parse_input(input_str: str) -> Dict[str, Any]:
    """Parse input string, support both JSON string and plain text"""
    if not input_str:
        return {"text": "你好"}

    # Try to parse as JSON first
    try:
        return json.loads(input_str)
    except json.JSONDecodeError:
        # If not valid JSON, treat as plain text
        return {"text": input_str}

def start_http_server(port):
    workers = 1
    reload = False
    if graph_helper.is_dev_env():
        reload = True

    logger.info(f"Start HTTP Server, Port: {port}, Workers: {workers}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload, workers=workers)

if __name__ == "__main__":
    args = parse_args()
    if args.m == "http":
        start_http_server(args.p)
    elif args.m == "flow":
        payload = parse_input(args.i)
        result = asyncio.run(service.run(payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "node" and args.n:
        payload = parse_input(args.i)
        result = asyncio.run(service.run_node(args.n, payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "agent":
        agent_ctx = new_context(method="agent")
        for chunk in service.stream(
                {
                    "type": "query",
                    "session_id": "1",
                    "message": "你好",
                    "content": {
                        "query": {
                            "prompt": [
                                {
                                    "type": "text",
                                    "content": {"text": "现在几点了？请调用工具获取当前时间"},
                                }
                            ]
                        }
                    },
                },
                run_config={"configurable": {"session_id": "1"}},
                ctx=agent_ctx,
        ):
            print(chunk)
