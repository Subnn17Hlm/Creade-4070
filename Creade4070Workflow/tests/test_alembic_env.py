"""
Test alembic/env.py can be loaded without ImportError
"""
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_alembic_env_import():
    """Test that alembic/env.py imports can be loaded without errors."""
    try:
        # Test that the imports work
        from storage.database.batch_models import Base, BatchJob, BatchTask
        
        # Verify that Base and models are imported
        assert Base is not None, "Base should be imported"
        assert BatchJob is not None, "BatchJob should be imported"
        assert BatchTask is not None, "BatchTask should be imported"
        assert Base.metadata is not None, "Base.metadata should be available"
        
        print("✓ alembic/env.py imports successful")
        print(f"  Base: {Base}")
        print(f"  BatchJob: {BatchJob}")
        print(f"  BatchTask: {BatchTask}")
        print(f"  target_metadata: {Base.metadata}")
        
    except ImportError as e:
        print(f"✗ Failed to import models: {e}")
        raise


def test_base_metadata():
    """Test that Base.metadata contains the expected tables."""
    from storage.database.batch_models import Base
    
    # Check that the metadata has the expected tables
    tables = Base.metadata.tables
    assert 'batch_jobs' in tables, "batch_jobs table should be in metadata"
    assert 'batch_tasks' in tables, "batch_tasks table should be in metadata"
    
    print("✓ Base.metadata contains expected tables")
    print(f"  Tables: {list(tables.keys())}")


if __name__ == "__main__":
    test_alembic_env_import()
    test_base_metadata()
    print("\n✓ All alembic tests passed")
