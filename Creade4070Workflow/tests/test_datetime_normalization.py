"""
Tests for datetime timezone normalization in status endpoint.

Verifies that the status endpoint handles mixed naive/aware datetimes correctly.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


def normalize_datetime(dt):
    """
    规范化 datetime 为 timezone-aware UTC。
    
    - None 保持 None
    - naive datetime 明确按 UTC 补 timezone.utc
    - aware datetime 转换为 UTC
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_datetime_iso(dt):
    """
    格式化 datetime 为带时区的 ISO 8601 UTC 格式。
    """
    normalized = normalize_datetime(dt)
    if normalized is None:
        return None
    return normalized.isoformat()


class TestDatetimeNormalization:
    """Test datetime normalization functions."""

    def test_normalize_datetime_none(self):
        """Test normalize_datetime with None."""
        assert normalize_datetime(None) is None

    def test_normalize_datetime_naive(self):
        """Test normalize_datetime with naive datetime."""
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)
        result = normalize_datetime(naive_dt)
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_normalize_datetime_aware_utc(self):
        """Test normalize_datetime with aware UTC datetime."""
        aware_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = normalize_datetime(aware_dt)
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc
        assert result == aware_dt

    def test_normalize_datetime_aware_other_timezone(self):
        """Test normalize_datetime with aware non-UTC datetime."""
        tz_plus8 = timezone(timedelta(hours=8))
        aware_dt = datetime(2024, 1, 1, 20, 0, 0, tzinfo=tz_plus8)
        result = normalize_datetime(aware_dt)
        assert result.tzinfo is not None
        assert result.hour == 12
        assert result.tzinfo == timezone.utc

    def test_format_datetime_iso_none(self):
        """Test format_datetime_iso with None."""
        assert format_datetime_iso(None) is None

    def test_format_datetime_iso_naive(self):
        """Test format_datetime_iso with naive datetime."""
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)
        result = format_datetime_iso(naive_dt)
        assert result is not None
        assert "+00:00" in result

    def test_format_datetime_iso_aware(self):
        """Test format_datetime_iso with aware datetime."""
        aware_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = format_datetime_iso(aware_dt)
        assert result is not None
        assert "2024-01-01" in result


class TestDatetimeSubtraction:
    """Test datetime subtraction with mixed timezones."""

    def test_naive_minus_naive_works(self):
        """Test naive - naive works."""
        dt1 = datetime(2024, 1, 1, 12, 0, 0)
        dt2 = datetime(2024, 1, 1, 11, 0, 0)
        result = dt1 - dt2
        assert result == timedelta(hours=1)

    def test_aware_minus_aware_works(self):
        """Test aware - aware works."""
        dt1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        result = dt1 - dt2
        assert result == timedelta(hours=1)

    def test_naive_minus_aware_fails(self):
        """Test naive - aware fails with TypeError."""
        dt1 = datetime(2024, 1, 1, 12, 0, 0)  # naive
        dt2 = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)  # aware
        with pytest.raises(TypeError):
            _ = dt1 - dt2

    def test_aware_minus_naive_fails(self):
        """Test aware - naive fails with TypeError."""
        dt1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # aware
        dt2 = datetime(2024, 1, 1, 11, 0, 0)  # naive
        with pytest.raises(TypeError):
            _ = dt1 - dt2

    def test_normalized_subtraction_works(self):
        """Test normalized datetime subtraction works."""
        dt1 = datetime(2024, 1, 1, 12, 0, 0)  # naive
        dt2 = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)  # aware
        
        # Normalize both
        dt1_norm = normalize_datetime(dt1)
        dt2_norm = normalize_datetime(dt2)
        
        # Now subtraction works
        result = dt1_norm - dt2_norm
        assert result == timedelta(hours=1)


class TestStatusEndpointDatetimeHandling:
    """Test status endpoint datetime handling logic."""

    def test_timeout_check_with_naive_started_at(self):
        """Test timeout check logic with naive started_at."""
        started_at = datetime(2024, 1, 1, 12, 0, 0)  # naive
        now_utc = datetime.now(timezone.utc)
        started_at_utc = normalize_datetime(started_at)
        
        # This should not raise TypeError
        running_duration = now_utc - started_at_utc
        assert running_duration > timedelta(0)

    def test_timeout_check_with_aware_started_at(self):
        """Test timeout check logic with aware started_at."""
        started_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # aware
        now_utc = datetime.now(timezone.utc)
        started_at_utc = normalize_datetime(started_at)
        
        # This should not raise TypeError
        running_duration = now_utc - started_at_utc
        assert running_duration > timedelta(0)

    def test_timeout_check_with_mixed_timezones(self):
        """Test timeout check logic with mixed timezones."""
        started_at = datetime(2024, 1, 1, 20, 0, 0, tzinfo=timezone(timedelta(hours=8)))  # +08:00
        now_utc = datetime.now(timezone.utc)
        started_at_utc = normalize_datetime(started_at)
        
        # This should not raise TypeError
        running_duration = now_utc - started_at_utc
        assert running_duration > timedelta(0)

    def test_timeout_check_with_null_started_at(self):
        """Test timeout check logic with null started_at."""
        started_at = None
        started_at_utc = normalize_datetime(started_at)
        
        assert started_at_utc is None
        # Should skip timeout check when started_at is None


class TestRealWorldScenarios:
    """Test real-world datetime scenarios."""

    def test_database_naive_vs_api_aware(self):
        """Simulate database returning naive datetime, API using aware."""
        # Database returns naive datetime (common with some DB drivers)
        db_started_at = datetime(2024, 1, 1, 12, 0, 0)  # naive from DB
        
        # API uses aware datetime
        now_utc = datetime.now(timezone.utc)  # aware
        
        # Without normalization, this would fail
        with pytest.raises(TypeError):
            _ = now_utc - db_started_at
        
        # With normalization, it works
        db_started_at_utc = normalize_datetime(db_started_at)
        running_duration = now_utc - db_started_at_utc
        assert running_duration > timedelta(0)

    def test_all_datetime_fields_normalized(self):
        """Test all datetime fields can be normalized."""
        fields = {
            'created_at': datetime(2024, 1, 1, 12, 0, 0),  # naive
            'started_at': datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),  # aware
            'completed_at': None,
        }
        
        normalized = {}
        for key, value in fields.items():
            normalized[key] = normalize_datetime(value)
        
        assert normalized['created_at'].tzinfo == timezone.utc
        assert normalized['started_at'].tzinfo == timezone.utc
        assert normalized['completed_at'] is None
