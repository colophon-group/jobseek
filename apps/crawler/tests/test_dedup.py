from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.dedup import SEEN_KEY, SEEN_TTL, filter_unseen, mark_seen


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    pipe = MagicMock()
    pipe.sadd = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.exec = AsyncMock(return_value=[])
    # pipeline() is called synchronously in the source code (no await),
    # so it must be a regular MagicMock, not an AsyncMock.
    redis.pipeline = MagicMock(return_value=pipe)
    with patch("src.shared.dedup.get_redis", return_value=redis):
        yield redis


class TestFilterUnseen:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        result = await filter_unseen([])
        assert result == []

    @pytest.mark.asyncio
    async def test_all_new(self, mock_redis):
        mock_redis.sismember = AsyncMock(return_value=False)
        urls = ["https://example.com/a", "https://example.com/b"]
        result = await filter_unseen(urls)
        assert result == urls
        assert mock_redis.sismember.call_count == 2

    @pytest.mark.asyncio
    async def test_all_seen(self, mock_redis):
        mock_redis.sismember = AsyncMock(return_value=True)
        urls = ["https://example.com/a", "https://example.com/b"]
        result = await filter_unseen(urls)
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed(self, mock_redis):
        # First URL is new, second is already seen
        mock_redis.sismember = AsyncMock(side_effect=[False, True])
        urls = ["https://example.com/new", "https://example.com/seen"]
        result = await filter_unseen(urls)
        assert result == ["https://example.com/new"]


class TestMarkSeen:
    @pytest.mark.asyncio
    async def test_empty_list(self, mock_redis):
        await mark_seen([])
        # No pipeline calls should be made
        mock_redis.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_urls(self, mock_redis):
        urls = ["https://example.com/a", "https://example.com/b"]
        await mark_seen(urls)

        pipe = mock_redis.pipeline.return_value
        assert pipe.sadd.call_count == 2
        for call_args in pipe.sadd.call_args_list:
            assert call_args[0][0] == SEEN_KEY
        pipe.expire.assert_called_once_with(SEEN_KEY, SEEN_TTL)
        pipe.exec.assert_awaited_once()
