from __future__ import annotations

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from src.core.monitors import BoardGoneError, DiscoveredJob, mokahr
from src.core.monitors.mokahr import can_handle, discover


def _aes_encrypt(plain: bytes, key: str, iv: str) -> str:
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()

    cipher = Cipher(algorithms.AES(key.encode("ascii")), modes.CBC(iv.encode("ascii")))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode("ascii")


def _spa_html(iv: str) -> str:
    init_data = {
        "aesIv": iv,
        "jobsGroupedByLocation": [
            {"id": "深圳市", "label": "深圳市", "cityId": 440300},
        ],
    }
    init_value = json.dumps(init_data, ensure_ascii=False).replace('"', "&quot;")
    return f'<input id="init-data" type="hidden" value="{init_value}">'


def _encrypted_jobs(jobs: list[dict], key: str, iv: str) -> dict:
    payload = json.dumps({"data": {"jobs": jobs}}, ensure_ascii=False).encode("utf-8")
    return {"data": _aes_encrypt(payload, key, iv), "necromancer": key}


def _raw_job(job_id: str, **overrides) -> dict:
    raw = {
        "id": job_id,
        "title": "卫星激光通信系统工程师",
        "commitment": "全职",
        "publishedAt": "2026-04-22T08:41:14",
        "locations": [{"cityId": 440300, "country": "中国"}],
        "minSalary": 40,
        "maxSalary": 80,
        "salaryUnit": 0,
        "minExperience": 5,
        "maxExperience": 10,
        "education": "硕士",
        "department": {"id": 430278, "name": "中兴通讯股份有限公司"},
        "zhineng": {"id": 72363, "name": "研发类"},
        "jobDescription": "<p>工作职责</p>",
    }
    raw.update(overrides)
    return raw


class TestDiscover:
    async def test_decrypts_pages_until_short_page(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(mokahr, "_PAGE_SIZE", 2)
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"
        seen_offsets: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            body = json.loads(request.content)
            seen_offsets.append(body["offset"])
            assert body["orgId"] == "zte"
            assert body["siteId"] == 47588
            assert body["limit"] == 2
            assert body["locale"] == "zh-CN"
            if body["offset"] == 0:
                jobs = [_raw_job("one"), _raw_job("two", title="算法工程师")]
            elif body["offset"] == 2:
                jobs = [_raw_job("three", title="测试工程师")]
            else:
                jobs = []
            return httpx.Response(200, json=_encrypted_jobs(jobs, key, iv), request=request)

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(board, client)

        assert seen_offsets == [0, 2]
        assert [job.title for job in jobs] == [
            "卫星激光通信系统工程师",
            "算法工程师",
            "测试工程师",
        ]
        assert all(isinstance(job, DiscoveredJob) for job in jobs)
        assert jobs[0].url == "https://app.mokahr.com/social-recruitment/zte/47588#/job/one"
        assert jobs[0].description == "<p>工作职责</p>"
        assert jobs[0].locations == ["深圳市, 中国"]
        assert jobs[0].employment_type == "全职"
        assert jobs[0].date_posted == "2026-04-22T08:41:14"
        assert jobs[0].base_salary == {
            "currency": "CNY",
            "min": 40000,
            "max": 80000,
            "unit": "monthly",
        }
        assert jobs[0].extras == {"experience": {"min_years": 5.0, "max_years": 10.0}}
        assert jobs[0].metadata == {
            "department": "中兴通讯股份有限公司",
            "education": "硕士",
            "job_function": "研发类",
        }

    async def test_first_page_404_is_board_gone(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request)

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BoardGoneError, match="Mokahr board page returned 404") as exc:
                await discover(board, client)

        assert exc.value.url == "https://app.mokahr.com/social-recruitment/zte/47588"

    async def test_flags_truncation_at_max_jobs(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(mokahr, "_PAGE_SIZE", 2)
        monkeypatch.setattr(mokahr, "_MAX_JOBS", 2)
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            return httpx.Response(
                200,
                json=_encrypted_jobs([_raw_job("one"), _raw_job("two")], key, iv),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert result.truncated is True
        assert len(result.jobs_by_url) == 2

    async def test_requires_org_id_and_site_id(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="requires org_id and site_id"):
                await discover(
                    {"board_url": "https://app.mokahr.com/social-recruitment/zte/47588"},
                    client,
                )


class TestCanHandle:
    async def test_parses_social_recruitment_url(self):
        result = await can_handle("https://app.mokahr.com/social-recruitment/zte/47588")
        assert result == {"org_id": "zte", "site_id": 47588}

    async def test_parses_campus_apply_url(self):
        result = await can_handle("https://app.mokahr.com/campus_apply/high-flyer/4605")
        assert result == {"org_id": "high-flyer", "site_id": 4605}

    async def test_rejects_unrelated_url(self):
        assert await can_handle("https://example.com/careers") is None
