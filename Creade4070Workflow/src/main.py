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
from sqlalchemy import event

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
    global result
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
    run_id = ctx.run_id
    request_context.set(ctx)

    logger.info(
        f"Received request for /run: "
        f"run_id={run_id}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )

    # 变量初始化，用于 finally 块
    script_id = None
    trace_file_path = None
    run_status = "failed"
    quality_status = None

    try:
        payload = await request.json()
        
        # 提取 script_id 并注册运行映射
        script_id = payload.get("script_id") or payload.get("run_id") or run_id
        trace_file_path = f"/tmp/runs/{script_id}/node_trace.jsonl"
        register_run(run_id, script_id, trace_file_path)

        # 创建任务并记录 - 这是关键，让我们可以通过run_id取消任务
        task = asyncio.create_task(service.run(payload, ctx))
        service.running_tasks[run_id] = task

        try:
            result = await asyncio.wait_for(task, timeout=float(TIMEOUT_SECONDS))
        except asyncio.TimeoutError:
            logger.error(f"Run execution timeout after {TIMEOUT_SECONDS}s for run_id: {run_id}")
            task.cancel()
            try:
                result = await task
            except asyncio.CancelledError:
                run_status = "timeout"
                return {
                    "status": "timeout",
                    "run_id": run_id,
                    "message": f"Execution timeout: exceeded {TIMEOUT_SECONDS} seconds"
                }

        if not result:
            result = {}
        if isinstance(result, dict):
            result["run_id"] = run_id
            # 提取质量状态
            quality_report = result.get("quality_report") or {}
            if isinstance(quality_report, dict):
                quality_status = quality_report.get("status")
            run_status = "success" if result.get("status") == "success" else "failed"
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format, {extract_core_stack()}")

    except asyncio.CancelledError:
        logger.info(f"Request cancelled for run_id: {run_id}")
        run_status = "cancelled"
        result = {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        return result

    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": "http_run", "run_id": run_id})
        logger.error(
            f"Unexpected error in http_run: [{error_response['error_code']}] {error_response['error_message']}, "
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
        # 持久化运行追踪
        try:
            if script_id and trace_file_path:
                from graphs.node_trace_utils import read_node_trace
                import os
                run_dir = os.path.dirname(trace_file_path)
                trace_entries = read_node_trace(run_dir)
                persist_run_trace(run_id, script_id, trace_entries, run_status, quality_status)
        except Exception as e:
            logger.warning("Failed to persist run trace: %s", e)
        
        cozeloop.flush()


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
        html += '\\n--- 完整响应 ---\\n' + JSON.stringify(parsed, null, 2);

        if (d.final_video_url) {
          videoDiv.style.display = 'block';
          videoDiv.innerHTML = '<video controls src="' + d.final_video_url + '"></video>';
        }
      } else {
        html += '<span class="fail">错误:</span>\\n' + JSON.stringify(parsed, null, 2);
      }
    } else {
      html += raw || '(空响应)';
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
  <div class="wf-row">
    <label for="wm-run-id">run_id</label>
    <input id="wm-run-id" type="text" placeholder="输入 run_id 查看运行追踪">
    <button id="wm-load-btn" class="wf-btn" onclick="loadWorkflowTrace()">加载运行记录</button>
  </div>
  <div id="wm-container" style="display:none; margin-top: 16px;">
    <div style="display: flex; gap: 16px; height: 500px;">
      <div id="wm-flow" style="flex: 1; background: #1a1a1a; border-radius: 8px; overflow: hidden;"></div>
      <div id="wm-detail" style="width: 300px; background: #1a1a1a; border-radius: 8px; padding: 16px; overflow-y: auto;">
        <h3 style="margin: 0 0 12px 0; font-size: 1rem; color: #fff;">节点详情</h3>
        <div id="wm-detail-content" style="font-size: 0.85rem; color: #ccc;">点击节点查看详情</div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@babel/standalone/babel.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/reactflow@11/dist/umd/reactflow.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reactflow@11/dist/style.css">
<style>
  .wm-node { padding: 10px 14px; border-radius: 6px; font-size: 13px; min-width: 120px; text-align: center; border: 2px solid; }
  .wm-node.pending { background: #3a3a3a; border-color: #555; color: #999; }
  .wm-node.running { background: #1e3a5f; border-color: #4a9eff; color: #4a9eff; }
  .wm-node.success { background: #1e4d2e; border-color: #4caf50; color: #4caf50; }
  .wm-node.failed { background: #4d1e1e; border-color: #f44; color: #f44; }
  .wm-node.skipped { background: #2a2a2a; border-color: #444; color: #666; }
  .wm-node-label { font-weight: 600; }
  .wm-node-status { font-size: 11px; margin-top: 4px; opacity: 0.8; }
  .wm-node-duration { font-size: 10px; margin-top: 2px; opacity: 0.6; }
  .react-flow__edge-path { stroke: #555; stroke-width: 2; }
  .react-flow__edge.animated path { stroke: #4a9eff; }
</style>
<script type="text/babel">
const { useState, useEffect, useCallback } = React;
const ReactFlow = window.ReactFlow;

let topologyData = null;

async function fetchTopology() {
  if (topologyData) return topologyData;
  const res = await fetch('/api/workflow/topology');
  topologyData = await res.json();
  return topologyData;
}

function WorkflowMonitor() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [nodeStates, setNodeStates] = useState({});
  const [selectedNode, setSelectedNode] = useState(null);
  const [containerVisible, setContainerVisible] = useState(false);

  useEffect(() => {
    fetchTopology().then(topo => {
      const flowNodes = topo.nodes.map(n => ({
        id: n.id,
        type: 'default',
        position: { x: 200, y: n.order * 70 },
        data: { label: n.label, nodeId: n.id },
        className: 'wm-node pending',
      }));
      const flowEdges = topo.edges.map((e, i) => ({
        id: `e${i}`,
        source: e.source,
        target: e.target,
        animated: false,
      }));
      setNodes(flowNodes);
      setEdges(flowEdges);
    });
  }, []);

  const onNodeClick = useCallback((event, node) => {
    setSelectedNode(node.data.nodeId);
    const state = nodeStates[node.data.nodeId] || { status: 'pending' };
    const detail = document.getElementById('wm-detail-content');
    detail.innerHTML = `
      <div><strong>节点:</strong> ${node.data.label}</div>
      <div><strong>状态:</strong> <span style="color: ${getStatusColor(state.status)}">${state.status}</span></div>
      ${state.started_at ? `<div><strong>开始:</strong> ${new Date(state.started_at * 1000).toLocaleTimeString()}</div>` : ''}
      ${state.completed_at ? `<div><strong>完成:</strong> ${new Date(state.completed_at * 1000).toLocaleTimeString()}</div>` : ''}
      ${state.duration_ms ? `<div><strong>耗时:</strong> ${state.duration_ms}ms</div>` : ''}
      ${state.error_message ? `<div style="color:#f44;margin-top:8px;"><strong>错误:</strong> ${state.error_message}</div>` : ''}
      ${Object.keys(state.input_summary || {}).length > 0 ? `<div style="margin-top:8px;"><strong>输入:</strong><pre style="font-size:11px;margin:4px 0;">${JSON.stringify(state.input_summary, null, 2)}</pre></div>` : ''}
      ${Object.keys(state.output_summary || {}).length > 0 ? `<div style="margin-top:8px;"><strong>输出:</strong><pre style="font-size:11px;margin:4px 0;">${JSON.stringify(state.output_summary, null, 2)}</pre></div>` : ''}
    `;
  }, [nodeStates]);

  return (
    <div style={{ width: '100%', height: '500px' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodeClick={onNodeClick}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        minZoom={0.5}
        maxZoom={1.5}
      />
    </div>
  );
}

function getStatusColor(status) {
  const colors = { pending: '#999', running: '#4a9eff', success: '#4caf50', failed: '#f44', skipped: '#666' };
  return colors[status] || '#999';
}

async function loadWorkflowTrace() {
  const runId = document.getElementById('wm-run-id').value.trim();
  if (!runId) { alert('请输入 run_id'); return; }

  const container = document.getElementById('wm-container');
  container.style.display = 'block';

  try {
    const res = await fetch(`/api/runs/${runId}/trace`);
    const data = await res.json();

    if (data.error) {
      alert('加载失败: ' + data.error);
      return;
    }

    const topo = await fetchTopology();
    const stateMap = {};
    (data.node_states || []).forEach(ns => { stateMap[ns.node] = ns; });

    const flowNodes = topo.nodes.map(n => {
      const state = stateMap[n.id] || { status: 'pending' };
      return {
        id: n.id,
        type: 'default',
        position: { x: 200, y: n.order * 70 },
        data: { label: n.label, nodeId: n.id, state },
        className: `wm-node ${state.status}`,
      };
    });

    const flowEdges = topo.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      animated: stateMap[e.source]?.status === 'running',
    }));

    ReactDOM.render(
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        onNodeClick={(event, node) => {
          const state = node.data.state || { status: 'pending' };
          const detail = document.getElementById('wm-detail-content');
          detail.innerHTML = `
            <div><strong>节点:</strong> ${node.data.label}</div>
            <div><strong>状态:</strong> <span style="color: ${getStatusColor(state.status)}">${state.status}</span></div>
            ${state.started_at ? `<div><strong>开始:</strong> ${new Date(state.started_at * 1000).toLocaleTimeString()}</div>` : ''}
            ${state.completed_at ? `<div><strong>完成:</strong> ${new Date(state.completed_at * 1000).toLocaleTimeString()}</div>` : ''}
            ${state.duration_ms ? `<div><strong>耗时:</strong> ${state.duration_ms}ms</div>` : ''}
            ${state.error_message ? `<div style="color:#f44;margin-top:8px;"><strong>错误:</strong> ${state.error_message}</div>` : ''}
            ${Object.keys(state.input_summary || {}).length > 0 ? `<div style="margin-top:8px;"><strong>输入:</strong><pre style="font-size:11px;margin:4px 0;">${JSON.stringify(state.input_summary, null, 2)}</pre></div>` : ''}
            ${Object.keys(state.output_summary || {}).length > 0 ? `<div style="margin-top:8px;"><strong>输出:</strong><pre style="font-size:11px;margin:4px 0;">${JSON.stringify(state.output_summary, null, 2)}</pre></div>` : ''}
          `;
        }}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        minZoom={0.5}
        maxZoom={1.5}
      />,
      document.getElementById('wm-flow')
    );
  } catch (e) {
    alert('加载失败: ' + e.message);
  }
}

// Auto-load trace after workflow run
const originalRunWorkflow = window.runWorkflow;
window.runWorkflow = async function() {
  await originalRunWorkflow();
  const resultDiv = document.getElementById('wf-result');
  const text = resultDiv.textContent;
  const match = text.match(/run_id[:\s]+([a-f0-9-]{36})/i);
  if (match) {
    document.getElementById('wm-run-id').value = match[1];
    setTimeout(() => loadWorkflowTrace(), 500);
  }
};
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
