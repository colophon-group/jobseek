from __future__ import annotations

import base64
import csv
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from src.core.monitors import BoardGoneError, DiscoveredJob, mokahr
from src.core.monitors.mokahr import can_handle, discover

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_GEELY_SOCIAL_PARTITIONS = [
    {
        "board_url": "https://autojob.geely.com/social-recruitment/geely/102042",
        "site_id": 102042,
    },
    {
        "board_url": "https://app.mokahr.com/social-recruitment/geely/102003",
        "site_id": 102003,
    },
    {
        "board_url": "https://job.geelytech.com/social-recruitment/geely/96001",
        "site_id": 96001,
    },
    {
        "board_url": "https://job.geelycv.com/social-recruitment/geely/94066",
        "site_id": 94066,
    },
]


def _aes_encrypt(plain: bytes, key: str, iv: str) -> str:
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()

    cipher = Cipher(algorithms.AES(key.encode("ascii")), modes.CBC(iv.encode("ascii")))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode("ascii")


def _spa_html(
    iv: str,
    *,
    org_id: str = "zte",
    site_id: int = 47588,
    site_type: str = "social",
) -> str:
    init_data = {
        "aesIv": iv,
        "siteId": str(site_id),
        "org": {
            "id": org_id,
            "siteId": site_id,
            "type": site_type,
        },
        "jobsGroupedByLocation": [
            {"id": "深圳市", "label": "深圳市", "cityId": 440300},
        ],
    }
    init_value = json.dumps(init_data, ensure_ascii=False).replace('"', "&quot;")
    return f'<input id="init-data" type="hidden" value="{init_value}">'


def _encrypted_jobs(
    jobs: list[dict],
    key: str,
    iv: str,
    *,
    total: int | None = None,
    org_id: str = "zte",
    success: bool = True,
    code: int = 0,
    msg: str = "成功",
) -> dict:
    payload = json.dumps(
        {
            "success": success,
            "code": code,
            "msg": msg,
            "data": {
                "jobStats": {"orgId": org_id, "total": len(jobs) if total is None else total},
                "jobs": jobs,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return {"data": _aes_encrypt(payload, key, iv), "necromancer": key}


def _raw_job(job_id: str, **overrides) -> dict:
    raw = {
        "id": job_id,
        "orgId": "zte",
        "status": "open",
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
            assert body["needStat"] is True
            if body["offset"] == 0:
                jobs = [_raw_job("one"), _raw_job("two", title="算法工程师")]
            elif body["offset"] == 2:
                jobs = [_raw_job("three", title="测试工程师")]
            else:
                jobs = []
            return httpx.Response(
                200,
                json=_encrypted_jobs(jobs, key, iv, total=3),
                request=request,
            )

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
            "provider_id": "one",
            "provider_site_id": 47588,
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

    async def test_explicit_shutdown_page_is_board_gone(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html><head><title>当前网页已关停</title></head></html>",
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/campus-recruitment/ixmetals/140496",
            "metadata": {"org_id": "ixmetals", "site_id": 140496},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BoardGoneError, match="explicitly shut down") as exc:
                await discover(board, client)

        assert exc.value.url == board["board_url"]

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
                json=_encrypted_jobs(
                    [_raw_job("one"), _raw_job("two")],
                    key,
                    iv,
                    total=3,
                ),
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
            with pytest.raises(ValueError, match="org_id|site_id"):
                await discover(
                    {"board_url": "https://app.mokahr.com/social-recruitment/zte/47588"},
                    client,
                )

    @pytest.mark.parametrize(
        ("init_kwargs", "message"),
        [
            ({"org_id": "other"}, "organisation"),
            ({"site_id": 999}, "site identity"),
            ({"site_type": "camp"}, "site type"),
        ],
    )
    async def test_authenticates_exact_spa_identity(self, init_kwargs, message):
        iv = "de7c21ed8d6f50fe"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_spa_html(iv, **init_kwargs),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match=message):
                await discover(board, client)

    async def test_rejects_unsuccessful_encrypted_envelope(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            return httpx.Response(
                200,
                json=_encrypted_jobs(
                    [],
                    key,
                    iv,
                    total=0,
                    success=False,
                    code=703015,
                    msg="site closed",
                ),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="rejected the request"):
                await discover(board, client)

    async def test_advertised_jobs_cannot_collapse_to_healthy_zero(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            return httpx.Response(
                200,
                json=_encrypted_jobs([], key, iv, total=2),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="empty page before its total"):
                await discover(board, client)

    async def test_explicit_zero_requires_two_converged_authenticated_reads(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"
        post_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_count
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            post_count += 1
            return httpx.Response(
                200,
                json=_encrypted_jobs([], key, iv, total=0),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover(board, client) == []
        assert post_count == 2

    async def test_nonconverged_zero_fails_closed(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"
        post_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_count
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            post_count += 1
            jobs = [] if post_count == 1 else [_raw_job("appeared")]
            return httpx.Response(
                200,
                json=_encrypted_jobs(jobs, key, iv, total=len(jobs)),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="zero inventory did not converge"):
                await discover(board, client)

    async def test_malformed_later_page_cannot_return_a_healthy_partial_inventory(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(mokahr, "_PAGE_SIZE", 2)
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            body = json.loads(request.content)
            if body["offset"] == 0:
                return httpx.Response(
                    200,
                    json=_encrypted_jobs(
                        [_raw_job("one"), _raw_job("two")],
                        key,
                        iv,
                        total=3,
                    ),
                    request=request,
                )
            return httpx.Response(200, json={"malformed": True}, request=request)

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="missing encryption fields"):
                await discover(board, client)

    @pytest.mark.parametrize("second_total", [3, 4])
    async def test_duplicate_or_changing_pagination_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        second_total: int,
    ):
        monkeypatch.setattr(mokahr, "_PAGE_SIZE", 2)
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            body = json.loads(request.content)
            jobs = [_raw_job("one"), _raw_job("two")] if body["offset"] == 0 else [_raw_job("two")]
            return httpx.Response(
                200,
                json=_encrypted_jobs(
                    jobs,
                    key,
                    iv,
                    total=3 if body["offset"] == 0 else second_total,
                ),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        expected = "repeated provider ID" if second_total == 3 else "total changed"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match=expected):
                await discover(board, client)

    async def test_returns_only_explicitly_open_jobs(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"
        rows = [
            _raw_job("open"),
            _raw_job("closed", status="closed"),
            _raw_job("paused", status="pause"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_spa_html(iv), request=request)
            return httpx.Response(
                200,
                json=_encrypted_jobs(rows, key, iv, total=3),
                request=request,
            )

        board = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(board, client)
        assert [job.metadata["provider_id"] for job in jobs] == ["open"]

    async def test_partition_union_deduplicates_by_provider_id_in_config_order(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"
        primary_rows = [
            _raw_job("shared", orgId="geely", title="Primary title"),
            _raw_job("primary-only", orgId="geely"),
        ]
        child_rows = [
            _raw_job("shared", orgId="geely", title="Contextual child title"),
            _raw_job("child-only", orgId="geely"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                site_id = 102042 if request.url.host == "autojob.geely.com" else 96123
                return httpx.Response(
                    200,
                    text=_spa_html(iv, org_id="geely", site_id=site_id),
                    request=request,
                )
            body = json.loads(request.content)
            rows = child_rows if body["siteId"] == 102042 else primary_rows
            return httpx.Response(
                200,
                json=_encrypted_jobs(rows, key, iv, org_id="geely"),
                request=request,
            )

        board = {
            "board_url": "https://job.geely.com/social-recruitment/geely/96123",
            "metadata": {
                "org_id": "geely",
                "site_id": 96123,
                "partitions": [
                    {
                        "board_url": ("https://autojob.geely.com/social-recruitment/geely/102042"),
                        "site_id": 102042,
                    }
                ],
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(board, client)

        by_id = {job.metadata["provider_id"]: job for job in jobs}
        assert set(by_id) == {"shared", "primary-only", "child-only"}
        assert by_id["shared"].title == "Primary title"
        assert by_id["shared"].url.startswith("https://job.geely.com/")
        assert by_id["child-only"].url.startswith("https://autojob.geely.com/")

    async def test_rejects_unbounded_or_contradictory_partitions(self):
        base = {
            "board_url": "https://app.mokahr.com/social-recruitment/zte/47588",
            "metadata": {"org_id": "zte", "site_id": 47588},
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(500, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            bad_route = json.loads(json.dumps(base))
            bad_route["metadata"]["partitions"] = [
                {
                    "board_url": "https://jobs.example.com/social-recruitment/zte/999",
                    "site_id": 1000,
                }
            ]
            with pytest.raises(ValueError, match="route identity"):
                await discover(bad_route, client)

            non_text_url = json.loads(json.dumps(base))
            non_text_url["metadata"]["partitions"] = [{"board_url": None, "site_id": 1000}]
            with pytest.raises(ValueError, match="board_url must be text"):
                await discover(non_text_url, client)

            too_many = json.loads(json.dumps(base))
            too_many["metadata"]["partitions"] = [
                {
                    "board_url": f"https://jobs{i}.example.com/social-recruitment/zte/{i + 1}",
                    "site_id": i + 1,
                }
                for i in range(mokahr._MAX_PARTITIONS)
            ]
            with pytest.raises(ValueError, match="at most"):
                await discover(too_many, client)


class TestCanHandle:
    async def test_parses_social_recruitment_url(self):
        result = await can_handle("https://app.mokahr.com/social-recruitment/zte/47588")
        assert result == {"org_id": "zte", "site_id": 47588}

    async def test_parses_campus_apply_url(self):
        result = await can_handle("https://app.mokahr.com/campus_apply/high-flyer/4605")
        assert result == {"org_id": "high-flyer", "site_id": 4605}

    async def test_rejects_unrelated_url(self):
        assert await can_handle("https://example.com/careers") is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://app.mokahr.com/social-recruitment/zte/47588",
            "https://user@app.mokahr.com/social-recruitment/zte/47588",
            "https://app.mokahr.com:8443/social-recruitment/zte/47588",
            "https://example.com/social-recruitment/zte/47588",
        ],
    )
    async def test_rejects_untrusted_or_unverified_route(self, url: str):
        assert await can_handle(url) is None

    async def test_detects_custom_domain_from_spa_bootstrap(self):
        iv = "de7c21ed8d6f50fe"
        init_data = {
            "aesIv": iv,
            "org": {"id": "pradagroup"},
            "siteId": "151069",
        }
        init_value = json.dumps(init_data).replace('"', "&quot;")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=f'<input id="init-data" value="{init_value}">',
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobschina.prada.cn/", client)

        assert result == {"org_id": "pradagroup", "site_id": 151069}

    async def test_custom_domain_route_requires_matching_bootstrap_ids(self):
        init_data = {
            "aesIv": "de7c21ed8d6f50fe",
            "org": {"id": "different-org"},
            "siteId": 999,
        }
        init_value = json.dumps(init_data).replace('"', "&quot;")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=f'<input id="init-data" value="{init_value}">',
                request=request,
            )

        url = "https://jobs.example.com/social-recruitment/pradagroup/151069"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(url, client) is None

    async def test_custom_domain_route_accepts_matching_bootstrap_ids(self):
        init_data = {
            "aesIv": "de7c21ed8d6f50fe",
            "org": {"id": "pradagroup"},
            "siteId": 151069,
        }
        init_value = json.dumps(init_data).replace('"', "&quot;")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=f'<input id="init-data" value="{init_value}">',
                request=request,
            )

        url = "https://jobs.example.com/social-recruitment/pradagroup/151069"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(url, client)

        assert result == {"org_id": "pradagroup", "site_id": 151069}

    async def test_custom_domain_listing_uses_same_origin(self):
        iv = "de7c21ed8d6f50fe"
        key = "1234567890abcdef"
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=_spa_html(iv, org_id="pradagroup", site_id=151069),
                    request=request,
                )
            return httpx.Response(
                200,
                json=_encrypted_jobs(
                    [_raw_job("custom", orgId="pradagroup")],
                    key,
                    iv,
                    org_id="pradagroup",
                ),
                request=request,
            )

        board = {
            "board_url": "https://jobschina.prada.cn/",
            "metadata": {"org_id": "pradagroup", "site_id": 151069},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(board, client)

        assert seen_urls == [
            "https://jobschina.prada.cn/social-recruitment/pradagroup/151069",
            "https://jobschina.prada.cn/api/outer/ats-apply/website/jobs/v2",
        ]
        assert jobs[0].url == (
            "https://jobschina.prada.cn/social-recruitment/pradagroup/151069#/job/custom"
        )


def test_geely_social_board_uses_branded_primary_and_bounded_official_partitions():
    with _BOARDS_PATH.open(newline="") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == "geely-holding-group-careers"
        )

    assert row["board_url"] == "https://job.geely.com/social-recruitment/geely/96123"
    config = json.loads(row["monitor_config"])
    assert config == {
        "org_id": "geely",
        "site_id": 96123,
        "partitions": _GEELY_SOCIAL_PARTITIONS,
    }
