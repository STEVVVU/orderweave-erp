# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

import json
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from trytond.modules.company.tests import create_company, set_company
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction

from ..receiver import LostAcknowledgement


class OrderweaveTestCase(ModuleTestCase):
    'Test Orderweave module'
    module = 'orderweave'

    def setUp(self):
        super().setUp()
        self.receiver_directory = tempfile.TemporaryDirectory()
        self.receiver_path = os.path.join(
            self.receiver_directory.name, 'receiver.sqlite3')
        self.receiver_patch = patch.dict(
            os.environ, {'ORDERWEAVE_RECEIVER_DB': self.receiver_path})
        self.receiver_patch.start()

    def tearDown(self):
        self.receiver_patch.stop()
        self.receiver_directory.cleanup()
        super().tearDown()

    def create_order(self):
        pool = Pool()
        Party = pool.get('party.party')
        Address = pool.get('party.address')
        ProductTemplate = pool.get('product.template')
        Uom = pool.get('product.uom')
        ImportBatch = pool.get('orderweave.import')

        company = create_company()
        with set_company(company):
            customer, = Party.create([{'name': 'Synthetic Customer'}])
            Address.create([{
                        'party': customer.id,
                        'invoice': True,
                        }])
            unit, = Uom.search([('name', '=', 'Unit')], limit=1)
            template, = ProductTemplate.create([{
                        'name': 'Synthetic Product',
                        'type': 'service',
                        'salable': True,
                        'default_uom': unit.id,
                        'sale_uom': unit.id,
                        'list_price': Decimal('12.30'),
                        'products': [('create', [{}])],
                        }])
            product, = template.products
            payload = {
                'company': company.id,
                'party': customer.id,
                'currency': company.currency.id,
                'expected_total': '24.60',
                'lines': [{
                    'product': product.id,
                    'quantity': '2.00',
                    'unit_price': '12.30',
                    }],
                }
            batch_id = ImportBatch.receive(
                'synthetic', 'order-1001', payload, True)
        return ImportBatch(batch_id)

    @with_transaction()
    def test_receive_is_idempotent_and_rejects_changed_payload(self):
        batch = self.create_order()
        payload = json.loads(batch.payload)
        ImportBatch = Pool().get('orderweave.import')
        self.assertEqual(
            ImportBatch.receive('synthetic', 'order-1001', payload), batch.id)
        payload['expected_total'] = '24.61'
        with self.assertRaisesRegex(ValueError, 'different payload'):
            ImportBatch.receive('synthetic', 'order-1001', payload)
        self.assertEqual(ImportBatch.search_count([]), 1)
        with self.assertRaisesRegex(Exception, 'checkpoints are read-only'):
            ImportBatch.write([batch], {'state': 'completed'})

    @with_transaction()
    def test_sale_checkpoint_receiver_replay_and_reconciliation(self):
        batch = self.create_order()
        ImportBatch = Pool().get('orderweave.import')
        ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        self.assertEqual(batch.state, 'quotation')
        self.assertEqual(batch.sale.state, 'quotation')

        ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        self.assertEqual(batch.state, 'confirmed')
        self.assertEqual(batch.sale.state, 'confirmed')

        with self.assertRaisesRegex(
                LostAcknowledgement, 'acknowledgment was lost'):
            ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        self.assertEqual(batch.state, 'confirmed')
        ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        self.assertEqual(batch.state, 'completed')

        result = ImportBatch.reconcile(batch.id)
        self.assertTrue(result['matches'])
        self.assertEqual(Decimal(result['expected_total']), Decimal('24.60'))
        self.assertEqual(Decimal(result['reconciled_total']), Decimal('24.60'))

    @with_transaction()
    def test_compensation_cancels_only_import_owned_quotation(self):
        batch = self.create_order()
        ImportBatch = Pool().get('orderweave.import')
        ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        sale = batch.sale
        ImportBatch.compensate([batch])
        batch = ImportBatch(batch.id)
        self.assertEqual(batch.state, 'compensated')
        self.assertEqual(sale.state, 'cancelled')

    @with_transaction()
    def test_compensation_after_confirmation_requires_manual_review(self):
        batch = self.create_order()
        ImportBatch = Pool().get('orderweave.import')
        ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        ImportBatch.advance([batch])
        batch = ImportBatch(batch.id)
        self.assertNotIn(batch.sale.state, {'draft', 'quotation'})
        ImportBatch.compensate([batch])
        batch = ImportBatch(batch.id)
        self.assertEqual(batch.state, 'manual_required')
        self.assertNotIn(batch.sale.state, {'draft', 'quotation'})


del ModuleTestCase
