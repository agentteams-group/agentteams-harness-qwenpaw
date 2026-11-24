"""Persistent maintenance failures must be visible in channel health."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nio.responses import KeysUploadError

from agentteams_matrix.channel import AgentTeamsMatrixChannel


def _client():
    return SimpleNamespace(
        access_token="test-token", olm=object(), should_upload_keys=True,
        should_query_keys=False, should_claim_keys=False,
        keys_upload=AsyncMock(return_value=KeysUploadError("upload unavailable", "M_UNKNOWN")),
        send_to_device_messages=AsyncMock(return_value=[]),
    )


def test_repeated_error_responses_change_health_and_recovery_resets_it():
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel.enabled = True
    channel.homeserver = "https://matrix.test"
    channel.encryption = True
    channel._client = _client()

    async def scenario():
        for count in range(1, 7):
            await channel._e2ee_maintenance()
            assert channel._e2ee_maintainer.consecutive_failures == count
            health = await channel.health_check()
            assert health["status"] == ("unhealthy" if count >= 5 else "healthy")
        assert "upload unavailable" in health["detail"]
        channel._client.keys_upload.return_value = None
        await channel._e2ee_maintenance()
        assert channel._e2ee_maintainer.consecutive_failures == 0
        assert (await channel.health_check())["status"] == "healthy"

        channel._client = _client()
        await channel._e2ee_maintenance()
        assert channel._e2ee_maintainer.consecutive_failures == 1
        channel.encryption = False
        await channel._e2ee_maintenance()
        assert channel._e2ee_maintainer is None
        assert channel._e2ee_maintenance_client is None

    asyncio.run(scenario())
