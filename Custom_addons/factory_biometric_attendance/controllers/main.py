# -*- coding: utf-8 -*-

import hmac
import logging
import os
import re
from urllib.parse import urlencode

from werkzeug.wrappers import Response

from odoo import SUPERUSER_ID, http
from odoo.exceptions import ValidationError
from odoo.http import request


_logger = logging.getLogger(__name__)
_SAFE_SERIAL_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
_SAFE_STAMP_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_MAX_BODY_BYTES = 512 * 1024


class ZKTecoPushController(http.Controller):
    """Minimal, fail-closed ZKTeco ADMS/PUSH ingress."""

    def _response(self, body="OK", status=200, charset="utf-8"):
        if isinstance(body, str):
            body = body.encode(charset, errors="strict")
        return Response(
            body,
            status=status,
            content_type="text/plain; charset=%s" % charset,
            headers={"Cache-Control": "no-store"},
        )

    def _authorize_gateway(self):
        expected = os.environ.get("ZK_PUSH_GATEWAY_TOKEN", "")
        provided = request.httprequest.headers.get("X-ZK-Push-Gateway", "")
        target_db = os.environ.get("ZK_ATTENDANCE_DB", "yasser3")
        current_db = getattr(request, "db", None)
        return bool(
            expected
            and hmac.compare_digest(expected, provided)
            and current_db == target_db
        )

    def _serial(self):
        serial = (request.httprequest.args.get("SN") or "").strip()
        if not _SAFE_SERIAL_RE.fullmatch(serial):
            raise ValidationError("Invalid device serial")
        return serial

    def _source_ip(self):
        return (
            request.httprequest.headers.get("X-Real-IP")
            or request.httprequest.remote_addr
            or ""
        )[:64]

    def _decode_payload(self, raw, fallback_charset=None):
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as utf8_error:
            if not fallback_charset or fallback_charset.lower() == "utf-8":
                raise ValidationError("Payload is not valid UTF-8") from utf8_error
            try:
                return raw.decode(fallback_charset, errors="strict")
            except (LookupError, UnicodeDecodeError) as fallback_error:
                raise ValidationError(
                    "Payload is invalid for the configured device encoding"
                ) from fallback_error

    def _payload(self, fallback_charset=None):
        raw = request.httprequest.get_data(cache=False)
        if len(raw) > _MAX_BODY_BYTES:
            raise ValidationError("Payload too large")
        return self._decode_payload(raw, fallback_charset=fallback_charset)

    def _stamp(self):
        stamp = (
            request.httprequest.args.get("Stamp")
            or request.httprequest.args.get("stamp")
            or ""
        ).strip()
        if stamp and not _SAFE_STAMP_RE.fullmatch(stamp):
            raise ValidationError("Invalid upload stamp")
        return stamp or None

    def _rollback(self):
        request.env.cr.rollback()

    def _device(self):
        serial = self._serial()
        args = request.httprequest.args
        source_ip = self._source_ip()
        device = request.env["factory.biometric.device"].sudo().search(
            [("serial_number", "=", serial)], limit=1
        )
        if not device:
            raise ValidationError("Unknown device serial")
        if not device._check_source_ip(source_ip):
            raise PermissionError("Source IP rejected")
        if device.state == "blocked":
            raise PermissionError("Device blocked")
        provided_key = (
            args.get("pushcommkey")
            or args.get("PushCommKey")
            or request.httprequest.headers.get("X-Push-Comm-Key", "")
        )
        if not device._check_push_comm_key(provided_key):
            raise PermissionError("Push communication key rejected")
        metadata = {
            "push_version": args.get("pushver") or args.get("PushProtVer"),
            "firmware_version": args.get("FWVersion") or args.get("firmware"),
            "platform": args.get("Device") or args.get("platform"),
            "info": args.get("INFO") or "",
        }
        device = request.env["factory.biometric.device"].sudo()._register_from_push(
            serial, source_ip=source_ip, metadata=metadata
        )
        return device

    @http.route(
        "/iclock/cdata",
        type="http",
        auth="none",
        csrf=False,
        save_session=False,
        methods=["GET", "POST"],
    )
    def cdata(self, **kwargs):
        if not self._authorize_gateway():
            return self._response("ERROR:FORBIDDEN", status=403)
        request.update_env(user=SUPERUSER_ID)
        try:
            device = self._device()
            if request.httprequest.method == "GET":
                if (request.httprequest.args.get("options") or "").lower() == "all":
                    return self._response(device._build_push_options())
                return self._response("OK")

            if device.state != "active":
                self._rollback()
                return self._response("ERROR:DEVICE NOT ACTIVE", status=403)

            table = (request.httprequest.args.get("table") or "ATTLOG").upper()
            payload = self._payload(
                fallback_charset=(
                    device._push_command_charset() if table == "OPERLOG" else None
                )
            )
            stamp = self._stamp()
            if table == "ATTLOG":
                count = request.env["factory.biometric.event"].sudo()._ingest_attlog(
                    device, payload, stamp=stamp
                )
            elif table == "OPERLOG":
                count = device.sudo()._ingest_operlog(payload, stamp=stamp)
            else:
                _logger.info(
                    "Ignoring unsupported ZKTeco table %s from %s", table, device.serial_number
                )
                count = len([line for line in payload.splitlines() if line.strip()])
            return self._response("OK: %s" % count)
        except PermissionError:
            self._rollback()
            return self._response("ERROR:FORBIDDEN", status=403)
        except ValidationError as exc:
            self._rollback()
            _logger.warning("Rejected ZKTeco cdata request: %s", exc)
            return self._response("ERROR:BAD REQUEST", status=400)
        except Exception:
            self._rollback()
            _logger.exception("Unexpected ZKTeco cdata failure")
            return self._response("ERROR:SERVER", status=500)

    @http.route(
        "/iclock/getrequest",
        type="http",
        auth="none",
        csrf=False,
        save_session=False,
        methods=["GET"],
    )
    def getrequest(self, **kwargs):
        if not self._authorize_gateway():
            return self._response("ERROR:FORBIDDEN", status=403)
        request.update_env(user=SUPERUSER_ID)
        try:
            device = self._device()
            if device.state != "active":
                return self._response("OK")
            result = request.env["factory.biometric.command"].sudo()._pop_for_device(device)
            charset = device._push_command_charset()
            return self._response(
                device._encode_push_command(result),
                charset=charset,
            )
        except PermissionError:
            self._rollback()
            return self._response("ERROR:FORBIDDEN", status=403)
        except ValidationError:
            self._rollback()
            return self._response("ERROR:BAD REQUEST", status=400)
        except Exception:
            self._rollback()
            _logger.exception("Unexpected ZKTeco getrequest failure")
            return self._response("ERROR:SERVER", status=500)

    @http.route(
        "/iclock/devicecmd",
        type="http",
        auth="none",
        csrf=False,
        save_session=False,
        methods=["GET", "POST"],
    )
    def devicecmd(self, **kwargs):
        if not self._authorize_gateway():
            return self._response("ERROR:FORBIDDEN", status=403)
        request.update_env(user=SUPERUSER_ID)
        try:
            device = self._device()
            if device.state != "active":
                self._rollback()
                return self._response("ERROR:DEVICE NOT ACTIVE", status=403)
            payload = self._payload() if request.httprequest.method == "POST" else ""
            if not payload:
                allowed = {
                    key: request.httprequest.args.get(key)
                    for key in ("ID", "Return", "CMD")
                    if request.httprequest.args.get(key) is not None
                }
                payload = urlencode(allowed)
            request.env["factory.biometric.command"].sudo()._acknowledge(device, payload)
            return self._response("OK")
        except PermissionError:
            self._rollback()
            return self._response("ERROR:FORBIDDEN", status=403)
        except ValidationError:
            self._rollback()
            return self._response("ERROR:BAD REQUEST", status=400)
        except Exception:
            self._rollback()
            _logger.exception("Unexpected ZKTeco devicecmd failure")
            return self._response("ERROR:SERVER", status=500)

    @http.route(
        ["/iclock/ping", "/iclock/registry"],
        type="http",
        auth="none",
        csrf=False,
        save_session=False,
        methods=["GET", "POST"],
    )
    def health(self, **kwargs):
        if not self._authorize_gateway():
            return self._response("ERROR:FORBIDDEN", status=403)
        request.update_env(user=SUPERUSER_ID)
        try:
            self._device()
            return self._response("OK")
        except PermissionError:
            self._rollback()
            return self._response("ERROR:FORBIDDEN", status=403)
        except Exception:
            self._rollback()
            _logger.exception("Unexpected ZKTeco health request failure")
            return self._response("ERROR:SERVER", status=500)
