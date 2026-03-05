from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.queue import (
    ACTIVE_KEY,
    DEAD_KEY,
    MAX_RETRIES,
    QUEUE_KEY,
    RETRY_KEY,
    QueueItem,
    complete,
    dequeue,
    enqueue,
    fail,
    recover_stale,
    requeue_retries,
)


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    pipe = MagicMock()
    pipe.lpush = MagicMock(return_value=pipe)
    pipe.zrem = MagicMock(return_value=pipe)
    pipe.sadd = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.exec = AsyncMock(return_value=[])
    # pipeline() is called synchronously in the source code (no await),
    # so it must be a regular MagicMock, not an AsyncMock.
    redis.pipeline = MagicMock(return_value=pipe)
    with patch("src.shared.queue.get_redis", return_value=redis):
        yield redis


class TestQueueItemSerialize:
    def test_round_trip(self):
        item = QueueItem(job_posting_id="123", url="https://example.com/job", board_id="b1")
        restored = QueueItem.deserialize(item.serialize())
        assert restored.job_posting_id == "123"
        assert restored.url == "https://example.com/job"
        assert restored.board_id == "b1"

    def test_includes_all_fields(self):
        item = QueueItem(
            job_posting_id="456",
            url="https://example.com/job/2",
            board_id="b2",
            retries=2,
        )
        data = json.loads(item.serialize())
        expected_keys = {"job_posting_id", "url", "retries", "board_id", "enqueued_at"}
        assert set(data.keys()) == expected_keys
        assert data["job_posting_id"] == "456"
        assert data["url"] == "https://example.com/job/2"
        assert data["retries"] == 2
        assert data["board_id"] == "b2"
        assert isinstance(data["enqueued_at"], float)

    def test_retries_default_zero(self):
        item = QueueItem(job_posting_id="1", url="https://example.com")
        assert item.retries == 0

    def test_round_trip_preserves_retries(self):
        item = QueueItem(job_posting_id="1", url="https://example.com", retries=5)
        restored = QueueItem.deserialize(item.serialize())
        assert restored.retries == 5


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        result = await enqueue([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_pushes_items(self, mock_redis):
        items = [
            QueueItem(job_posting_id="1", url="https://example.com/a", board_id="b1"),
            QueueItem(job_posting_id="2", url="https://example.com/b", board_id="b1"),
        ]
        result = await enqueue(items)
        assert result == 2

        pipe = mock_redis.pipeline.return_value
        assert pipe.lpush.call_count == 2
        for call_args in pipe.lpush.call_args_list:
            assert call_args[0][0] == QUEUE_KEY
        pipe.exec.assert_awaited_once()


class TestDequeue:
    @pytest.mark.asyncio
    async def test_empty_queue(self, mock_redis):
        mock_redis.rpop = AsyncMock(return_value=None)
        items = await dequeue(5)
        assert items == []

    @pytest.mark.asyncio
    async def test_pops_items(self, mock_redis):
        item1 = QueueItem(job_posting_id="1", url="https://example.com/a", board_id="b1")
        item2 = QueueItem(job_posting_id="2", url="https://example.com/b", board_id="b1")
        mock_redis.rpop = AsyncMock(side_effect=[item1.serialize(), item2.serialize(), None])
        mock_redis.hset = AsyncMock()

        items = await dequeue(5)
        assert len(items) == 2
        assert items[0].job_posting_id == "1"
        assert items[1].job_posting_id == "2"
        # Verify items were tracked in active set
        assert mock_redis.hset.call_count == 2


class TestComplete:
    @pytest.mark.asyncio
    async def test_removes_from_active(self, mock_redis):
        mock_redis.hdel = AsyncMock()
        item = QueueItem(job_posting_id="1", url="https://example.com/job", board_id="b1")
        await complete(item)
        mock_redis.hdel.assert_awaited_once_with(ACTIVE_KEY, item.url)


class TestFail:
    @pytest.mark.asyncio
    async def test_retries_when_under_max(self, mock_redis):
        mock_redis.hdel = AsyncMock()
        mock_redis.zadd = AsyncMock()
        item = QueueItem(
            job_posting_id="1",
            url="https://example.com/job",
            retries=0,
            board_id="b1",
        )
        await fail(item, "some error")
        # retries incremented to 1, which is < MAX_RETRIES (3)
        mock_redis.zadd.assert_awaited_once()
        call_args = mock_redis.zadd.call_args
        assert call_args[0][0] == RETRY_KEY
        mock_redis.hdel.assert_awaited_once_with(ACTIVE_KEY, "https://example.com/job")

    @pytest.mark.asyncio
    async def test_dead_letter_at_max(self, mock_redis):
        mock_redis.hdel = AsyncMock()
        mock_redis.lpush = AsyncMock()
        item = QueueItem(
            job_posting_id="1",
            url="https://example.com/job",
            retries=MAX_RETRIES - 1,
            board_id="b1",
        )
        await fail(item, "final error")
        # retries incremented to MAX_RETRIES → dead letter
        mock_redis.lpush.assert_awaited_once()
        call_args = mock_redis.lpush.call_args
        assert call_args[0][0] == DEAD_KEY
        mock_redis.hdel.assert_awaited_once_with(ACTIVE_KEY, "https://example.com/job")


class TestRequeueRetries:
    @pytest.mark.asyncio
    async def test_no_due_items(self, mock_redis):
        mock_redis.zrangebyscore = AsyncMock(return_value=[])
        result = await requeue_retries()
        assert result == 0

    @pytest.mark.asyncio
    async def test_moves_due_items(self, mock_redis):
        item = QueueItem(job_posting_id="1", url="https://example.com/job", board_id="b1")
        mock_redis.zrangebyscore = AsyncMock(return_value=[item.serialize()])

        pipe = mock_redis.pipeline.return_value
        result = await requeue_retries()
        assert result == 1
        pipe.lpush.assert_called_once()
        pipe.zrem.assert_called_once()
        pipe.exec.assert_awaited_once()


class TestRecoverStale:
    @pytest.mark.asyncio
    async def test_no_stale(self, mock_redis):
        mock_redis.hgetall = AsyncMock(return_value={})
        result = await recover_stale()
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_stale_recent_timestamps(self, mock_redis):
        mock_redis.hgetall = AsyncMock(
            return_value={
                "https://example.com/job": str(time.time()),
            }
        )
        result = await recover_stale()
        assert result == 0

    @pytest.mark.asyncio
    async def test_recovers_old_items(self, mock_redis):
        mock_redis.hdel = AsyncMock()
        old_time = str(time.time() - 600)  # 10 minutes ago, beyond 5-minute timeout
        mock_redis.hgetall = AsyncMock(
            return_value={
                "https://example.com/job1": old_time,
                "https://example.com/job2": old_time,
            }
        )
        result = await recover_stale()
        assert result == 2
        mock_redis.hdel.assert_awaited_once()
        # Verify both stale keys were passed
        call_args = mock_redis.hdel.call_args
        assert call_args[0][0] == ACTIVE_KEY
        assert set(call_args[0][1:]) == {
            "https://example.com/job1",
            "https://example.com/job2",
        }
