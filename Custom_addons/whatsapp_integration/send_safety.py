"""Stable send identity across Odoo transaction retries (not stored in the DB transaction)."""
import hashlib
import uuid
from odoo import http
from odoo.exceptions import UserError


def send_request_id(env, operation, supplied=None):
    if not supplied:
        # The WSGI request survives env.reset()/rollback in service.model.retrying.
        # This also protects already-open legacy pages that do not send a nonce.
        if not http.request:
            raise UserError('A stable WhatsApp send request ID is required.')
        supplied = http.request.httprequest.environ.setdefault(
            'mapilla.whatsapp.send_request_id', uuid.uuid4().hex)
    if not isinstance(supplied, str) or not 8 <= len(supplied) <= 160:
        raise UserError('Invalid WhatsApp send request ID. Please refresh the page.')
    scope = f'{env.cr.dbname}:{env.uid}:{operation}:{supplied}'
    return hashlib.sha256(scope.encode()).hexdigest()
