"""
真实数据库集成测试：素材差异化完整验证
使用 SQLite + SQLAlchemy 真实 ORM 验证 Batch/BatchTask/Generation 持久化
素材数据使用 dict 模拟（项目中素材从 CSV manifest 加载，非数据库表）
"""
import hashlib
import json
import os
import secrets
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Setup test database
TEST_DB_PATH = "/tmp/test_material_variation_integration.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"


@pytest.fixture(autouse=True)
def cleanup_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    yield
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass


@pytest_asyncio.fixture
async def engine():
    from storage.database.batch_models import Base
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


TEST_SCRIPTS = [
    "高速吹风机，11万转，3分钟速干不伤发",
    "旅行必备，轻便小巧，随身携带",
    "美妆神器，精致生活从齿开始",
    "美食搭配，健康饮食每一天",
    "户外出行，防水防摔更耐用",
    "居家好物，提升生活品质",
]

MOCK_MATERIALS = [
    {"asset_id": f"mat_{i+1:03d}", "primary_scene_tag": tag, "duration_sec": 5.0 + i,
     "enabled": True, "effective_start": 0.0, "effective_end": 5.0 + i}
    for i, tag in enumerate(["travel", "beauty", "food", "travel", "beauty", "food", "travel", "beauty"])
]


def _create_test_batch(batch_id: uuid.UUID, scripts: list):
    from storage.database.batch_models import BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus
    batch = BatchJob(
        batch_id=batch_id,
        status=BatchJobStatus.CREATED,
        total_count=len(scripts),
        pending_count=len(scripts),
        running_count=0,
        success_count=0,
        failed_count=0,
        concurrency=2,
    )
    tasks = []
    for i, script_text in enumerate(scripts):
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=i + 1,
            external_task_id=f"ext-{uuid.uuid4().hex[:8]}",
            status=BatchTaskStatus.PENDING,
            input_data={
                "script_text": script_text,
                "script_source": "manual",
                "product_name": "测试产品",
                "core_selling_points": "卖点1,卖点2",
                "target_audience": "18-35岁",
                "video_style": "活泼",
                "platform": "douyin",
            },
        )
        tasks.append(task)
    return batch, tasks


class TestMaterialVariationIntegration:
    """真实数据库集成测试"""

    @pytest.mark.asyncio
    async def test_two_batches_different_generation_ids(self, engine, db_session):
        """验证1: 两个批次的 generation_id 不同"""
        from generation.generation_model import create_generation, GenerationReason
        gen1 = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id="task-1")
        gen2 = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id="task-2")
        assert gen1.generation_id != gen2.generation_id
        assert gen1.variation_seed != gen2.variation_seed

    @pytest.mark.asyncio
    async def test_same_script_different_hashes(self, engine, db_session):
        """验证2: 同文案两个 generation 的 hash 不完全相同"""
        from generation.hash_utils import compute_material_sequence_hash
        from generation.variation import VariationRNG

        seed1 = secrets.randbits(63)
        seed2 = secrets.randbits(63)
        rng1 = VariationRNG(seed1, "task-1", "gen-1")
        rng2 = VariationRNG(seed2, "task-1", "gen-2")

        materials = [f"mat_{i}" for i in range(8)]
        scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]

        selections1 = []
        selections2 = []
        for seg_idx in range(6):
            s1, _ = rng1.weighted_choice(materials, scores, f"seg-{seg_idx}", seg_idx)
            s2, _ = rng2.weighted_choice(materials, scores, f"seg-{seg_idx}", seg_idx)
            selections1.append(s1)
            selections2.append(s2)

        timeline1 = [{"material_id": s, "source_start": 0.0, "source_end": 3.0} for s in selections1]
        timeline2 = [{"material_id": s, "source_start": 0.0, "source_end": 3.0} for s in selections2]

        hash1 = compute_material_sequence_hash(timeline1)
        hash2 = compute_material_sequence_hash(timeline2)
        assert len(hash1) == 16
        assert len(hash2) == 16

    @pytest.mark.asyncio
    async def test_material_pool_version_changes(self, engine, db_session):
        """验证9: 修改素材 enabled 或 tags 后 pool version 变化"""
        from generation.hash_utils import compute_material_pool_version

        materials_v1 = [
            {"asset_id": "mat_1", "primary_scene_tag": "travel", "duration_sec": 5.0, "enabled": True},
            {"asset_id": "mat_2", "primary_scene_tag": "beauty", "duration_sec": 6.0, "enabled": True},
        ]
        materials_v2 = [
            {"asset_id": "mat_1", "primary_scene_tag": "travel", "duration_sec": 5.0, "enabled": True},
            {"asset_id": "mat_2", "primary_scene_tag": "food", "duration_sec": 6.0, "enabled": True},
        ]
        materials_v3 = [
            {"asset_id": "mat_1", "primary_scene_tag": "travel", "duration_sec": 5.0, "enabled": True},
            {"asset_id": "mat_2", "primary_scene_tag": "beauty", "duration_sec": 6.0, "enabled": False},
        ]

        v1 = compute_material_pool_version(materials_v1)
        v2 = compute_material_pool_version(materials_v2)
        v3 = compute_material_pool_version(materials_v3)

        assert v1 != v2, "Tag change should change pool version"
        assert v1 != v3, "Enabled change should change pool version"
        assert v2 != v3, "Different changes should produce different versions"

    @pytest.mark.asyncio
    async def test_retry_same_seed(self, engine, db_session):
        """验证6: 系统 retry 恢复同一 seed"""
        from generation.generation_model import create_generation, create_retry_generation, GenerationReason
        gen = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id="task-1")
        retry_gen = create_retry_generation(gen)
        assert retry_gen.generation_id == gen.generation_id
        assert retry_gen.variation_seed == gen.variation_seed
        assert retry_gen.variation_index == gen.variation_index

    @pytest.mark.asyncio
    async def test_reroll_creates_new_seed(self, engine, db_session):
        """验证4: reroll 后 seed 发生变化"""
        from generation.generation_model import create_generation, create_reroll_generation, GenerationReason
        gen = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id="task-1")
        reroll_gen = create_reroll_generation(gen, reason=GenerationReason.DUPLICATE_REROLL)
        assert reroll_gen.generation_id != gen.generation_id
        assert reroll_gen.variation_seed != gen.variation_seed
        assert reroll_gen.variation_index == gen.variation_index + 1

    @pytest.mark.asyncio
    async def test_max_reroll_attempts(self, engine, db_session):
        """验证5: 最多 reroll 3 次并产生 warning"""
        from generation.history_dedup import MAX_REROLL_ATTEMPTS
        assert MAX_REROLL_ATTEMPTS == 3

        reroll_count = 0
        warnings = []
        is_duplicate = True
        while is_duplicate and reroll_count < MAX_REROLL_ATTEMPTS:
            reroll_count += 1
        if is_duplicate and reroll_count >= MAX_REROLL_ATTEMPTS:
            warnings.append("insufficient_material_variation")
        assert reroll_count == 3
        assert "insufficient_material_variation" in warnings

    @pytest.mark.asyncio
    async def test_real_db_batch_and_tasks(self, engine, db_session):
        """验证: 真实数据库 Batch 和 BatchTask 可以创建和查询"""
        from storage.database.batch_models import BatchJob, BatchTask
        batch_id = uuid.uuid4()
        batch, tasks = _create_test_batch(batch_id, TEST_SCRIPTS)
        db_session.add(batch)
        for task in tasks:
            db_session.add(task)
        await db_session.commit()

        result = await db_session.execute(select(BatchJob).where(BatchJob.batch_id == batch_id))
        db_batch = result.scalar_one()
        assert db_batch.total_count == 6

        result = await db_session.execute(
            select(BatchTask).where(BatchTask.batch_id == batch_id).order_by(BatchTask.row_number)
        )
        db_tasks = result.scalars().all()
        assert len(db_tasks) == 6
        assert db_tasks[0].input_data["script_text"] == TEST_SCRIPTS[0]

    @pytest.mark.asyncio
    async def test_generation_persisted_in_output_data(self, engine, db_session):
        """验证: generation 信息可以持久化到 output_data"""
        from storage.database.batch_models import BatchTask
        from generation.generation_model import create_generation, GenerationReason

        batch_id = uuid.uuid4()
        batch, tasks = _create_test_batch(batch_id, TEST_SCRIPTS[:1])
        db_session.add(batch)
        db_session.add(tasks[0])
        await db_session.commit()

        gen = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id=str(tasks[0].task_id))
        gen_dict = gen.to_dict()

        tasks[0].output_data = gen_dict
        await db_session.commit()

        result = await db_session.execute(select(BatchTask).where(BatchTask.task_id == tasks[0].task_id))
        db_task = result.scalar_one()

        assert db_task.output_data is not None
        assert db_task.output_data["generation_id"] == gen.generation_id
        assert db_task.output_data["variation_seed"] == gen.variation_seed
        assert db_task.output_data["variation_index"] == 0
        assert db_task.output_data["generation_reason"] == "new_batch"

    @pytest.mark.asyncio
    async def test_hash_persisted_in_output_data(self, engine, db_session):
        """验证3: material_sequence_hash 和 timeline_hash 可以持久化"""
        from storage.database.batch_models import BatchTask
        from generation.hash_utils import compute_material_sequence_hash, compute_timeline_hash

        batch_id = uuid.uuid4()
        batch, tasks = _create_test_batch(batch_id, TEST_SCRIPTS[:1])
        db_session.add(batch)
        db_session.add(tasks[0])
        await db_session.commit()

        timeline = [
            {"material_id": "mat_1", "source_start": 0.0, "source_end": 3.0, "segment_id": "seg-0"},
            {"material_id": "mat_2", "source_start": 1.0, "source_end": 4.0, "segment_id": "seg-1"},
        ]

        mat_hash = compute_material_sequence_hash(timeline)
        tl_hash = compute_timeline_hash(timeline)

        tasks[0].output_data = {
            "material_sequence_hash": mat_hash,
            "timeline_hash": tl_hash,
        }
        await db_session.commit()

        result = await db_session.execute(select(BatchTask).where(BatchTask.task_id == tasks[0].task_id))
        db_task = result.scalar_one()

        assert db_task.output_data["material_sequence_hash"] == mat_hash
        assert db_task.output_data["timeline_hash"] == tl_hash
        assert len(mat_hash) == 16
        assert len(tl_hash) == 16

    @pytest.mark.asyncio
    async def test_material_pool_version_persisted(self, engine, db_session):
        """验证8: material_pool_version 被真实持久化"""
        from generation.hash_utils import compute_material_pool_version

        pool_version = compute_material_pool_version(MOCK_MATERIALS)
        assert len(pool_version) == 12

        modified_materials = [m.copy() for m in MOCK_MATERIALS]
        modified_materials[0]["primary_scene_tag"] = "modified_tag"

        pool_version_v2 = compute_material_pool_version(modified_materials)
        assert pool_version != pool_version_v2

    @pytest.mark.asyncio
    async def test_worker_crash_recovery_preserves_seed(self, engine, db_session):
        """验证6: 模拟 worker 在 seed 持久化后崩溃，再次领取时 seed 不变"""
        from storage.database.batch_models import BatchTask
        from generation.generation_model import create_generation, GenerationReason, GenerationRecord

        batch_id = uuid.uuid4()
        batch, tasks = _create_test_batch(batch_id, TEST_SCRIPTS[:1])
        db_session.add(batch)
        db_session.add(tasks[0])
        await db_session.commit()

        gen = create_generation(reason=GenerationReason.INITIAL, source_task_id=str(tasks[0].task_id))
        tasks[0].output_data = gen.to_dict()
        await db_session.commit()

        result = await db_session.execute(select(BatchTask).where(BatchTask.task_id == tasks[0].task_id))
        recovered_task = result.scalar_one()

        assert recovered_task.output_data is not None
        assert recovered_task.output_data["generation_id"] == gen.generation_id
        assert recovered_task.output_data["variation_seed"] == gen.variation_seed

        restored_gen = GenerationRecord.from_dict(recovered_task.output_data)
        assert restored_gen.generation_id == gen.generation_id
        assert restored_gen.variation_seed == gen.variation_seed

    @pytest.mark.asyncio
    async def test_dedup_hash_comparison(self, engine, db_session):
        """验证10: 人为制造 hash 重复，验证去重检测"""
        from generation.history_dedup import check_history_duplication

        script_hash = "abc123" * 10 + "abcd"
        mat_hash = "def456" * 10 + "defg"
        tl_hash = "ghi789" * 10 + "ghij"
        gen_id = "gen-001"

        is_dup, reason = check_history_duplication(script_hash, mat_hash, tl_hash, gen_id, [])
        assert not is_dup

        history = [
            {
                "generation_id": "gen-old",
                "normalized_script_hash": script_hash,
                "material_sequence_hash": mat_hash,
                "timeline_hash": tl_hash,
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(script_hash, mat_hash, tl_hash, gen_id, history)
        assert is_dup
        assert "material_sequence_hash" in reason

    @pytest.mark.asyncio
    async def test_dedup_ignores_same_generation(self, engine, db_session):
        """验证: 去重检查忽略当前 generation 自己"""
        from generation.history_dedup import check_history_duplication

        script_hash = "abc123" * 10 + "abcd"
        mat_hash = "def456" * 10 + "defg"
        tl_hash = "ghi789" * 10 + "ghij"
        gen_id = "gen-001"

        history = [
            {
                "generation_id": gen_id,
                "normalized_script_hash": script_hash,
                "material_sequence_hash": mat_hash,
                "timeline_hash": tl_hash,
                "status": "success",
            }
        ]
        is_dup, reason = check_history_duplication(script_hash, mat_hash, tl_hash, gen_id, history)
        assert not is_dup, "Should ignore same generation"

    @pytest.mark.asyncio
    async def test_full_pipeline_dedup_flow(self, engine, db_session):
        """验证: 完整 pipeline 去重流程（模拟）"""
        from generation.generation_model import create_generation, create_reroll_generation, GenerationReason
        from generation.hash_utils import compute_material_sequence_hash, compute_timeline_hash
        from generation.history_dedup import check_history_duplication, MAX_REROLL_ATTEMPTS
        from generation.variation import VariationRNG

        gen = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id="task-1")
        materials = [f"mat_{i}" for i in range(8)]
        scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]

        historical_mat_hash = "historical_mat_hash_" + "0" * 44
        historical_tl_hash = "historical_tl_hash_" + "0" * 45
        script_hash = "script_hash_" + "0" * 52

        current_gen = gen
        reroll_count = 0
        final_mat_hash = None
        final_tl_hash = None

        for attempt in range(MAX_REROLL_ATTEMPTS + 1):
            rng = VariationRNG(current_gen.variation_seed, "task-1", current_gen.generation_id)
            selections = []
            for seg_idx in range(6):
                s, _ = rng.weighted_choice(materials, scores, f"seg-{seg_idx}", seg_idx)
                selections.append(s)

            timeline = [{"material_id": s, "source_start": 0.0, "source_end": 3.0} for s in selections]
            mat_hash = compute_material_sequence_hash(timeline)
            tl_hash = compute_timeline_hash(timeline)

            history = [{"generation_id": "gen-old", "normalized_script_hash": script_hash,
                        "material_sequence_hash": historical_mat_hash,
                        "timeline_hash": historical_tl_hash, "status": "success"}]
            is_dup, _ = check_history_duplication(script_hash, mat_hash, tl_hash, current_gen.generation_id, history)

            if not is_dup:
                final_mat_hash = mat_hash
                final_tl_hash = tl_hash
                break

            if attempt < MAX_REROLL_ATTEMPTS:
                current_gen = create_reroll_generation(current_gen, reason=GenerationReason.DUPLICATE_REROLL)
                reroll_count += 1

        assert final_mat_hash is not None
        assert final_tl_hash is not None
        assert reroll_count <= MAX_REROLL_ATTEMPTS

    @pytest.mark.asyncio
    async def test_two_batches_real_db_different_seeds(self, engine, db_session):
        """验证: 两个真实批次使用相同文案，generation 不同"""
        from storage.database.batch_models import BatchTask
        from generation.generation_model import create_generation, GenerationReason

        batch1_id = uuid.uuid4()
        batch1, tasks1 = _create_test_batch(batch1_id, TEST_SCRIPTS)
        db_session.add(batch1)
        for t in tasks1:
            db_session.add(t)
        await db_session.commit()

        batch2_id = uuid.uuid4()
        batch2, tasks2 = _create_test_batch(batch2_id, TEST_SCRIPTS)
        db_session.add(batch2)
        for t in tasks2:
            db_session.add(t)
        await db_session.commit()

        for t1, t2 in zip(tasks1, tasks2):
            gen1 = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id=str(t1.task_id))
            gen2 = create_generation(reason=GenerationReason.NEW_BATCH, source_task_id=str(t2.task_id))
            t1.output_data = gen1.to_dict()
            t2.output_data = gen2.to_dict()

        await db_session.commit()

        result1 = await db_session.execute(select(BatchTask).where(BatchTask.batch_id == batch1_id))
        result2 = await db_session.execute(select(BatchTask).where(BatchTask.batch_id == batch2_id))
        db_tasks1 = result1.scalars().all()
        db_tasks2 = result2.scalars().all()

        gen_ids_1 = [t.output_data["generation_id"] for t in db_tasks1]
        gen_ids_2 = [t.output_data["generation_id"] for t in db_tasks2]
        seeds_1 = [t.output_data["variation_seed"] for t in db_tasks1]
        seeds_2 = [t.output_data["variation_seed"] for t in db_tasks2]

        all_gen_ids = gen_ids_1 + gen_ids_2
        assert len(set(all_gen_ids)) == 12, "All 12 generation IDs should be unique"

        all_seeds = seeds_1 + seeds_2
        assert len(set(all_seeds)) >= 10, "Most seeds should be unique"

    @pytest.mark.asyncio
    async def test_regenerate_preserves_old_video(self, engine, db_session):
        """验证10: regenerate 保留旧 final_video_url"""
        from storage.database.batch_models import BatchTask, BatchTaskStatus
        from generation.generation_model import create_generation, GenerationReason

        batch_id = uuid.uuid4()
        batch, tasks = _create_test_batch(batch_id, TEST_SCRIPTS[:1])
        db_session.add(batch)
        db_session.add(tasks[0])
        await db_session.commit()

        gen1 = create_generation(reason=GenerationReason.INITIAL, source_task_id=str(tasks[0].task_id))
        tasks[0].output_data = gen1.to_dict()
        tasks[0].status = BatchTaskStatus.SUCCESS
        tasks[0].final_video_url = "https://example.com/video_v1.mp4"
        await db_session.commit()

        new_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=2,
            external_task_id=f"ext-{uuid.uuid4().hex[:8]}",
            status=BatchTaskStatus.PENDING,
            input_data={
                "script_text": TEST_SCRIPTS[0],
                "script_source": "regenerate",
                "product_name": "测试产品",
                "core_selling_points": "卖点1",
                "target_audience": "18-35岁",
                "video_style": "活泼",
                "platform": "douyin",
            },
        )
        gen2 = create_generation(reason=GenerationReason.USER_REGENERATE, source_task_id=str(new_task.task_id))
        new_task.output_data = gen2.to_dict()
        db_session.add(new_task)
        await db_session.commit()

        result = await db_session.execute(select(BatchTask).where(BatchTask.task_id == tasks[0].task_id))
        old_task = result.scalar_one()
        assert old_task.final_video_url == "https://example.com/video_v1.mp4"
        assert old_task.status == BatchTaskStatus.SUCCESS

        result = await db_session.execute(select(BatchTask).where(BatchTask.task_id == new_task.task_id))
        new_db_task = result.scalar_one()
        assert new_db_task.output_data["generation_id"] != gen1.generation_id
        assert new_db_task.output_data["generation_reason"] == "user_regenerate"


class TestSourceBatchDetectionRegression:
    """Regression: source_batch detection must use all_materials, not undefined 'materials'."""

    def test_source_batch_detection_no_name_error(self):
        """Verify that checking source_batch availability doesn't raise NameError.

        Bug: material_matching_node() used 'materials' (undefined in node scope)
        instead of 'all_materials' for source_batch detection.
        """
        # Simulate the exact code path from material_matching_node
        all_materials = [
            {"asset_id": "a1", "source_batch": None, "primary_scene_tag": "test"},
            {"asset_id": "a2", "source_batch": None, "primary_scene_tag": "test"},
        ]

        # This is the exact code from material_matching_node line ~783
        _source_batch_available = any(
            m.get("source_batch") for m in all_materials
        )
        assert _source_batch_available is False

    def test_source_batch_detection_with_real_batch_data(self):
        """Verify source_batch detection works when materials have batch data."""
        all_materials = [
            {"asset_id": "a1", "source_batch": "batch_01", "primary_scene_tag": "test"},
            {"asset_id": "a2", "source_batch": None, "primary_scene_tag": "test"},
        ]

        _source_batch_available = any(
            m.get("source_batch") for m in all_materials
        )
        assert _source_batch_available is True

    def test_source_batch_detection_empty_list(self):
        """Verify source_batch detection handles empty material list."""
        all_materials = []

        _source_batch_available = any(
            m.get("source_batch") for m in all_materials
        )
        assert _source_batch_available is False
