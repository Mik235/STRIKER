import sys

sys.path.append("../..")

import asyncio
import logging
from dataclasses import asdict
from functools import partial
from json import dumps, loads

import config
from websockets import server
from websockets.exceptions import ConnectionClosed

from messages import commands, events
from messages.broker import Broker, MessageError
from messages.bus import MessageBus
from messages.deco import handler, listener
from shared.log import logging_config

logging_config(config.DEBUG)
log = logging.getLogger(__name__)


async def ws_connection_handler(websocket: server.WebSocketServerProtocol, queue: asyncio.Queue):
    async def send(c, d):
        await websocket.send(dumps([c, d]))

    auth = websocket.request_headers.get("Authorization", None)
    if auth is None or auth not in config.TOKENS:
        await websocket.close(code=1008, reason="Missing or invalid token")

    command: commands.RequestRecording
    fut: asyncio.Future = None

    try:
        async for message in websocket:
            action, data = loads(message)

            if action == "request":
                log.info("Waiting for recording job to give to client...")
                command, fut = await queue.get()
                data = asdict(command)
                await send("record", data)

            elif action == "success":
                log.info("Client reported successful recording")
                fut.set_result(events.RecorderSuccess(**data))
                fut = None

            elif action == "failure":
                reason = data["reason"]
                log.info("Client reported failed recording: %s", reason)
                fut.set_exception(MessageError(reason))
                fut = None

    except ConnectionClosed as exc:
        if fut:
            fut.set_exception(exc)


@handler(commands.RequestRecording)
async def on_recording_request(
    command: commands.RequestRecording, broker: Broker, queue: asyncio.Queue
):
    fut = asyncio.Future()
    await queue.put((command, fut))

    result = await fut
    await broker.publish(result)


async def main():
    logging.getLogger("aiormq").setLevel(logging.INFO)
    # logging.getLogger("websockets").setLevel(logging.INFO)

    queue = asyncio.Queue()

    bus = MessageBus(dependencies=dict(queue=queue))
    broker = Broker(bus)
    bus.add_dependencies(broker=broker)
    bus.register_decos()
    await broker.start(config.RABBITMQ_HOST, prefetch_count=0)

    await server.serve(
        ws_handler=partial(ws_connection_handler, queue=queue),
        host="localhost",
        port=9191,
    )


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_forever()
