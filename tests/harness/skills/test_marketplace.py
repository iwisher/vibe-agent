"""Tests for skill marketplace — discovery, search, and installation."""

import json
import tempfile

import pytest

from vibe.harness.skills.marketplace import (
    MarketplaceSearchResult,
    SkillListing,
    SkillMarketplace,
)


class TestSkillMarketplace:
    @pytest.fixture
    def sample_registry(self):
        return {
            "skills": [
                {
                    "name": "git-commit",
                    "version": "1.0.0",
                    "description": "Smart git commit messages",
                    "author": "alice",
                    "tags": ["git", "productivity"],
                    "rating": 4.5,
                    "downloads": 1000,
                },
                {
                    "name": "docker-lint",
                    "version": "2.0.0",
                    "description": "Lint Dockerfiles",
                    "author": "bob",
                    "tags": ["docker", "devops"],
                    "rating": 3.8,
                    "downloads": 500,
                },
                {
                    "name": "python-refactor",
                    "version": "1.5.0",
                    "description": "Refactor Python code",
                    "author": "alice",
                    "tags": ["python", "refactoring"],
                    "rating": 4.9,
                    "downloads": 2000,
                },
            ]
        }

    @pytest.fixture
    def marketplace(self, sample_registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_registry, f)
            f.flush()
            mp = SkillMarketplace(local_registry_path=f.name)
            # Load the registry synchronously (refresh is async but _load_local_registry is sync)
            mp._listings = mp._load_local_registry()
            return mp

    @pytest.mark.asyncio
    async def test_refresh_loads_local(self, marketplace):
        await marketplace.refresh()
        assert len(marketplace._listings) == 3

    def test_search_by_name(self, marketplace):
        result = marketplace.search(query="git")
        assert result.total == 1
        assert result.listings[0].name == "git-commit"

    def test_search_by_description(self, marketplace):
        result = marketplace.search(query="Dockerfile")
        assert result.total == 1
        assert result.listings[0].name == "docker-lint"

    def test_search_by_tag(self, marketplace):
        result = marketplace.search(tags=["python"])
        assert result.total == 1
        assert result.listings[0].name == "python-refactor"

    def test_search_by_author(self, marketplace):
        result = marketplace.search(author="alice")
        assert result.total == 2

    def test_search_sorts_by_rating(self, marketplace):
        result = marketplace.search()
        assert result.listings[0].name == "python-refactor"  # 4.9 rating
        assert result.listings[1].name == "git-commit"  # 4.5 rating

    def test_get_skill(self, marketplace):
        skill = marketplace.get_skill("git-commit")
        assert skill is not None
        assert skill.author == "alice"

    def test_get_skill_not_found(self, marketplace):
        assert marketplace.get_skill("nonexistent") is None

    def test_list_categories(self, marketplace):
        cats = marketplace.list_categories()
        assert "python" in cats
        assert cats["python"] == 1

    def test_list_authors(self, marketplace):
        authors = marketplace.list_authors()
        assert authors["alice"] == 2
        assert authors["bob"] == 1

    def test_mark_installed(self, marketplace):
        marketplace.mark_installed("git-commit")
        assert marketplace.get_skill("git-commit").installed is True

    def test_mark_uninstalled(self, marketplace):
        marketplace.mark_installed("git-commit")
        marketplace.mark_uninstalled("git-commit")
        assert marketplace.get_skill("git-commit").installed is False

    def test_search_installed_only(self, marketplace):
        marketplace.mark_installed("git-commit")
        result = marketplace.search(installed_only=True)
        assert result.total == 1
        assert result.listings[0].name == "git-commit"

    @pytest.mark.asyncio
    async def test_install_from_registry(self, marketplace):
        success, msg = await marketplace.install_from_registry("git-commit")
        assert success is True
        assert "installed" in msg

    @pytest.mark.asyncio
    async def test_install_not_found(self, marketplace):
        success, msg = await marketplace.install_from_registry("nonexistent")
        assert success is False
        assert "not found" in msg

    @pytest.mark.asyncio
    async def test_install_already_installed(self, marketplace):
        await marketplace.install_from_registry("git-commit")
        success, msg = await marketplace.install_from_registry("git-commit")
        assert success is True
        assert "already installed" in msg
