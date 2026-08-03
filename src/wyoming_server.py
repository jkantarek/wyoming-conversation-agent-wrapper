import argparse
import asyncio
import logging

from wyoming.event import Event, Eventable
from wyoming.info import Describe, Info, Attribution, IntentModel, IntentProgram, SelectProgram
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.asr import Transcript
from wyoming.intent import Recognize, Intent
from wyoming.handle import Handled
from dataclasses import dataclass

@dataclass
class Text(Eventable):
    text: str
    
    @staticmethod
    def is_type(event_type: str) -> bool:
        return event_type == "text"
        
    def event(self) -> Event:
        return Event(type="text", data={"text": self.text})
        
    @staticmethod
    def from_event(event: Event) -> "Text":
        return Text(text=event.data.get("text", ""))

from .middleware import Middleware
from .omp_subprocess import OmpSubprocess

logger = logging.getLogger(__name__)

class WyomingOMPHandler(AsyncEventHandler):
    def __init__(
        self,
        *,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        middleware: Middleware,
        omp_subprocess: OmpSubprocess,
        **kwargs,
    ):
        # Pass reader/writer to AsyncEventHandler (library expects them positionally)
        super().__init__(kwargs.get("reader"), kwargs.get("writer"))
        self.cli_args = cli_args
        self.wyoming_info = wyoming_info
        self.middleware = middleware
        self.omp_subprocess = omp_subprocess
        logger.info("WyomingOMPHandler initialized")

    async def handle_event(self, event: Event) -> bool:
        logger.info(f"WYOMING INGRESS: event_type={event.type!r}")

        try:
            if Describe.is_type(event.type):
                logger.info("WYOMING: Describe request, sending server info")
                await self.write_event(self.wyoming_info.event())
                return True

            text = None
            is_recognize = False

            if SelectProgram.is_type(event.type):
                select = SelectProgram.from_event(event)
                logger.info(f"WYOMING: SelectProgram(name={select.name!r})")
                return True

            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event)
                text = transcript.text
            elif Recognize.is_type(event.type):
                recognize = Recognize.from_event(event)
                text = recognize.text
                is_recognize = True

            if text is not None:
                logger.info(f"WYOMING: Received query: {text!r}")

                # 1. Check Middleware (Fast path)
                middleware_response = self.middleware.process_transcript(text)

                if middleware_response:
                    logger.info("WYOMING: Middleware intercepted query")
                    response_text = middleware_response
                else:
                    # 2. Forward to OMP Subprocess
                    logger.info("WYOMING: Forwarding to OMP subprocess")
                    response_text = await self.omp_subprocess.query(text)

                # Respond with Handled — HA's read loop bypasses intent resolution for Handled,
                # just sets speech directly. Intent requires a registered intent type.
                logger.info(f"WYOMING: Response text: {response_text!r}")
                response_event = Handled(text=response_text).event()
                logger.info(f"WYOMING EGRESS: Handled(text={response_text!r})")
                await self.write_event(response_event)

                return True

            logger.info(f"WYOMING: No handler for event type {event.type!r}, passing through")
            return True

        except Exception:
            logger.error(f"WYOMING: Error handling event type={event.type!r}", exc_info=True)
            raise

    async def write_event(self, event: Event):
        """Override to log every outgoing event."""
        logger.debug(f"WYOMING EGRESS: writing event_type={event.type!r}")
        try:
            await super().write_event(event)
        except Exception:
            logger.error(f"WYOMING EGRESS: Failed to write event_type={event.type!r}", exc_info=True)

async def run_wyoming_server(host: str, port: int, middleware: Middleware, omp_subprocess: OmpSubprocess):
    wyoming_info = Info(
        intent=[
            IntentProgram(
                name="omp",
                attribution=Attribution(name="Oh My Pi", url="https://github.com/oh-my-pi"),
                installed=True,
                description="OMP Wyoming Bridge - LLM-powered intent recognition",
                version="1.0.0",
                models=[
                    IntentModel(
                        name="omp-bridge",
                        languages=["en"],
                        attribution=Attribution(name="Oh My Pi", url="https://github.com/oh-my-pi"),
                        installed=True,
                        description="OMP Wyoming Bridge model",
                        version="1.0.0",
                    )
                ],
            )
        ]
    )

    server = AsyncServer.from_uri(f"tcp://{host}:{port}")
    logger.info(f"Starting Wyoming server on tcp://{host}:{port}")

    # Start OMP Subprocess lazily — don't block server startup on OMP init.
    # The subprocess starts in the background; queries trigger connection on demand.
    asyncio.create_task(omp_subprocess.start())

    # The wyoming AsyncServer.run is a coroutine that handles incoming connections
    # We pass a handler factory — called for each new TCP connection
    connection_count = 0

    def handler_factory(*args, **kwargs):
        nonlocal connection_count
        connection_count += 1
        logger.info(f"WYOMING: New connection #{connection_count}")
        # args = (reader, writer) from library; merge into kwargs
        if args:
            kwargs["reader"] = args[0]
            if len(args) > 1:
                kwargs["writer"] = args[1]
        kwargs["wyoming_info"] = wyoming_info
        kwargs["cli_args"] = argparse.Namespace()
        kwargs["middleware"] = middleware
        kwargs["omp_subprocess"] = omp_subprocess
        return WyomingOMPHandler(**kwargs)
    
    await server.run(handler_factory)
