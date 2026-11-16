"""
Voice router — translated phone calls via Twilio.

Endpoints:
- POST /voice/translated-call: Initiate a two-leg translated call (auth required).
- GET /voice/twiml/stream: Return TwiML for Media Streams (called by Twilio).
- WS /voice/ws/media: WebSocket for Twilio Media Streams (bidirectional).
"""

from __future__ import annotations

import json
import re
import secrets
from xml.sax.saxutils import quoteattr

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import get_current_user
from app.services import call_guard
from app.services.twilio_media_bridge import (
    AudioProcessor,
    active_session_count,
    get_session_meta,
    register_leg,
    register_session,
    stream_token_ok,
    unregister_leg,
)
from app.services.usage import QuotaExceededError, check_quota

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

_LEGS = ("user", "dest")
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


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
    if cfg.is_production and not cfg.live_translate_public_url:
        # Without it Twilio callbacks can't be signature-checked behind the proxy.
        raise HTTPException(status_code=503, detail="Calls unavailable: LIVE_TRANSLATE_PUBLIC_URL is not set.")

    # Abuse guards: paid plan, real (non-premium) numbers, per-user limits.
    call_guard.require_call_plan(user, cfg)
    user_phone = call_guard.check_destination(body.user_phone, cfg, "user_phone")
    dest_phone = call_guard.check_destination(body.dest_phone, cfg, "dest_phone")
    own = call_guard.normalize_phone(cfg.twilio_phone_number)
    if user_phone == dest_phone or own in (user_phone, dest_phone):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_phone", "message": "Use two different numbers, neither of them ours."},
        )
    if active_session_count(user["id"]) >= cfg.twilio_max_concurrent_calls:
        raise HTTPException(
            status_code=429,
            detail={"error": "call_in_progress", "limit": cfg.twilio_max_concurrent_calls,
                    "message": "Finish your current call before starting another."},
        )

    # The session id is the only thing guarding the media WebSocket, so make it
    # unguessable (was 8 hex characters of a uuid).
    session_id = secrets.token_urlsafe(18)
    try:
        # Each call is metered per chunk; refuse to dial when nothing is left.
        await check_quota(user["id"], "stt", 1)
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=402,
            detail={"error": "quota_exceeded", "event_type": exc.event_type, "used": exc.used, "limit": exc.limit},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    call_guard.rate_limiter.check_and_record(user["id"], cfg.twilio_max_calls_per_hour)
    base_url = _get_base_url()
    register_session(session_id, user["id"], body.target_lang)

    from twilio.rest import Client

    client = Client(cfg.twilio_account_sid, cfg.twilio_auth_token)

    twiml_user = f"{base_url}/api/v1/voice/twiml/stream?leg=user&session={session_id}"
    twiml_dest = f"{base_url}/api/v1/voice/twiml/stream?leg=dest&session={session_id}"

    try:
        call_user = client.calls.create(
            url=twiml_user,
            to=user_phone,
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
            to=dest_phone,
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
async def ws_media(
    websocket: WebSocket,
    leg: str = Query(...),
    session: str = Query(...),
    token: str | None = Query(default=None),
) -> None:
    """WebSocket handler for Twilio bidirectional Media Streams."""
    await websocket.accept()
    meta = get_session_meta(session) if leg in _LEGS else None
    # The per-leg token only exists in the signed TwiML, so only Twilio has it.
    if not meta or not stream_token_ok(session, leg, token):
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


@router.api_route("/twiml/stream", methods=["GET", "POST"])
async def twiml_stream(
    request: Request,
    leg: str = Query(...),
    session: str = Query(...),
) -> Response:
    """Return TwiML that connects the call to our Media Streams WebSocket.

    Twilio fetches call URLs with POST by default; both methods are signature-checked.
    """
    await call_guard.verify_twilio_request(request, get_settings())
    if leg not in _LEGS or not _SESSION_RE.match(session) or get_session_meta(session) is None:
        # Query values went straight into the XML; reject anything that is not ours.
        raise HTTPException(status_code=404, detail="Unknown call session")
    base_url = _get_base_url()
    wss_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    token = get_session_meta(session)["stream_tokens"][leg]
    stream_url = f"{wss_url}/api/v1/voice/ws/media?leg={leg}&session={session}&token={token}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url={quoteattr(stream_url)} />
  </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


def _get_base_url() -> str:
    """Return base URL for webhooks (from config or env)."""
    cfg = get_settings()
    url = cfg.live_translate_public_url or ""
    if not url:
        import os

        url = os.environ.get("LIVE_TRANSLATE_PUBLIC_URL", "https://your-backend.com")
    return url.rstrip("/")
