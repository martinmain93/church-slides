"""Lead-sheet import: read a notation PDF into a reference for its song.

Parsing runs off the event loop — a long PDF takes a second or two — and
never errors for a PDF that simply is not music: it answers
`is_lead_sheet=False` with the reason, and the app's importer treats the
file as an ordinary chart.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging

from websockets.asyncio.server import ServerConnection

from auto_presenter.leadsheet import READER_VERSION, parse_lead_sheet
from auto_presenter.protocol import ParseLeadSheetRequest, ParseLeadSheetResponse
from auto_presenter.server_state import _send

log = logging.getLogger(__name__)


async def _handle_parse_lead_sheet(ws: ServerConnection, msg: ParseLeadSheetRequest) -> None:
    try:
        pdf = base64.b64decode(msg.pdf_base64, validate=True)
    except (binascii.Error, ValueError) as err:
        await _send(
            ws, ParseLeadSheetResponse(id=msg.id, is_lead_sheet=False, reason=f"not base64: {err}")
        )
        return
    sheet = await asyncio.to_thread(parse_lead_sheet, pdf, msg.file_name)
    if sheet.is_lead_sheet:
        log.info(
            "lead sheet %r: %d bars (%d unreadable), key %s, CCLI %s",
            msg.file_name,
            sheet.bar_count,
            sheet.problem_bars,
            sheet.key,
            sheet.ccli,
        )
    await _send(
        ws,
        ParseLeadSheetResponse(
            id=msg.id,
            is_lead_sheet=sheet.is_lead_sheet,
            reason=sheet.reason,
            title=sheet.title,
            ccli=sheet.ccli,
            key=sheet.key,
            copyright=sheet.copyright,
            bar_count=sheet.bar_count,
            problem_bars=sheet.problem_bars,
            sections=sheet.sections,
            reader_version=READER_VERSION if sheet.is_lead_sheet else 0,
            score_json=sheet.score_json(),
        ),
    )
