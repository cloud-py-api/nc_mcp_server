"""Integration tests for Unified Search tools against a real Nextcloud instance."""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


class TestListSearchProviders:
    @pytest.mark.asyncio
    async def test_returns_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_search_providers")
        providers = json.loads(result)
        assert isinstance(providers, list)
        assert providers

    @pytest.mark.asyncio
    async def test_files_provider_exists(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_search_providers")
        providers = json.loads(result)
        ids = [p["id"] for p in providers]
        assert "files" in ids

    @pytest.mark.asyncio
    async def test_provider_has_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_search_providers")
        providers = json.loads(result)
        files_provider = next(p for p in providers if p["id"] == "files")
        assert "id" in files_provider
        assert "name" in files_provider
        assert "app" in files_provider
        assert files_provider["app"] == "files"

    @pytest.mark.asyncio
    async def test_files_provider_has_filters(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_search_providers")
        providers = json.loads(result)
        files_provider = next(p for p in providers if p["id"] == "files")
        assert "filters" in files_provider
        assert "term" in files_provider["filters"]

    @pytest.mark.asyncio
    async def test_multiple_providers_present(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_search_providers")
        providers = json.loads(result)
        ids = {p["id"] for p in providers}
        assert len(ids) >= 5


class TestUnifiedSearch:
    @pytest.mark.asyncio
    async def test_search_files(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file("mcp-test-suite/search-target.txt", "findable content")
        result = await nc_mcp.call("unified_search", provider="files", term="search-target")
        data = json.loads(result)
        assert "entries" in data
        assert "has_more" in data
        assert "cursor" in data
        assert "provider" in data
        assert data["provider"] == "Files"

    @pytest.mark.asyncio
    async def test_search_result_fields(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file("mcp-test-suite/search-fields.txt", "content")
        result = await nc_mcp.call("unified_search", provider="files", term="search-fields")
        data = json.loads(result)
        assert data["entries"]
        entry = data["entries"][0]
        assert "title" in entry
        assert "subline" in entry

    @pytest.mark.asyncio
    async def test_search_returns_matching_results(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file("mcp-test-suite/unique-search-xyz987.txt", "data")
        result = await nc_mcp.call("unified_search", provider="files", term="unique-search-xyz987")
        data = json.loads(result)
        titles = [e["title"] for e in data["entries"]]
        assert any("unique-search-xyz987" in t for t in titles)

    @pytest.mark.asyncio
    async def test_search_no_results(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "unified_search", provider="files", term="nonexistent-file-that-does-not-exist-zzz999"
        )
        data = json.loads(result)
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_search_with_limit(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("unified_search", provider="files", term="pagtest", limit=2)
        data = json.loads(result)
        assert len(data["entries"]) <= 2

    @pytest.mark.asyncio
    async def test_search_pagination(self, nc_mcp: McpTestHelper) -> None:
        result1 = await nc_mcp.call("unified_search", provider="files", term="pagtest", limit=2)
        data1 = json.loads(result1)
        if not data1["has_more"]:
            pytest.skip("Not enough pagtest files for pagination test")
        cursor = str(data1["cursor"])

        result2 = await nc_mcp.call("unified_search", provider="files", term="pagtest", limit=2, cursor=cursor)
        data2 = json.loads(result2)
        assert data2["entries"]
        titles1 = {e["title"] for e in data1["entries"]}
        titles2 = {e["title"] for e in data2["entries"]}
        assert not titles1.intersection(titles2), "Pages should not overlap"

    @pytest.mark.asyncio
    async def test_search_nonexistent_provider_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("unified_search", provider="nonexistent-provider-xyz", term="test")

    @pytest.mark.asyncio
    async def test_search_with_filter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "unified_search",
            provider="files",
            term="pagtest",
            filters='{"mime": "text"}',
        )
        data = json.loads(result)
        assert isinstance(data["entries"], list)

    @pytest.mark.asyncio
    async def test_search_contacts_provider(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("unified_search", provider="contacts", term="admin")
        data = json.loads(result)
        assert isinstance(data["entries"], list)
        assert data["provider"] == "Contacts"

    @pytest.mark.asyncio
    async def test_search_talk_conversations(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("unified_search", provider="talk-conversations", term="a")
        data = json.loads(result)
        assert isinstance(data["entries"], list)


class TestSearchPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list_providers(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_search_providers")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_allows_search(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("unified_search", provider="files", term="test")
        data = json.loads(result)
        assert isinstance(data["entries"], list)
