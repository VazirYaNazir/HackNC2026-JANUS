import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent_runtime.llm import GeminiProvider, ProviderError
from app.agent_runtime.similarity import normalized, suggestions
from tests.support import program, request


class SimilarityTests(unittest.IsolatedAsyncioTestCase):
    def store(self, description="Print Foo"):
        return SimpleNamespace(programs=AsyncMock(return_value=[{"id": "valid-id", "request_json": request(description).model_dump_json(), "program_json": program(request(description)).model_dump_json()}]))

    async def test_exact_match_needs_no_provider_and_preserves_case_parameters_and_whitespace(self):
        provider = SimpleNamespace(similar=AsyncMock())
        result = await suggestions(self.store(), provider, "identity", request(" Print Foo "))
        self.assertEqual(result["matches"][0]["match"], "exact")
        provider.similar.assert_not_awaited()
        self.assertNotEqual(normalized("Print Foo"), normalized("Print foo"))
        self.assertNotEqual(normalized("Print 'a b'"), normalized("Print 'a  b'"))
        self.assertNotEqual(normalized("Set volume 10"), normalized("Set volume 100"))

    async def test_semantic_check_sends_only_scoped_descriptions_and_never_executes(self):
        provider = SimpleNamespace(similar=AsyncMock(return_value=["valid-id"]))
        store = self.store("Print the temperature")
        req = request("Show the temperature")
        result = await suggestions(store, provider, "identity", req)
        self.assertEqual(result["matches"][0]["match"], "semantic")
        store.programs.assert_awaited_once_with("identity", req)
        self.assertEqual(provider.similar.await_args.args[1], [{"id": "valid-id", "description": "Print the temperature"}])
        self.assertNotIn("source", json.dumps(result))

    async def test_empty_or_unrelated_candidates_skip_provider_and_errors_allow_new_generation(self):
        provider = SimpleNamespace(similar=AsyncMock(side_effect=ProviderError("RATE_LIMITED")))
        empty = self.store(); empty.programs.return_value = []
        self.assertEqual((await suggestions(empty, provider, "id", request()))["matches"], [])
        self.assertEqual((await suggestions(self.store("zzzzzz"), provider, "id", request("aaaaaa")))["matches"], [])
        provider.similar.assert_not_awaited()
        self.assertFalse((await suggestions(self.store(), provider, "id", request("Print foo")))["checked"])

    async def test_provider_uses_structured_async_output_and_rejects_invented_ids(self):
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock()), aclose=AsyncMock()))
        provider = GeminiProvider(client=client)
        for ids in (["valid-id"], ["invented"], ["valid-id", "valid-id"], "bad"):
            client.aio.models.generate_content.return_value = SimpleNamespace(text=json.dumps({"matching_ids": ids}))
            if ids == ["valid-id"]:
                self.assertEqual(await provider.similar("Show temperature", [{"id": "valid-id", "description": "Print temperature"}]), ids)
                self.assertEqual(client.aio.models.generate_content.call_args.kwargs["config"].response_mime_type, "application/json")
            else:
                with self.assertRaisesRegex(ProviderError, "MALFORMED_RESPONSE"):
                    await provider.similar("Show temperature", [{"id": "valid-id", "description": "Print temperature"}])
        self.assertEqual(await provider.similar("Anything", []), [])
        self.assertEqual(await provider.similar("Anything", [{"id": str(n)} for n in range(6)]), [])
        await provider.close()
