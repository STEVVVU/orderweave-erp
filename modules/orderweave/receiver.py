# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

import os
import sqlite3
from contextlib import closing


class ReceiverError(Exception):
    pass


class LostAcknowledgement(ReceiverError):
    pass


class ReceiptConflict(ReceiverError):
    pass


class Receiver:

    def __init__(self, path=None):
        self.path = path or os.environ.get('ORDERWEAVE_RECEIVER_DB')
        if not self.path:
            raise ReceiverError(
                'Set ORDERWEAVE_RECEIVER_DB to a separate receipt database')

    def deliver(self, source, external_order_id, payload_hash, sale_id,
            lose_ack=False):
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            with connection:
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS receipt ('
                    'source TEXT NOT NULL, external_order_id TEXT NOT NULL, '
                    'payload_hash TEXT NOT NULL, sale_id INTEGER NOT NULL, '
                    'PRIMARY KEY (source, external_order_id))')
                row = connection.execute(
                    'SELECT payload_hash, sale_id FROM receipt '
                    'WHERE source = ? AND external_order_id = ?',
                    (source, external_order_id)).fetchone()
                if row:
                    if row != (payload_hash, sale_id):
                        raise ReceiptConflict(
                            'The receiver has a different receipt '
                            'for this order')
                    return False
                connection.execute(
                    'INSERT INTO receipt VALUES (?, ?, ?, ?)',
                    (source, external_order_id, payload_hash, sale_id))
        if lose_ack:
            raise LostAcknowledgement(
                'The receipt was saved, but its acknowledgment was lost')
        return True

    def has_receipt(self, source, external_order_id):
        if not os.path.exists(self.path):
            return False
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'receipt'").fetchone()
            if not row:
                return False
            return connection.execute(
                'SELECT 1 FROM receipt '
                'WHERE source = ? AND external_order_id = ?',
                (source, external_order_id)).fetchone() is not None
