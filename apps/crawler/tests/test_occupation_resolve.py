"""Tests for occupation_resolve.match_occupation.

Coverage focuses on the 22+ new sub-role slugs added in #3358 plus the
precision-bug prunes (Financial Analyst, Systems Engineer, etc.).
"""

from __future__ import annotations

import pytest

from src.core.occupation_resolve import match_occupation


class TestSiteReliabilityEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Site Reliability Engineer",
            "Senior SRE",
            "SRE - Production Systems",
            "Production Engineer",
            "Infrastructure Reliability Engineer",
        ],
    )
    def test_resolves_sre(self, title: str):
        assert match_occupation(title) == "site-reliability-engineer"


class TestPlatformEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Platform Engineer",
            "Senior Platform Engineer",
            "Developer Experience Engineer",
            "Internal Platform Engineer at Stripe",
            "DevEx Engineer",
        ],
    )
    def test_resolves_platform(self, title: str):
        assert match_occupation(title) == "platform-engineer"


class TestCloudEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Cloud Engineer",
            "AWS Engineer",
            "Senior Azure Engineer",
            "Cloud Operations Engineer",
            "CloudOps Engineer",
        ],
    )
    def test_resolves_cloud(self, title: str):
        assert match_occupation(title) == "cloud-engineer"


class TestAnalyticsEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Analytics Engineer",
            "Senior Analytics Engineer",
            "dbt Engineer",
            "Business Intelligence Engineer",
            "Data Modeling Engineer",
        ],
    )
    def test_resolves_analytics(self, title: str):
        assert match_occupation(title) == "analytics-engineer"


class TestMLOpsEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "MLOps Engineer",
            "MLOps Engineer at OpenAI",
            "ML Platform Engineer",
            "AI Infrastructure Engineer",
            "Machine Learning Operations Engineer",
        ],
    )
    def test_resolves_mlops(self, title: str):
        assert match_occupation(title) == "mlops-engineer"


class TestComputerVisionEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Computer Vision Engineer",
            "CV Engineer",
            "Senior Perception Engineer",
            "3D Vision Engineer",
            "Image Processing Engineer",
        ],
    )
    def test_resolves_cv(self, title: str):
        assert match_occupation(title) == "computer-vision-engineer"


class TestNLPEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "NLP Engineer",
            "Natural Language Processing Engineer",
            "LLM Engineer",
            "Conversational AI Engineer",
            "Speech Recognition Engineer",
        ],
    )
    def test_resolves_nlp(self, title: str):
        assert match_occupation(title) == "nlp-engineer"

    def test_language_engineer_moves_from_annotator(self):
        """Language Engineer used to alias data-annotator; now nlp-engineer."""
        assert match_occupation("Language Engineer") == "nlp-engineer"


class TestApplicationSecurityEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Application Security Engineer",
            "AppSec Engineer",
            "Senior AppSec Engineer",
            "Product Security Engineer",
            "DevSecOps Engineer",
        ],
    )
    def test_resolves_appsec(self, title: str):
        assert match_occupation(title) == "application-security-engineer"


class TestFirmwareEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Firmware Engineer",
            "Senior Firmware Engineer",
            "Embedded Firmware Engineer",
            "Bootloader Engineer",
            "BIOS Engineer",
        ],
    )
    def test_resolves_firmware(self, title: str):
        assert match_occupation(title) == "firmware-engineer"


class TestHardwareEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Hardware Engineer",
            "Senior Hardware Engineer",
            "PCB Design Engineer",
            "Analog Design Engineer",
            "Digital Design Engineer",
        ],
    )
    def test_resolves_hardware(self, title: str):
        assert match_occupation(title) == "hardware-engineer"


class TestASICEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "ASIC Engineer",
            "Chip Design Engineer",
            "RTL Design Engineer",
            "Design Verification Engineer",
            "Physical Design Engineer",
        ],
    )
    def test_resolves_asic(self, title: str):
        assert match_occupation(title) == "asic-engineer"


class TestSDET:
    @pytest.mark.parametrize(
        "title",
        [
            "SDET",
            "Software Development Engineer in Test",
            "Senior SDET",
            "Test Automation Engineer",
            "QA Automation Engineer",
        ],
    )
    def test_resolves_sdet(self, title: str):
        assert match_occupation(title) == "sdet"


class TestCloudArchitect:
    @pytest.mark.parametrize(
        "title",
        [
            "Cloud Architect",
            "AWS Solutions Architect",
            "Azure Solutions Architect",
            "Multi-Cloud Architect",
            "GCP Architect",
        ],
    )
    def test_resolves_cloud_architect(self, title: str):
        assert match_occupation(title) == "cloud-architect"


class TestDataArchitect:
    @pytest.mark.parametrize(
        "title",
        [
            "Data Architect",
            "Data Platform Architect",
            "Big Data Architect",
            "Data Warehouse Architect",
            "Lakehouse Architect",
        ],
    )
    def test_resolves_data_architect(self, title: str):
        assert match_occupation(title) == "data-architect"


class TestTechLead:
    @pytest.mark.parametrize(
        "title",
        [
            "Tech Lead",
            "Technical Lead",
            "Lead Software Engineer",
            "Engineering Lead",
            "Team Lead Engineering",
        ],
    )
    def test_resolves_tech_lead(self, title: str):
        assert match_occupation(title) == "tech-lead"


class TestResearchScientist:
    @pytest.mark.parametrize(
        "title",
        [
            "Research Scientist",
            "AI Research Scientist",
            "ML Research Scientist",
            "Research Scientist - NLP",
            "Research Scientist - Computer Vision",
        ],
    )
    def test_resolves_research_scientist(self, title: str):
        assert match_occupation(title) == "research-scientist"


class TestAppliedScientist:
    @pytest.mark.parametrize(
        "title",
        [
            "Applied Scientist",
            "Senior Applied Scientist",
            "Applied Research Scientist",
            "Applied Machine Learning Scientist",
        ],
    )
    def test_resolves_applied_scientist(self, title: str):
        assert match_occupation(title) == "applied-scientist"


class TestQuantitativeAnalyst:
    @pytest.mark.parametrize(
        "title",
        [
            "Quantitative Analyst",
            "Quant Researcher",
            "Quant Developer",
            "Algorithmic Trader",
            "Quantitative Developer",
        ],
    )
    def test_resolves_quant(self, title: str):
        assert match_occupation(title) == "quantitative-analyst"


class TestBioinformatician:
    @pytest.mark.parametrize(
        "title",
        [
            "Bioinformatician",
            "Bioinformatics Scientist",
            "Computational Biologist",
            "Genomics Data Scientist",
            "Bioinformatics Engineer",
        ],
    )
    def test_resolves_bioinformatician(self, title: str):
        assert match_occupation(title) == "bioinformatician"


class TestRoboticsEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Robotics Engineer",
            "Robotics Software Engineer",
            "Motion Planning Engineer",
            "SLAM Engineer",
            "Autonomy Engineer",
        ],
    )
    def test_resolves_robotics(self, title: str):
        assert match_occupation(title) == "robotics-engineer"


class TestDeveloperAdvocate:
    @pytest.mark.parametrize(
        "title",
        [
            "Developer Advocate",
            "Senior Developer Advocate",
            "DevRel",
            "Developer Relations Engineer",
            "Technical Evangelist",
        ],
    )
    def test_resolves_devrel(self, title: str):
        assert match_occupation(title) == "developer-advocate"


class TestPerformanceEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Performance Engineer",
            "Performance Test Engineer",
            "Load Test Engineer",
            "Scalability Engineer",
            "Performance Analyst",
        ],
    )
    def test_resolves_performance(self, title: str):
        assert match_occupation(title) == "performance-engineer"


class TestProductEngineer:
    @pytest.mark.parametrize(
        "title",
        [
            "Product Engineer",
            "Product Software Engineer",
            "Product Developer",
            "Product Focused Engineer",
        ],
    )
    def test_resolves_product_engineer(self, title: str):
        assert match_occupation(title) == "product-engineer"


class TestCybersecurityAnalyst:
    @pytest.mark.parametrize(
        "title",
        [
            "Cybersecurity Analyst",
            "SOC Analyst",
            "Incident Response Analyst",
            "Threat Intelligence Analyst",
            "GRC Analyst",
        ],
    )
    def test_resolves_cybersec_analyst(self, title: str):
        assert match_occupation(title) == "cybersecurity-analyst"


class TestGeospatialAnalyst:
    @pytest.mark.parametrize(
        "title",
        [
            "Geospatial Analyst",
            "GIS Engineer",
            "GIS Analyst",
            "Cartographer",
            "Remote Sensing Analyst",
        ],
    )
    def test_resolves_geospatial(self, title: str):
        assert match_occupation(title) == "geospatial-analyst"


class TestUmbrellaParentsStillResolve:
    """Regression: existing umbrella parents must still resolve for backward compat."""

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Developer", "software-engineer"),
            ("Software Engineer", "software-engineer"),
            ("Software Developer", "software-engineer"),
            ("DevOps Engineer", "devops-engineer"),
            ("QA Engineer", "qa-engineer"),
            ("Data Engineer", "data-engineer"),
            ("Data Scientist", "data-scientist"),
            ("ML Engineer", "ml-engineer"),
            ("Machine Learning Engineer", "ml-engineer"),
            ("Security Engineer", "security-engineer"),
            ("Solutions Architect", "solutions-architect"),
            ("Embedded Engineer", "embedded-engineer"),
            ("Embedded Software Engineer", "embedded-engineer"),
            # Test that the remaining QA aliases still work
            ("Quality Assurance Engineer", "qa-engineer"),
            ("QA Tester", "qa-engineer"),
            # DevOps still catches the remaining alias
            ("Infrastructure Engineer", "devops-engineer"),
        ],
    )
    def test_umbrella_parent_resolves(self, title: str, expected: str):
        assert match_occupation(title) == expected


class TestEuropeanLocaleOccupationCoverage:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Inżynier oprogramowania", "software-engineer"),
            ("Analityk danych", "data-analyst"),
            ("Ingeniero de software", "software-engineer"),
            ("Analista de datos", "data-analyst"),
            ("Softwareontwikkelaar", "software-engineer"),
            ("Data-analist", "data-analyst"),
            ("Engenheiro de software", "software-engineer"),
            ("Analista de dados", "data-analyst"),
            ("Softwarový inženýr", "software-engineer"),
            ("Datový analytik", "data-analyst"),
            ("Mjukvaruingenjör", "software-engineer"),
            ("Dataanalytiker", "data-analyst"),
            ("Szoftvermérnök", "software-engineer"),
            ("Adatelemző", "data-analyst"),
            ("Inginer software", "software-engineer"),
            ("Analist de date", "data-analyst"),
            ("Софтуерен инженер", "software-engineer"),
            ("Анализатор на данни", "data-analyst"),
            ("Μηχανικός λογισμικού", "software-engineer"),
            ("Αναλυτής δεδομένων", "data-analyst"),
            ("Softwareingeniør", "software-engineer"),
            ("Dataanalytiker", "data-analyst"),
            ("Ohjelmistoinsinööri", "software-engineer"),
            ("Data-analyytikko", "data-analyst"),
            ("Softverski inženjer", "software-engineer"),
            ("Analitičar podataka", "data-analyst"),
            ("Softvérový inžinier", "software-engineer"),
            ("Dátový analytik", "data-analyst"),
            ("Programski inženir", "software-engineer"),
            ("Podatkovni analitik", "data-analyst"),
            ("Programinės įrangos inžinierius", "software-engineer"),
            ("Duomenų analitikas", "data-analyst"),
            ("Programmatūras inženieris", "software-engineer"),
            ("Datu analītiķis", "data-analyst"),
            ("Tarkvarainsener", "software-engineer"),
            ("Andmeanalüütik", "data-analyst"),
        ],
    )
    def test_resolves_requested_eu_locale_titles(self, title: str, expected: str):
        assert match_occupation(title) == expected

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Inzynier Jakosci", "quality-manager"),
            ("Desarrollador/a Senior Salesforce_Platform Event", "software-engineer"),
            ("PLC Programátor (m/ž)", "automation-engineer"),
            ("Projektový inženýr mechanik (m/ž)", "mechanical-engineer"),
            ("Karbantartó Mérnök", "maintenance-technician"),
            ("Inginer grupuri electrogene si DSI - telecom & data centers", "electrical-engineer"),
        ],
    )
    def test_resolves_native_engineering_samples(self, title: str, expected: str):
        assert match_occupation(title) == expected

    @pytest.mark.parametrize(
        "title",
        [
            "Desarrollador de negocio",
            "Programador CNC",
            "Inżynier sprzedaży",
        ],
    )
    def test_broad_native_terms_do_not_match_software(self, title: str):
        assert match_occupation(title) != "software-engineer"


class TestPrunedAliasesNoLongerMatch:
    """Negative tests for Phase 1 alias prunes."""

    def test_financial_analyst_no_longer_data_analyst(self):
        """Financial Analyst was pruned from data-analyst aliases (#3358)."""
        # The standalone string and example variants should not resolve to data-analyst.
        assert match_occupation("Financial Analyst") is None
        assert match_occupation("Financial Analyst - Treasury") is None
        assert match_occupation("FinOps Analyst") is None

    def test_systems_engineer_no_longer_network_engineer(self):
        """Systems Engineer was pruned from network-engineer aliases (#3358)."""
        # Should NOT resolve to network-engineer in any of these forms.
        assert match_occupation("Systems Engineer") != "network-engineer"
        assert match_occupation("Systems Engineer (Hardware Test)") != "network-engineer"
        assert match_occupation("Senior Systems Engineer") != "network-engineer"

    def test_hardware_engineer_no_longer_embedded(self):
        """Hardware Engineer was promoted to a root slug; no longer embedded."""
        # Now resolves to its own slug, not embedded-engineer.
        assert match_occupation("Hardware Engineer") == "hardware-engineer"

    def test_verification_engineer_now_asic(self):
        """Verification Engineer was pruned from embedded; now an ASIC alias."""
        assert match_occupation("Verification Engineer") == "asic-engineer"


class TestEdgeCases:
    def test_empty_string(self):
        assert match_occupation("") is None

    def test_no_match(self):
        assert match_occupation("Random title that should not match anything") is None
