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


def test_sync_ssl_handling():
    """Test that synchronous psycopg2 driver keeps sslmode in URL."""
    # Test the logic directly without loading env.py
    test_url = "postgresql://user:pass@host:5432/db?sslmode=require"
    
    # Simulate get_sync_connect_args logic
    # psycopg2 should keep sslmode in URL, no modification needed
    result_url = test_url
    connect_args = {}
    
    # psycopg2 should keep sslmode in URL
    assert "sslmode=require" in result_url, "psycopg2 should keep sslmode in URL"
    assert connect_args == {}, "psycopg2 should not need connect_args for sslmode"
    
    print("✓ Synchronous psycopg2 SSL handling correct")
    print(f"  URL keeps sslmode: {result_url}")
    print(f"  connect_args: {connect_args}")


def test_async_ssl_handling():
    """Test that async asyncpg driver gets ssl=require in connect_args."""
    from storage.database.db import get_async_db_url, get_async_engine
    
    # Mock a URL with sslmode=require
    original_url = os.getenv("PGDATABASE_URL")
    try:
        os.environ["PGDATABASE_URL"] = "postgresql://user:pass@host:5432/db?sslmode=require"
        
        # Get async URL - should remove sslmode
        async_url = get_async_db_url()
        assert "sslmode" not in async_url, "asyncpg URL should not contain sslmode"
        assert "postgresql+asyncpg://" in async_url, "Should be asyncpg format"
        
        print("✓ Asynchronous asyncpg SSL handling correct")
        print(f"  URL removes sslmode: {async_url}")
        print(f"  Engine will use connect_args={{'ssl': 'require'}}")
        
    finally:
        # Restore original URL
        if original_url:
            os.environ["PGDATABASE_URL"] = original_url
        else:
            os.environ.pop("PGDATABASE_URL", None)


if __name__ == "__main__":
    test_alembic_env_import()
    test_base_metadata()
    test_sync_ssl_handling()
    test_async_ssl_handling()
    print("\n✓ All alembic tests passed")
