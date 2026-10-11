import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from google.genai import errors

from app.agent_runtime.llm import GeminiProvider, ProviderError
from tests.support import BAD, FakeSDK, GOOD, program, request


class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def test_environment_credentials_model_and_sdk_retry_configuration(self):
        sdk = FakeSDK()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-credential", "GOOGLE_API_KEY": "google-test-credential", "GEMINI_MODEL": "configured-model"}, clear=True), patch("app.agent_runtime.llm.genai.Client", return_value=sdk) as client:
            provider = GeminiProvider()
            self.assertEqual(provider.model, "configured-model")
            self.assertEqual(client.call_args.kwargs["api_key"], "gemini-test-credential")
            self.assertFalse(client.call_args.kwargs["vertexai"])
            self.assertEqual(client.call_args.kwargs["http_options"].retry_options.attempts, 1)
            await provider.close()
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "google-test-credential"}, clear=True), patch("app.agent_runtime.llm.genai.Client", return_value=FakeSDK()) as client:
            provider = GeminiProvider()
            self.assertEqual(provider.model, "gemini-3.8-flash")
            self.assertEqual(client.call_args.kwargs["api_key"], "google-test-credential")
            await provider.close()

    async def test_structured_generation_and_repair_use_async_sdk_and_fixed_scope(self):
        sdk = FakeSDK({"fixture": [BAD, GOOD]})
        provider = GeminiProvider(client=sdk)
        generated = await provider.generate(request())
        repaired = await provider.repair(request(), generated.source, "program.c:1:2: error: undeclared identifier")
        self.assertEqual(repaired.source, GOOD)
        self.assertEqual(sdk.calls[1]["source"], BAD)
        kwargs = sdk.aio.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["config"].response_mime_type, "application/json")
        self.assertIn("properties", kwargs["config"].response_json_schema)
        await provider.close()
        sdk.aio.aclose.assert_awaited_once()

    async def test_generation_pool_is_bounded_and_concurrent(self):
        sdk = FakeSDK(delay=.02)
        provider = GeminiProvider(client=sdk, max_concurrent=3)
        results = await asyncio.gather(*(provider.generate(request(str(n))) for n in range(8)))
        self.assertEqual(len(results), 8)
        self.assertEqual(sdk.peak, 3)
        self.assertEqual(len(sdk.calls), 8)

    async def test_rate_limits_and_server_errors_retry_only_within_limit(self):
        for code in (429, 503):
            with self.subTest(code=code), patch("app.agent_runtime.llm.random.uniform", return_value=0):
                sdk = FakeSDK({"fixture": [errors.ClientError(code, {"error": {"message": "private-secret"}}), GOOD]})
                self.assertEqual((await GeminiProvider(client=sdk).generate(request())).source, GOOD)
                self.assertEqual(len(sdk.calls), 2)
        sdk = FakeSDK({"fixture": [errors.ClientError(429, {}), errors.ClientError(429, {}), GOOD]})
        with patch("app.agent_runtime.llm.random.uniform", return_value=0), self.assertRaisesRegex(ProviderError, "^RATE_LIMITED$"):
            await GeminiProvider(client=sdk).generate(request())
        self.assertEqual(len(sdk.calls), 2)

    async def test_nonretryable_errors_hide_provider_details(self):
        sdk = FakeSDK({"fixture": [errors.ClientError(400, {"error": {"message": "private-secret"}})]})
        with self.assertRaisesRegex(ProviderError, "^PROVIDER_ERROR$"):
            await GeminiProvider(client=sdk).generate(request())
        self.assertEqual(len(sdk.calls), 1)

    async def test_http_transport_and_socket_failures_are_sanitized(self):
        for error, reason in ((httpx.ReadTimeout("private"), "PROVIDER_TIMEOUT"), (httpx.ConnectError("private"), "PROVIDER_UNAVAILABLE"), (OSError("private"), "PROVIDER_UNAVAILABLE")):
            sdk = FakeSDK({"fixture": [error]})
            with self.subTest(error=type(error).__name__), self.assertRaisesRegex(ProviderError, f"^{reason}$"):
                await GeminiProvider(client=sdk).generate(request())

    async def test_malformed_or_scope_expanding_responses_never_leave_service(self):
        for value in (None, "[]", "not-json", "x" * 524289, program().model_dump_json().replace('"compute"', '"other"')):
            sdk = FakeSDK()
            sdk.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(text=value))
            with self.subTest(kind=type(value)), self.assertRaisesRegex(ProviderError, "^MALFORMED_RESPONSE$"):
                await GeminiProvider(client=sdk).generate(request())

    async def test_timeouts_and_cancellation_release_provider_slots(self):
        sdk = FakeSDK(delay=.1)
        with self.assertRaisesRegex(ProviderError, "^PROVIDER_TIMEOUT$"):
            await GeminiProvider(client=sdk, timeout=.01).generate(request())
        provider = GeminiProvider(client=sdk, max_concurrent=1)
        task = asyncio.create_task(provider.generate(request()))
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        sdk.delay = 0
        self.assertEqual((await provider.generate(request())).source, GOOD)

    async def test_missing_credentials_and_bad_limits_fail_cleanly(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = GeminiProvider()
            with self.assertRaisesRegex(ProviderError, "^PROVIDER_NOT_CONFIGURED$"):
                await provider.generate(request())
            await provider.close()
        for change in ({"max_concurrent": 0}, {"attempts": 4}, {"timeout": 26}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                GeminiProvider(**change)
        with self.assertRaises(ValueError):
            await GeminiProvider(client=FakeSDK()).repair(request(), GOOD, "private path /home/person")


@unittest.skipUnless(os.getenv("JANUS_RUN_LIVE_GEMINI") == "1" and (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")), "Live Gemini explicitly opt-in; credentials required")
class LiveGeminiTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_structured_generation_without_compilation_or_execution(self):
        provider = GeminiProvider()
        try:
            output = await provider.generate(request("Return a standalone C11 program printing a JSON object with version 1 and value 42."))
            output.check_scope(request())
            self.assertIn("main", output.source)
        finally:
            await provider.close()
