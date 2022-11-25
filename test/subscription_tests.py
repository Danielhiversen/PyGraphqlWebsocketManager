"""
Tests for graphql_subscription_manager
"""
import asyncio
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
import aiohttp  # noqa: E402

from graphql_subscription_manager import SubscriptionManager

DEMO_TOKEN = "5K4MVS-OjfWhK_4yrjOlFe1F6kJXPVf7eQYggo8ebAE"
WS_URL = "wss://websocket-api.tibber.com/v1-beta/gql/subscriptions"
DEMO_HOME_ID = "96a14971-525a-4420-aae9-e5aedaa129ff"

LIVE_SUBSCRIBE = """
            subscription{
              liveMeasurement(homeId:"%s"){
                lastMeterConsumption
                signalStrength
                timestamp
            }
           }
        """

root = logging.getLogger()
root.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)


def callback_sub(data: dict) -> None:
            print(data)


async def mainloop():
    print("Waiting")


async def create_subscription() -> tuple:
    manager = SubscriptionManager(
            {"token": DEMO_TOKEN},
            WS_URL,
            "subscription_tests"
        )
    manager.start()

    if manager is not None:
            sub_id = await manager.subscribe(
                LIVE_SUBSCRIBE % DEMO_HOME_ID, callback_sub
            )

    return manager, sub_id


async def run_subscription():
    await create_subscription()
    
    while True:
        await asyncio.sleep(1)
        logging.debug("Sleeping...")


@pytest.mark.asyncio
async def test_subscription():
    
    manager, sub_id = await create_subscription()

    assert manager.is_running
    assert sub_id == "0"
    
    await manager.stop()

    assert not manager.is_running

if __name__ == "__main__":
    asyncio.run(run_subscription())