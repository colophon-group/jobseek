from __future__ import annotations

from src.shared.throttle import _delay_for_host


class TestDelayForHost:
    def test_known_ats_host_greenhouse(self):
        assert _delay_for_host("boards-api.greenhouse.io") == 0.5

    def test_known_ats_host_lever(self):
        assert _delay_for_host("api.lever.co") == 0.5

    def test_known_ats_host_ashby(self):
        assert _delay_for_host("api.ashbyhq.com") == 0.5

    def test_known_ats_host_smartrecruiters(self):
        assert _delay_for_host("api.smartrecruiters.com") == 0.5

    def test_known_ats_host_hireology(self):
        assert _delay_for_host("api.hireology.com") == 0.5

    def test_known_ats_host_rippling(self):
        assert _delay_for_host("api.rippling.com") == 0.5

    def test_unknown_host(self):
        assert _delay_for_host("example.com") == 2.0

    def test_another_unknown_host(self):
        assert _delay_for_host("careers.bigcorp.com") == 2.0

    def test_unknown_host_default(self):
        assert _delay_for_host("random-domain.org") == 2.0
