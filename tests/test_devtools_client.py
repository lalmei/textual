import asyncio
import json
from asyncio import Queue
from datetime import datetime

import pytest
import time_machine
from aiohttp.web_ws import WebSocketResponse
from rich.console import ConsoleDimensions
from rich.panel import Panel

from textual.devtools import make_aiohttp_app
from textual.devtools_client import DevtoolsClient

TIMESTAMP = 1649166819


@pytest.fixture
async def server(aiohttp_server, unused_tcp_port):
    server = await aiohttp_server(make_aiohttp_app(), port=unused_tcp_port)
    yield server
    await server.close()


@pytest.fixture
async def devtools(aiohttp_client, server):
    client = await aiohttp_client(server)
    devtools = DevtoolsClient(address=client.host, port=client.port)
    await devtools.connect()
    yield devtools
    await devtools.disconnect()
    await client.close()


def test_devtools_client_initialize_defaults():
    devtools = DevtoolsClient()
    assert devtools.url == "ws://127.0.0.1:8081"


async def test_devtools_client_is_connected(devtools):
    assert devtools.is_connected


@time_machine.travel(datetime.fromtimestamp(TIMESTAMP))
async def test_devtools_log_places_encodes_and_queues_message(devtools):
    await devtools.cancel_log_queue_processing()
    devtools.log("Hello, world!")
    queued_log = await devtools.log_queue.get()
    queued_log_json = json.loads(queued_log)
    assert queued_log_json == {
        "payload": {
            "encoded_segments": "gASVQgAAAAAAAABdlCiMDHJpY2guc2VnbWVudJSMB1NlZ"
                                "21lbnSUk5SMDUhlbGxvLCB3b3JsZCGUTk6HlIGUaAOMAQqUTk6HlIGUZS4=",
            "line_number": 0,
            "path": "",
            "timestamp": TIMESTAMP,
        },
        "type": "client_log",
    }


@time_machine.travel(datetime.fromtimestamp(TIMESTAMP))
async def test_devtools_log_places_encodes_and_queues_many_logs_as_string(devtools):
    await devtools.cancel_log_queue_processing()
    devtools.log("hello", "world")
    queued_log = await devtools.log_queue.get()
    queued_log_json = json.loads(queued_log)
    assert queued_log_json == {
        "type": "client_log",
        "payload": {
            "timestamp": TIMESTAMP,
            "path": "",
            "line_number": 0,
            "encoded_segments": "gASVQAAAAAAAAABdlCiMDHJpY2guc2VnbWVudJSMB1NlZ21lbnSUk5SMC2hlbGxvIHdvcmxklE5Oh5SBlGgDjAEKlE5Oh5SBlGUu",
        },
    }


async def test_devtools_log_spillover(devtools):
    # Give the devtools an intentionally small max queue size
    devtools.log_queue = Queue(maxsize=2)
    await devtools.cancel_log_queue_processing()

    # Force spillover of 2
    devtools.log(Panel("hello, world"))
    devtools.log("second message")
    devtools.log("third message")  # Discarded by rate-limiting
    devtools.log("fourth message")  # Discarded by rate-limiting

    assert devtools.spillover == 2

    # Consume log queue
    while not devtools.log_queue.empty():
        await devtools.log_queue.get()

    # Add another message now that we're under spillover threshold
    devtools.log("another message")
    await devtools.log_queue.get()

    # Ensure we're informing the server of spillover rate-limiting
    spillover_message = await devtools.log_queue.get()
    assert json.loads(spillover_message) == {"type": "client_spillover", "payload": {"spillover": 2}}


async def test_devtools_client_update_console_dimensions(devtools, server):
    server_websocket: WebSocketResponse = next(iter(server.app["websockets"]))
    # Send new server information from the server via the websocket
    server_info = {
        "type": "server_info",
        "payload": {
            "width": 123,
            "height": 456,
        },
    }
    await server_websocket.send_json(server_info)
    timer = 0
    poll_period = .1
    while True:
        if timer > 3:
            pytest.fail("The devtools client dimensions did not update")
        if devtools.console.size == ConsoleDimensions(123, 456):
            break
        await asyncio.sleep(.1)
        timer += poll_period