"""Subscription manager for Graph QL websocket."""

import asyncio
import json
import logging
import socket
import sys
from time import time

import pkg_resources
import websockets

_LOGGER = logging.getLogger(__name__)

STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"

try:
    VERSION = pkg_resources.require("graphql-subscription-manager")[0].version
except Exception:  # pylint: disable=broad-except
    VERSION = "dev"


class SubscriptionManager:
    """Subscription manager."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, init_payload, url):
        """Create resources for websocket communication."""
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.get_event_loop()
        self.subscriptions = {}
        self._url = url
        self._state = None
        self.websocket = None
        self._retry_timer = None
        self._client_task = None
        self._wait_time_before_retry = 1
        self._session_id = 0
        self._init_payload = init_payload
        self._user_agent = "Python/{0[0]}.{0[1]} PyGraphqlWebsocketManager/{1}".format(
            sys.version_info, VERSION)

    def start(self):
        """Start websocket."""
        _LOGGER.debug("Start state %s.", self._state)
        if self._state == STATE_RUNNING:
            return
        self._state = STATE_STARTING
        self._cancel_client_task()
        self._client_task = self.loop.create_task(self.running())
        for subscription in self.subscriptions.copy():
            callback, sub_query = self.subscriptions.pop(
                subscription, (None, None))
            _LOGGER.debug("Removed, %s", subscription)
            if callback is None:
                continue
            _LOGGER.debug("Add subscription %s", callback)
            self.loop.create_task(self.subscribe(sub_query, callback))

    @property
    def is_running(self):
        """Return if client is running or not."""
        return self._state == STATE_RUNNING

    async def running(self):
        """Start websocket connection."""
        _LOGGER.debug("Starting")

        try:
            await self._init_web_socket()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Failed to connect. Reconnecting... ", exc_info=True)
            self._state = STATE_STOPPED
            self.retry()
            return

        _LOGGER.debug("Running")

        self._state = STATE_RUNNING

        try:
            await self._running_loop()
        except Exception:  # pylint: disable=broad-except
            if self._state != STATE_STOPPED:
                _LOGGER.error("Connection error", exc_info=True)
            else:
                _LOGGER.debug("Connection error", exc_info=True)
        finally:
            _LOGGER.debug("Closing running task.")
            if self._state == STATE_STOPPED:
                return
            _LOGGER.debug("Reconnecting")
            self._state = STATE_STOPPED
            self.retry()

    async def stop(self, timeout=100):
        """Close websocket connection."""
        _LOGGER.debug("Stopping client.")
        start_time = time()
        self._cancel_retry_timer()

        for subscription_id in self.subscriptions.copy().keys():
            _LOGGER.debug("Sending unsubscribe: %s", subscription_id)
            await self.unsubscribe(subscription_id)

        while (timeout > 0 and self.websocket is not None
               and not self.subscriptions
               and (time() - start_time) < timeout / 2):
            await asyncio.sleep(0.1)

        self._state = STATE_STOPPED
        await self._close_websocket()

        while (timeout > 0 and self.websocket is not None
               and not self.websocket.closed
               and (time() - start_time) < timeout):

            await asyncio.sleep(0.1)

        self._cancel_client_task()
        _LOGGER.debug("Server connection is stopped")

    def retry(self):
        """Retry to connect to websocket."""
        _LOGGER.debug("Retry, state: %s", self._state)
        if self._state in [STATE_STARTING, STATE_RUNNING]:
            _LOGGER.debug("Skip retry since state: %s", self._state)
            return
        _LOGGER.debug("Cancel retry timer")
        self._cancel_retry_timer()
        self._state = STATE_STARTING
        _LOGGER.debug("Restart")
        self._retry_timer = self.loop.call_later(self._wait_time_before_retry,
                                                 self.start)
        _LOGGER.debug("Reconnecting to server in %i seconds.",
                      self._wait_time_before_retry)

    async def subscribe(self, sub_query, callback, timeout=3):
        """Add a new subscription."""
        current_session_id = self._session_id
        self._session_id += 1
        subscription = {
            "query": sub_query,
            "type": "subscription_start",
            "id": current_session_id,
        }
        json_subscription = json.dumps(subscription)
        self.subscriptions[current_session_id] = (callback, sub_query)

        start_time = time()
        while time() - start_time < timeout:
            if (self.websocket is None or not self.websocket.open
                    or not self._state == STATE_RUNNING):
                await asyncio.sleep(1)
                continue

            await self.websocket.send(json_subscription)
            _LOGGER.debug("New subscription %s", current_session_id)
            return current_session_id
        return None

    async def unsubscribe(self, subscription_id):
        """Unsubscribe."""
        if self.websocket is None or not self.websocket.open:
            _LOGGER.warning("Websocket is closed.")
            return
        await self.websocket.send(
            json.dumps({
                "id": subscription_id,
                "type": "subscription_end"
            }))
        if self.subscriptions and subscription_id in self.subscriptions:
            self.subscriptions.pop(subscription_id)

    async def _close_websocket(self):
        if self.websocket is None:
            return
        try:
            await self.websocket.close()
        finally:
            self.websocket = None

    async def _process_msg(self, msg):
        """Process received msg."""
        result = json.loads(msg)

        msg_type = result.get("type", "")
        if msg_type == "init_fail":
            _LOGGER.debug(msg_type)
            return

        subscription_id = result.get("id")
        if subscription_id is None:
            return

        callback, _ = self.subscriptions.get(subscription_id, (None, None))
        if callback is None:
            _LOGGER.debug("Unknown id %s.", subscription_id)
            return

        if msg_type == "complete":
            _LOGGER.debug("Unsubscribe %s successfully.", subscription_id)
            return

        data = result.get("payload")
        if data is None:
            return

        callback(data)

    def _cancel_retry_timer(self):
        if self._retry_timer is None:
            return
        try:
            self._retry_timer.cancel()
        finally:
            self._retry_timer = None

    def _cancel_client_task(self):
        if self._client_task is None:
            return
        try:
            self._client_task.cancel()
        finally:
            self._client_task = None


    async def _init_web_socket(self):
        self.websocket = await asyncio.wait_for(
            websockets.connect(
                self._url,
                subprotocols=["graphql-subscriptions"],
                extra_headers={"User-Agent": self._user_agent},
            ),
            timeout=30,
        )
        await self.websocket.send(
            json.dumps({
                "type": "init",
                "payload": self._init_payload,
            }))

    async def _running_loop(self):
        k = 0
        while self._state == STATE_RUNNING:
            try:
                msg = await asyncio.wait_for(self.websocket.recv(),
                                             timeout=30)
            except asyncio.TimeoutError:
                if k > 60:
                    _LOGGER.debug("No data, reconnecting.")
                    _LOGGER.debug("Reconnecting")
                    self._state = STATE_STOPPED
                    self.retry()
                    return

                _LOGGER.debug("No websocket data, sending a ping.")
                try:
                    pong_waiter = await self.websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=3)
                except asyncio.TimeoutError:
                    _LOGGER.error(
                        "No response to ping in 3 seconds, reconnecting."
                    )
                    _LOGGER.debug("Reconnecting")
                    self._state = STATE_STOPPED
                    self.retry()

                k += 1

            else:
                k = 0
                await self._process_msg(msg)
