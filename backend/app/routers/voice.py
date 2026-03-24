"""
Voice router — translated phone calls via Twilio.

Endpoints:
- POST /voice/translated-call: Initiate a two-leg translated call (auth required).
- GET /voice/twiml/stream: Return TwiML for Media Streams (called by Twilio).
- WS /voice/ws/media: WebSocket for Twilio Media Streams (bidirectional).
"""

from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import get_current_user
from app.services.twilio_media_bridge import (
    AudioProcessor,
    get_session_meta,
    register_leg,
    register_session,
    unregister_leg,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])


class TranslatedCallRequest(BaseModel):
    """Request to start a translated phone call."""

    user_phone: str
    dest_phone: str
    target_lang: str = "es"


@router.post("/translated-call")
async def start_translated_call(
    body: TranslatedCallRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Create two Twilio call legs and bridge them with translation."""
    cfg = get_settings()
    if not cfg.twilio_account_sid or not cfg.twilio_auth_token or not cfg.twilio_phone_number:
        raise HTTPException(
            status_code=503,
            detail="Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER.",
        )

    session_id = str(uuid.uuid4())[:8]
    base_url = _get_base_url()
    register_session(session_id, user["id"], body.target_lang)

    from twilio.rest import Client

    client = Client(cfg.twilio_account_sid, cfg.twilio_auth_token)

    twiml_user = f"{base_url}/api/v1/voice/twiml/stream?leg=user&session={session_id}"
    twiml_dest = f"{base_url}/api/v1/voice/twiml/stream?leg=dest&session={session_id}"

    try:
        call_user = client.calls.create(
            url=twiml_user,
            to=body.user_phone,
            from_=cfg.twilio_phone_number,
        )
        logger.info("Created user leg", call_sid=call_user.sid, session_id=session_id)
    except Exception as e:
        logger.exception("Twilio user leg failed", error=str(e))
        unregister_leg(session_id, "user")
        raise HTTPException(status_code=502, detail=f"Twilio error: {str(e)}") from e

    try:
        call_dest = client.calls.create(
            url=twiml_dest,
            to=body.dest_phone,
            from_=cfg.twilio_phone_number,
        )
        logger.info("Created dest leg", call_sid=call_dest.sid, session_id=session_id)
    except Exception as e:
        logger.warning("Twilio dest leg failed (user may answer first)", error=str(e))

    return {
        "session_id": session_id,
        "user_call_sid": call_user.sid,
        "message": "Both legs created. Answer both calls for real-time translation.",
    }


@router.websocket("/ws/media")
async def ws_media(websocket: WebSocket, leg: str = Query(...), session: str = Query(...)) -> None:
    """WebSocket handler for Twilio bidirectional Media Streams."""
    await websocket.accept()
    meta = get_session_meta(session)
    if not meta:
        await websocket.close(code=4004)
        return

    stream_sid: str | None = None
    processor: AudioProcessor | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            event = msg.get("event")

            if event == "connected":
                pass
            elif event == "start":
                stream_sid = (
                    msg.get("start", {}).get("streamSid")
                    or msg.get("streamSid")
                )
                if stream_sid and leg in ("user", "dest"):
                    register_leg(session, leg, websocket, stream_sid)
                    processor = AudioProcessor(
                        leg=leg,
                        session_id=session,
                        target_lang=meta["target_lang"],
                        user_id=meta["user_id"],
                    )
            elif event == "media" and processor:
                payload = (msg.get("media") or {}).get("payload")
                if payload:
                    await processor.add_chunk(payload)
            elif event == "stop":
                break
    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError as e:
        logger.warning("Invalid WS JSON", error=str(e))
    finally:
        if leg and session:
            unregister_leg(session, leg)
        if processor:
            await processor.drain()


@router.get("/twiml/stream")
async def twiml_stream(
    request: Request,
    leg: str = Query(...),
    session: str = Query(...),
) -> Response:
    """Return TwiML that connects the call to our Media Streams WebSocket."""
    base_url = _get_base_url()
    wss_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    stream_url = f"{wss_url}/api/v1/voice/ws/media?leg={leg}&session={session}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url}" />
  </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


def _get_base_url() -> str:
    """Return base URL for webhooks (from config or env)."""
    from app.config import get_settings

    cfg = get_settings()
    url = cfg.live_translate_public_url or ""
    if not url:
        import os

        url = os.environ.get("LIVE_TRANSLATE_PUBLIC_URL", "https://your-backend.com")
    return url.rstrip("/")
