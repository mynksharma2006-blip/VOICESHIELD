import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import api


class FakeWebSocket:
    def __init__(self, scheme: str = "wss", authorization: str = "") -> None:
        self.scope = {"scheme": scheme}
        self.headers = {"authorization": authorization}
        self.close = AsyncMock()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent_messages.append(message)

    async def receive(self) -> dict[str, object]:
        return self.messages.pop(0)



class CallStreamAuthorizationTests(unittest.TestCase):
    def test_rejects_when_server_token_is_not_configured(self) -> None:
        websocket = FakeWebSocket()
        with patch.object(api, "CALL_STREAM_TOKEN", ""):
            asyncio.run(api.stream_call_audio(websocket))
        websocket.close.assert_awaited_once()
        self.assertEqual(websocket.close.await_args.kwargs["code"], 1013)

    def test_rejects_non_secure_websocket(self) -> None:
        websocket = FakeWebSocket(scheme="ws", authorization="Bearer secret")
        with (
            patch.object(api, "CALL_STREAM_TOKEN", "secret"),
            patch.object(api, "_stream_audio", new_callable=AsyncMock) as stream,
        ):
            asyncio.run(api.stream_call_audio(websocket))
        websocket.close.assert_awaited_once()
        self.assertEqual(websocket.close.await_args.kwargs["code"], 1008)
        stream.assert_not_awaited()

    def test_rejects_invalid_bearer_token(self) -> None:
        websocket = FakeWebSocket(authorization="Bearer wrong")
        with (
            patch.object(api, "CALL_STREAM_TOKEN", "secret"),
            patch.object(api, "_stream_audio", new_callable=AsyncMock) as stream,
        ):
            asyncio.run(api.stream_call_audio(websocket))
        websocket.close.assert_awaited_once()
        self.assertEqual(websocket.close.await_args.kwargs["code"], 4401)
        stream.assert_not_awaited()

    def test_accepts_authorized_wss_and_uses_call_specific_validation(self) -> None:
        websocket = FakeWebSocket(authorization="bEaReR secret")
        with (
            patch.object(api, "CALL_STREAM_TOKEN", "secret"),
            patch.object(api, "CALL_AUDIO_LABELS_VALIDATED", False),
            patch.object(api, "_stream_audio", new_callable=AsyncMock) as stream,
        ):
            asyncio.run(api.stream_call_audio(websocket))
        stream.assert_awaited_once_with(
            websocket,
            "speakerphone microphone room audio; not direct cellular-call audio",
            False,
        )

    def test_existing_web_stream_keeps_its_existing_source_description(self) -> None:
        websocket = FakeWebSocket(scheme="ws")
        with patch.object(api, "_stream_audio", new_callable=AsyncMock) as stream:
            asyncio.run(api.stream_audio(websocket))
        stream.assert_awaited_once_with(
            websocket,
            "microphone input; not direct phone-call audio",
        )

    def test_unvalidated_call_stream_score_is_always_inconclusive(self) -> None:
        class Detector:
            model_id = "test-model"
            window_samples = 8_000

            def score(self, samples) -> float:
                return -5.9

        websocket = FakeWebSocket()
        websocket.query_params = {"sample_rate": "16000"}
        websocket.sent_messages = []
        websocket.messages = [
            {"type": "websocket.receive", "bytes": b"\x00\x00" * 8_000},
            {"type": "websocket.disconnect"},
        ]
        with (
            patch.object(api.app.state, "detector", Detector(), create=True),
            patch.object(api, "INCONCLUSIVE_MARGIN", 0.25),
        ):
            asyncio.run(
                api._stream_audio(
                    websocket,
                    "speakerphone microphone room audio; not direct cellular-call audio",
                    False,
                )
            )

        score_message = websocket.sent_messages[-1]
        self.assertEqual(score_message["status"], "scored")
        self.assertEqual(score_message["label"], "inconclusive")
        self.assertEqual(score_message["score_raw"], -5.9)
        self.assertIn("not been validated", score_message["reason"])

    def test_authorized_call_stream_completes_and_returns_cant_tell(self) -> None:
        class Detector:
            model_id = "test-model"
            window_samples = 8_000

            def score(self, samples) -> float:
                return -5.9

        websocket = FakeWebSocket(authorization="Bearer test-token")
        websocket.query_params = {"sample_rate": "16000"}
        websocket.sent_messages = []
        websocket.messages = [
            {"type": "websocket.receive", "bytes": b"\x00\x00" * 8_000},
            {"type": "websocket.disconnect"},
        ]
        with (
            patch.object(api, "CALL_STREAM_TOKEN", "test-token"),
            patch.object(api, "CALL_AUDIO_LABELS_VALIDATED", False),
            patch.object(api.app.state, "detector", Detector(), create=True),
            patch.object(api, "INCONCLUSIVE_MARGIN", 0.25),
        ):
            asyncio.run(api.stream_call_audio(websocket))

        self.assertTrue(websocket.accepted)
        self.assertEqual(websocket.sent_messages[0]["status"], "ready")
        score_message = websocket.sent_messages[-1]
        self.assertEqual(score_message["status"], "scored")
        self.assertEqual(score_message["label"], "inconclusive")
        self.assertEqual(score_message["source"], "rolling speakerphone microphone room audio; not direct cellular-call audio")
        self.assertIn("not been validated", score_message["reason"])

    def test_demo_button_runs_simulated_stream_test(self) -> None:
        result = asyncio.run(api.call_stream_demo_test())

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["result"], "Can’t tell")
        self.assertEqual(len(result["steps"]), 3)
        self.assertIn("does not test", result["notice"])


if __name__ == "__main__":
    unittest.main()
