"""Tests for technology_resolve.match_technologies."""

from __future__ import annotations

import pytest

from src.core.technology_resolve import match_technologies


class TestBasicMatches:
    """Technologies that should match reliably."""

    @pytest.mark.parametrize(
        "text,expected_slug",
        [
            ("Experience with Python and Django", "python"),
            ("Experience with Python and Django", "django"),
            ("Proficiency in TypeScript required", "typescript"),
            ("Strong SQL skills", "sql"),
            ("AWS cloud infrastructure", "aws"),
            ("Docker and Kubernetes experience", "docker"),
            ("Docker and Kubernetes experience", "kubernetes"),
            ("React.js frontend development", "react"),
            ("Building APIs with Node.js", "nodejs"),
            ("PostgreSQL database management", "postgresql"),
            ("Experience with MongoDB", "mongodb"),
            ("CI/CD with Jenkins", "jenkins"),
            ("Terraform infrastructure as code", "terraform"),
            ("Apache Kafka streaming", "kafka"),
            ("TensorFlow or PyTorch for ML", "tensorflow"),
            ("TensorFlow or PyTorch for ML", "pytorch"),
            ("Figma design tools", "figma"),
        ],
    )
    def test_match_present(self, text: str, expected_slug: str):
        assert expected_slug in match_technologies(text)


class TestCaseSensitive:
    """Technologies with case-sensitive matching."""

    def test_react_uppercase(self):
        assert "react" in match_technologies("We use React for our frontend")

    def test_react_lowercase_excluded(self):
        """Lowercase 'react' is a verb, not the framework."""
        assert "react" not in match_technologies("We need someone who can react quickly")

    def test_swift_uppercase(self):
        assert "swift" in match_technologies("iOS development with Swift")

    def test_rust_uppercase(self):
        assert "rust" in match_technologies("Systems programming in Rust")

    def test_helm_uppercase(self):
        assert "helm" in match_technologies("Deploy with Helm charts")

    def test_helm_lowercase_excluded(self):
        assert "helm" not in match_technologies("at the helm of the organization")

    def test_bootstrap_uppercase(self):
        assert "bootstrap-css" in match_technologies("Frontend with Bootstrap and jQuery")

    def test_bootstrap_lowercase_excluded(self):
        assert "bootstrap-css" not in match_technologies("help bootstrap a new team")

    def test_flask_uppercase(self):
        assert "flask" in match_technologies("Python web development with Flask")

    def test_docker_uppercase(self):
        assert "docker" in match_technologies("Containerization with Docker")

    def test_spark_uppercase(self):
        assert "spark" in match_technologies("Data processing with Spark and Hadoop")

    def test_angular_uppercase(self):
        assert "angular" in match_technologies("Frontend with Angular framework")


class TestFalsePositivePrevention:
    """Known ambiguous terms that must NOT match."""

    def test_go_bare_word_excluded(self):
        """Bare 'Go' matches too many non-tech contexts."""
        assert "golang" not in match_technologies("Go to market strategy")
        assert "golang" not in match_technologies("Go further in your career")
        assert "golang" not in match_technologies("Go ahead and apply")

    def test_golang_matches(self):
        assert "golang" in match_technologies("Experience with Golang required")

    def test_oracle_bare_excluded(self):
        """Bare 'Oracle' is ambiguous (company vs product)."""
        assert "oracle-db" not in match_technologies("Oracle is a great company")

    def test_oracle_compound_matches(self):
        assert "oracle-db" in match_technologies("Oracle Cloud infrastructure")
        assert "oracle-db" in match_technologies("Oracle HCM integration")
        assert "oracle-db" in match_technologies("Experience with PL/SQL")

    def test_workday_bare_excluded(self):
        """Bare 'Workday' is ambiguous (company vs product mention)."""
        assert "workday-tech" not in match_technologies("Apply through Workday")

    def test_workday_compound_matches(self):
        assert "workday-tech" in match_technologies("Workday HCM administration")
        assert "workday-tech" in match_technologies("Workday Integration development")

    def test_spring_bare_excluded(self):
        """Bare 'Spring' matches seasonal references."""
        result = match_technologies("Spring 2026 internship program")
        assert "spring-boot" not in result
        assert "spring-framework" not in result

    def test_spring_boot_matches(self):
        assert "spring-boot" in match_technologies("Java with Spring Boot microservices")

    def test_spring_framework_compound_matches(self):
        assert "spring-framework" in match_technologies("Experience with Spring MVC")
        assert "spring-framework" in match_technologies("Spring Security configuration")

    def test_chef_bare_excluded(self):
        """Bare 'Chef' could mean a cook."""
        assert "chef" not in match_technologies("We're looking for a Chef")

    def test_chef_infra_matches(self):
        assert "chef" in match_technologies("Configuration management with Chef Infra")


class TestJavaBoundary:
    """Java must not match JavaScript."""

    def test_java_alone(self):
        result = match_technologies("Strong Java experience required")
        assert "java" in result

    def test_javascript_does_not_trigger_java(self):
        result = match_technologies("JavaScript and TypeScript only")
        assert "javascript" in result
        # Java pattern uses \bJava\b which won't match inside "JavaScript"
        assert "java" not in result


class TestHTMLStripping:
    """HTML content is stripped before matching."""

    def test_html_tags_stripped(self):
        html = "<p>We use <strong>Python</strong> and <em>React</em></p>"
        result = match_technologies(html)
        assert "python" in result
        assert "react" in result

    def test_html_attributes_not_matched(self):
        html = '<a href="https://react.dev">Our tech stack</a>'
        # "react" appears in URL attribute — after stripping, only "Our tech stack" remains
        result = match_technologies(html)
        assert "react" not in result


class TestEdgeCases:
    def test_empty_string(self):
        assert match_technologies("") == []

    def test_none_input(self):
        assert match_technologies(None) == []

    def test_no_technologies(self):
        assert match_technologies("We are looking for a friendly team player") == []

    def test_multiple_technologies(self):
        text = "Python, TypeScript, React, PostgreSQL, Docker, AWS"
        result = match_technologies(text)
        assert "python" in result
        assert "typescript" in result
        assert "react" in result
        assert "postgresql" in result
        assert "docker" in result
        assert "aws" in result

    def test_no_duplicates(self):
        text = "Python Python Python everywhere"
        result = match_technologies(text)
        assert result.count("python") == 1

    def test_cplusplus(self):
        assert "cplusplus" in match_technologies("C++ development experience")

    def test_csharp(self):
        assert "csharp" in match_technologies("C# and .NET development")
        assert "dotnet" in match_technologies("C# and .NET development")

    def test_dotnet_variants(self):
        assert "dotnet" in match_technologies("ASP.NET Core web API")
        assert "dotnet" in match_technologies(".NET Framework 4.8")

    def test_r_language_only_with_context(self):
        """R alone is too ambiguous — only match with programming context."""
        assert "r-lang" in match_technologies("R programming and statistics")
        assert "r-lang" not in match_technologies("R&D department")

    def test_k8s_alias(self):
        assert "kubernetes" in match_technologies("K8s cluster management")

    def test_sap_uppercase(self):
        assert "sap" in match_technologies("SAP integration and configuration")

    def test_excel(self):
        assert "excel" in match_technologies("Advanced Excel and data analysis")

    def test_excel_lowercase_excluded(self):
        """Lowercase 'excel' is a verb."""
        assert "excel" not in match_technologies("We excel at building great products")

    def test_matlab(self):
        assert "matlab" in match_technologies("MATLAB simulations and modeling")

    def test_autocad(self):
        assert "autocad" in match_technologies("AutoCAD and SolidWorks proficiency")
        assert "solidworks" in match_technologies("AutoCAD and SolidWorks proficiency")

    def test_sharepoint(self):
        assert "sharepoint" in match_technologies("SharePoint administration")


class TestCompoundOnlyPatterns:
    """Technologies that only match compound forms to avoid company-name FPs."""

    def test_cloudflare_bare_excluded(self):
        """Bare 'Cloudflare' matches company name, not technology."""
        assert "cloudflare" not in match_technologies("At Cloudflare, we are on a mission")

    def test_cloudflare_product_matches(self):
        assert "cloudflare" in match_technologies("Deploy to Cloudflare Workers")
        assert "cloudflare" in match_technologies("Cloudflare CDN configuration")

    def test_salesforce_bare_excluded(self):
        assert "salesforce" not in match_technologies("At Salesforce, we believe in equality")

    def test_salesforce_product_matches(self):
        assert "salesforce" in match_technologies("Salesforce CRM administration")
        assert "salesforce" in match_technologies("SFDC integration experience")
        assert "salesforce" in match_technologies("SOQL query optimization")
