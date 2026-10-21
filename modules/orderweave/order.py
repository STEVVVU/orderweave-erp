# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

import hashlib
import json
from decimal import Decimal, InvalidOperation

from trytond.model import ModelSQL, ModelView, Unique, fields
from trytond.model.exceptions import AccessError
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.rpc import RPC
from trytond.transaction import Transaction

from .receiver import (
    LostAcknowledgement, ReceiptConflict, Receiver, ReceiverError)


STATES = [
    ('received', 'Received'),
    ('quotation', 'Quotation'),
    ('confirmed', 'Confirmed'),
    ('completed', 'Completed'),
    ('compensated', 'Compensated'),
    ('manual_required', 'Manual Review Required'),
    ]


def canonical_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError('Order payload must be a JSON object')
    required = {'company', 'party', 'currency', 'expected_total', 'lines'}
    if set(payload) != required:
        raise ValueError('Order payload has unsupported fields')
    if not required.issubset(payload):
        raise ValueError('Order payload is missing required fields')
    if not isinstance(payload['lines'], list) or not payload['lines']:
        raise ValueError('Order payload must include at least one line')
    if len(payload['lines']) > 100:
        raise ValueError('Order payload cannot include more than 100 lines')
    for field in ('company', 'party', 'currency'):
        if (not isinstance(payload[field], int)
                or isinstance(payload[field], bool) or payload[field] <= 0):
            raise ValueError(f'{field} must be a positive Tryton record ID')
    for line in payload['lines']:
        if not isinstance(line, dict) or not {
                'product', 'quantity', 'unit_price'} == line.keys():
            raise ValueError(
                'Each line needs product, quantity, and unit_price')
        if (not isinstance(line['product'], int)
                or isinstance(line['product'], bool) or line['product'] <= 0):
            raise ValueError('Product must be a positive Tryton record ID')
        for field in ('quantity', 'unit_price'):
            value = line[field]
            if not isinstance(value, str):
                raise ValueError(f'Line {field} must be a decimal string')
            try:
                decimal = Decimal(value)
                if not decimal.is_finite():
                    raise InvalidOperation
                if field == 'quantity' and decimal <= 0:
                    raise ValueError('Line quantity must be greater than zero')
                if field == 'unit_price' and decimal < 0:
                    raise ValueError('Line unit price cannot be negative')
            except InvalidOperation as exception:
                raise ValueError(
                    f'Line {field} must be a finite decimal') from exception
    if not isinstance(payload['expected_total'], str):
        raise ValueError('expected_total must be a decimal string')
    try:
        expected_total = Decimal(payload['expected_total'])
        if not expected_total.is_finite():
            raise InvalidOperation
        if expected_total < 0:
            raise ValueError('expected_total cannot be negative')
    except InvalidOperation as exception:
        raise ValueError(
            'expected_total must be a finite decimal') from exception
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


class ImportBatch(ModelSQL, ModelView):
    """An external order and its owned Tryton sale."""

    __name__ = 'orderweave.import'
    _rec_name = 'external_order_id'
    source = fields.Char('Source', required=True, readonly=True)
    external_order_id = fields.Char(
        'External Order ID', required=True, readonly=True)
    payload = fields.Text('Payload', required=True, readonly=True)
    payload_hash = fields.Char('Payload SHA-256', required=True, readonly=True)
    state = fields.Selection(STATES, 'State', required=True, readonly=True)
    sale = fields.Many2One(
        'sale.sale', 'Tryton Sale', readonly=True, ondelete='RESTRICT')
    expected_total = fields.Numeric(
        'Expected Total', digits=(16, 4), readonly=True)
    reconciled_total = fields.Numeric(
        'Reconciled Total', digits=(16, 4), readonly=True)
    last_error = fields.Text('Last Error', readonly=True)
    simulate_lost_ack = fields.Boolean(
        'Simulate Lost Acknowledgement', readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        table = cls.__table__()
        cls._sql_constraints += [
            ('source_external_order_id_uniq', Unique(
                    table, table.source, table.external_order_id),
                'orderweave.msg_duplicate_order'),
            ]
        cls._buttons.update({
                'advance': {
                    'invisible': Eval('state').in_(
                        ['completed', 'compensated', 'manual_required']),
                    'depends': ['state'],
                    },
                'compensate': {
                    'invisible': Eval('state').in_(
                        ['completed', 'compensated']),
                    'depends': ['state'],
                    },
                })
        cls.__rpc__.update({
                'receive': RPC(readonly=False),
                'reconcile': RPC(readonly=False),
                })

    @classmethod
    def _create(cls, values):
        with Transaction().set_context(_orderweave_internal=True):
            return super().create(values)

    @classmethod
    def _write(cls, records, values):
        with Transaction().set_context(_orderweave_internal=True):
            return super().write(records, values)

    @classmethod
    def create(cls, vlist):
        if not Transaction().context.get('_orderweave_internal'):
            raise AccessError('Order imports can only be created by receive')
        return super().create(vlist)

    @classmethod
    def write(cls, *args):
        if not Transaction().context.get('_orderweave_internal'):
            raise AccessError('Order import checkpoints are read-only')
        return super().write(*args)

    @classmethod
    def delete(cls, records):
        raise AccessError('Order imports cannot be deleted')

    @classmethod
    def receive(cls, source, external_order_id, payload,
            simulate_lost_ack=False):
        """Store an external order once, refusing identity reuse."""
        if (not isinstance(source, str)
                or not isinstance(external_order_id, str)
                or not source or not external_order_id
                or len(source) > 128 or len(external_order_id) > 256):
            raise ValueError('Source and external order ID are required')
        if not isinstance(simulate_lost_ack, bool):
            raise ValueError('simulate_lost_ack must be a boolean')
        serialized = canonical_payload(payload)
        if len(serialized.encode('utf-8')) > 65536:
            raise ValueError('Order payload exceeds 64 KiB')
        payload_hash = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        batches = cls.search([
                ('source', '=', source),
                ('external_order_id', '=', external_order_id),
                ], limit=1)
        if batches:
            batch, = batches
            if batch.payload_hash != payload_hash:
                raise ValueError(
                    'This source order already exists with a '
                    'different payload')
            return batch.id
        expected_total = Decimal(payload['expected_total'])
        batch, = cls._create([{
                    'source': source,
                    'external_order_id': external_order_id,
                    'payload': serialized,
                    'payload_hash': payload_hash,
                    'state': 'received',
                    'expected_total': expected_total,
                    'simulate_lost_ack': bool(simulate_lost_ack),
                    }])
        return batch.id

    @classmethod
    @ModelView.button
    def advance(cls, batches):
        """Move one checkpoint forward; each RPC call commits one step."""
        Sale = Pool().get('sale.sale')
        for batch in batches:
            if batch.state == 'received':
                payload = json.loads(batch.payload)
                lines = []
                Party = Pool().get('party.party')
                party = Party(payload['party'])
                invoice_address = party.address_get(type='invoice')
                Product = Pool().get('product.product')
                for external_line in payload['lines']:
                    product = Product(external_line['product'])
                    lines.append(('create', [{
                                'type': 'line',
                                'product': product.id,
                                'quantity': Decimal(external_line['quantity']),
                                'unit': product.sale_uom.id,
                                'unit_price': Decimal(
                                    external_line['unit_price']),
                                }]))
                sale, = Sale.create([{
                            'company': payload['company'],
                            'party': payload['party'],
                            'invoice_address': (
                                invoice_address.id
                                if invoice_address else None),
                            'currency': payload['currency'],
                            'invoice_method': 'manual',
                            'shipment_method': 'manual',
                            'lines': lines,
                            }])
                Sale.quote([sale])
                cls._write([batch], {
                        'sale': sale.id,
                        'state': 'quotation',
                        })
            elif batch.state == 'quotation':
                expected = Decimal(batch.expected_total)
                actual = Decimal(batch.sale.total_amount)
                cls._write([batch], {'reconciled_total': actual})
                if expected != actual:
                    cls._write([batch], {
                            'state': 'manual_required',
                            'last_error': (
                                'The Tryton sale total differs from '
                                'the source order total'),
                            })
                    continue
                Sale.confirm([batch.sale])
                cls._write([batch], {'state': 'confirmed'})
            elif batch.state == 'confirmed':
                try:
                    Receiver().deliver(
                        batch.source, batch.external_order_id,
                        batch.payload_hash, batch.sale.id,
                        lose_ack=batch.simulate_lost_ack)
                except LostAcknowledgement:
                    raise
                except (ReceiptConflict, ReceiverError) as exception:
                    cls._write([batch], {
                            'state': 'manual_required',
                            'last_error': str(exception),
                            })
                else:
                    cls._write([batch], {'state': 'completed'})
            else:
                continue

    @classmethod
    @ModelView.button
    def compensate(cls, batches):
        """Cancel only this import's reversible sale, or require review."""
        Sale = Pool().get('sale.sale')
        for batch in batches:
            if batch.state in {'completed', 'compensated'}:
                continue
            if batch.sale:
                receiver = Receiver()
                if receiver.has_receipt(batch.source, batch.external_order_id):
                    cls._write([batch], {
                            'state': 'manual_required',
                            'last_error': (
                                'A fulfillment receipt already exists'),
                            })
                    continue
                if batch.sale.state not in {'draft', 'quotation'}:
                    cls._write([batch], {
                            'state': 'manual_required',
                            'last_error': (
                                'The sale is no longer eligible for automatic '
                                'cancellation'),
                            })
                    continue
                Sale.cancel([batch.sale])
            cls._write([batch], {'state': 'compensated'})

    @classmethod
    def reconcile(cls, batch_id):
        """Compare the source total with the total on the owned Tryton sale."""
        batch = cls(batch_id)
        if not batch.sale:
            return {
                'state': batch.state,
                'expected_total': str(batch.expected_total),
                'reconciled_total': None,
                'matches': False,
                }
        sale = batch.sale
        expected = Decimal(batch.expected_total)
        actual = Decimal(sale.total_amount)
        cls._write([batch], {'reconciled_total': actual})
        return {
            'state': batch.state,
            'expected_total': str(expected),
            'reconciled_total': str(actual),
            'matches': expected == actual,
            }
