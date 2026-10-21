#!/usr/bin/env python3
"""Drive Orderweave through a private local Tryton XML-RPC server."""

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import xmlrpc.client
import urllib.request
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLING = ROOT / '.tooling' / 'orderweave'
EVIDENCE = ROOT / '.audit' / 'evidence'
PORT = 18080
DATABASE = 'orderweave_verify'


class FileTime(ctypes.Structure):
    _fields_ = [('low', ctypes.c_ulong), ('high', ctypes.c_ulong)]


def python_path():
    return ROOT / '.venv' / 'Scripts' / 'python.exe'


def admin_path():
    return ROOT / '.venv' / 'Scripts' / 'trytond-admin.exe'


def runtime_path():
    return TOOLING / 'runtime.json'


def read_runtime():
    if not runtime_path().exists():
        raise RuntimeError(
            'No Orderweave instance is recorded; run launch first')
    return json.loads(runtime_path().read_text(encoding='utf-8'))


def write_runtime(runtime):
    runtime_path().write_text(
        json.dumps(runtime, indent=2) + '\n', encoding='utf-8')


def environment():
    env = os.environ.copy()
    env.update({
        'TZ': 'UTC',
        'TRYTOND_DATABASE_URI': 'sqlite://',
        'TRYTONPASSFILE': str(TOOLING / 'admin-password.txt'),
        'ORDERWEAVE_RECEIVER_DB': str(TOOLING / 'receiver.sqlite3'),
        'PIP_CONSTRAINT': str(ROOT / 'requirements-orderweave.lock.txt'),
        })
    return env


def endpoint():
    return f'http://127.0.0.1:{PORT}/{DATABASE}/rpc/'


def wait_ready(password, process=None):
    deadline = time.monotonic() + 90
    error = None
    while time.monotonic() < deadline:
        if process and process.poll() is not None:
            raise RuntimeError(
                f'Tryton server exited with code {process.returncode}')
        try:
            server = xmlrpc.client.ServerProxy(
                endpoint(), allow_none=True, use_builtin_types=True)
            server.common.db.login('admin', {'password': password})
            return
        except Exception as exception:
            error = exception
            time.sleep(0.5)
    raise RuntimeError(f'Tryton was not ready at {endpoint()}: {error}')


def process_identity(pid):
    """Read a process image and creation time without trusting its PID."""
    if os.name != 'nt':
        raise RuntimeError(
            'The Orderweave verifier currently requires Windows')
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [
        ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong)]
    handle = kernel32.OpenProcess(0x00101000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:
            return None
        raise ctypes.WinError(error)

    try:
        created = FileTime()
        exited = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        image = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(image))
        if not kernel32.QueryFullProcessImageNameW(
                handle, 0, image, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {
            'image': str(Path(image.value).resolve()),
            'created': (created.high << 32) | created.low,
            }
    finally:
        kernel32.CloseHandle(handle)


def recorded_server_identity(runtime):
    if not runtime.get('server_pid'):
        return None
    expected = runtime.get('server_identity')
    if not expected:
        raise RuntimeError(
            'Recorded server has no verifiable process identity; refusing '
            'to signal it')
    actual = process_identity(runtime['server_pid'])
    if actual is None:
        return None
    if (actual['created'] != expected.get('created')
            or actual['image'].casefold()
            != expected.get('image', '').casefold()):
        raise RuntimeError(
            'Recorded server PID now refers to a different process; refusing '
            'to signal it')
    return actual


def bootstrap():
    if not python_path().exists():
        subprocess.run(
            [sys.executable,
                str(ROOT / 'scripts' / 'bootstrap_orderweave.py')],
            cwd=ROOT, check=True)
    with (TOOLING / 'module-build.log').open('w', encoding='utf-8') as log:
        subprocess.run(
            [str(python_path()), '-m', 'pip', 'install', '--no-deps',
                '--force-reinstall', str(ROOT / 'modules' / 'orderweave')],
            cwd=ROOT, env=environment(), check=True,
            stdout=log, stderr=subprocess.STDOUT)


def assert_port_free():
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', PORT)) == 0:
            raise RuntimeError(
                f'127.0.0.1:{PORT} is occupied; stop the recorded verifier '
                'instance or choose another port before launching')


def start_server(runtime):
    log_path = TOOLING / 'server.log'
    log = log_path.open('a', encoding='utf-8')
    creationflags = 0
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [str(python_path()), '-m', 'trytond.cli',
            '-c', str(TOOLING / 'trytond.conf'), '-d', DATABASE],
        cwd=ROOT, env=environment(), stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, creationflags=creationflags,
        close_fds=True)
    log.close()
    runtime['server_pid'] = process.pid
    try:
        runtime['server_identity'] = process_identity(process.pid)
    except Exception:
        process.terminate()
        process.wait(timeout=10)
        raise
    if not runtime['server_identity']:
        raise RuntimeError('Could not verify the started Tryton process')
    write_runtime(runtime)
    wait_ready(runtime['password'], process)
    return runtime


def stop_server(runtime):
    pid = runtime.get('server_pid')
    if not pid:
        return
    if os.name == 'nt':
        actual = recorded_server_identity(runtime)
        if actual is None:
            runtime['server_pid'] = None
            runtime['server_identity'] = None
            write_runtime(runtime)
            return
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong)]
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.GenerateConsoleCtrlEvent.argtypes = [
            ctypes.c_ulong, ctypes.c_ulong]
        handle = kernel32.OpenProcess(0x00101001, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # Recheck the identity on the exact handle that will be
            # terminated, closing the PID-reuse window between checks.
            created = FileTime()
            exited = FileTime()
            kernel = FileTime()
            user = FileTime()
            if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user)):
                raise ctypes.WinError(ctypes.get_last_error())
            image = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(image))
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, image, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            identity = {
                'image': str(Path(image.value).resolve()),
                'created': (created.high << 32) | created.low,
                }
            if (identity['created'] != actual['created']
                    or identity['image'].casefold()
                    != actual['image'].casefold()):
                raise RuntimeError(
                    'Tryton PID changed during cleanup; refusing to signal it')
            # First ask this owned Python process to shut down cleanly so
            # Tryton can close its database connections. If it ignores the
            # console event, terminate only the identity-checked handle.
            kernel32.GenerateConsoleCtrlEvent(1, pid)
            result = kernel32.WaitForSingleObject(handle, 5000)
            if result == 0x102:
                if not kernel32.TerminateProcess(handle, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                result = kernel32.WaitForSingleObject(handle, 10000)
            if result != 0:
                raise RuntimeError(
                    f'Tryton server process {pid} did not stop cleanly '
                    f'(wait result {result})')
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
    runtime['server_pid'] = None
    runtime['server_identity'] = None
    write_runtime(runtime)


def launch():
    if runtime_path().exists():
        current = read_runtime()
        if current.get('server_pid'):
            if recorded_server_identity(current) is not None:
                raise RuntimeError(
                    'A recorded Orderweave verifier is already running')
    TOOLING.mkdir(parents=True, exist_ok=True)
    assert_port_free()
    bootstrap()
    password = secrets.token_urlsafe(24)
    (TOOLING / 'admin-password.txt').write_text(
        password + '\n', encoding='utf-8')
    data_path = TOOLING / 'database'
    data_path.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(data_path / f'{DATABASE}.sqlite').close()
    config = TOOLING / 'trytond.conf'
    config.write_text(
        '[database]\n'
        'uri = sqlite://\n'
        f'path = {data_path}\n'
        'list = False\n\n'
        '[web]\n'
        f'listen = 127.0.0.1:{PORT}\n'
        f'root = {ROOT / "sao"}\n',
        encoding='utf-8')
    runtime = {
        'database': DATABASE,
        'port': PORT,
        'server_pid': None,
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'password': password,
        }
    write_runtime(runtime)
    try:
        log_path = TOOLING / 'database-init.log'
        with log_path.open('w', encoding='utf-8') as log:
            subprocess.run(
                [str(admin_path()), '-c', str(config), '-d', DATABASE,
                    '-u', 'orderweave', '--activate-dependencies',
                    '--email', 'orderweave@example.invalid'],
                cwd=ROOT, env=environment(), check=True, timeout=900,
                stdout=log, stderr=subprocess.STDOUT)
        start_server(runtime)
        doctor()
        print(f'Orderweave is ready at http://127.0.0.1:{PORT}/{DATABASE}/')
    except Exception:
        stop_server(runtime)
        raise


def doctor():
    runtime = read_runtime()
    if not runtime.get('server_pid'):
        raise RuntimeError('No owned Tryton server process is recorded')
    if recorded_server_identity(runtime) is None:
        raise RuntimeError('Recorded Tryton server process is not running')
    password = (TOOLING / 'admin-password.txt').read_text(
        encoding='utf-8').strip()
    wait_ready(password)
    from proteus import Model
    from proteus.config import set_xmlrpc_session

    with set_xmlrpc_session(endpoint(), 'admin', password):
        Import = Model.get('orderweave.import')
        Import.find([], limit=1)
    print(f'Tryton {DATABASE} accepts authenticated XML-RPC requests')
    if (ROOT / 'sao/dist/tryton-sao.js').is_file():
        assets = []
        for route in (
                'index.html', 'dist/tryton-sao.js', 'dist/tryton-sao.css'):
            expected = (ROOT / 'sao' / route).read_bytes()
            url = f'http://127.0.0.1:{PORT}/{route}'
            with urllib.request.urlopen(url, timeout=10) as response:
                actual = response.read()
            if actual != expected:
                raise AssertionError(
                    'Served web asset differs from the built source: '
                    f'{route}')
            assets.append({
                'path': route, 'bytes': len(actual),
                'sha256': hashlib.sha256(actual).hexdigest()})
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / 'orderweave-web-assets.json').write_text(
            json.dumps({'assets': assets, 'renderedBrowser': False}, indent=2)
            + '\n', encoding='utf-8')
        print('Built Sao index, JavaScript and CSS served with exact bytes')


@contextmanager
def client(password):
    from proteus.config import set_xmlrpc_session

    with set_xmlrpc_session(endpoint(), 'admin', password) as config:
        yield config


def expect_rpc_fault(action, expected_text):
    try:
        action()
    except xmlrpc.client.Fault as fault:
        message = fault.faultString
        if expected_text.casefold() not in message.casefold():
            raise AssertionError(
                f'RPC refusal did not contain {expected_text!r}: {message}')
        return message
    raise AssertionError(f'RPC unexpectedly accepted {expected_text!r}')


def seed_records(config):
    from proteus import Model, Wizard

    Currency = Model.get('currency.currency', config=config)
    currencies = Currency.find([('code', '=', 'USD')])
    if currencies:
        currency, = currencies
    else:
        currency = Currency(
            name='U.S. Dollar', code='USD', symbol='$',
            rounding=Decimal('0.01'))
        currency.save()
        CurrencyRate = Model.get('currency.currency.rate', config=config)
        rate = CurrencyRate()
        rate.currency = currency
        rate.date = dt.date.min
        rate.rate = Decimal('1.0')
        rate.save()

    Party = Model.get('party.party', config=config)
    company_party = Party(name='Orderweave Synthetic Company')
    company_party.save()
    CompanySetup = Wizard('company.company.config', config=config)
    CompanySetup.execute('company')
    CompanySetup.form.party = company_party
    CompanySetup.form.currency = currency
    CompanySetup.execute('add')
    User = Model.get('res.user', config=config)
    config._context = User.get_preferences(True, {})

    companies = Model.get('company.company', config=config).find()
    company = companies[-1]
    customer = Party(name='Orderweave Synthetic Customer')
    customer.save()
    Address = Model.get('party.address', config=config)
    address = Address()
    address.party = customer
    address.invoice = True
    address.save()

    Uom = Model.get('product.uom', config=config)
    unit, = Uom.find([('name', '=', 'Unit')])
    Template = Model.get('product.template', config=config)
    template = Template()
    template.name = 'Orderweave Synthetic Product'
    template.type = 'service'
    template.salable = True
    template.default_uom = unit
    template.sale_uom = unit
    template.list_price = Decimal('12.30')
    template.save()
    product, = template.products
    return {
        'company': company.id,
        'customer': customer.id,
        'currency': currency.id,
        'product': product.id,
        }


def import_payload(records):
    return {
        'company': records['company'],
        'party': records['customer'],
        'currency': records['currency'],
        'expected_total': '24.60',
        'lines': [{
            'product': records['product'],
            'quantity': '2.00',
            'unit_price': '12.30',
            }],
        }


def drive():
    runtime = read_runtime()
    password = (TOOLING / 'admin-password.txt').read_text(
        encoding='utf-8').strip()
    evidence = {
        'database': DATABASE,
        'actions': [],
        }
    with client(password) as config:
        from proteus import Model

        records = seed_records(config)
        Import = Model.get('orderweave.import', config=config)
        payload = import_payload(records)
        batch_id = Import.receive(
            'synthetic', 'orderweave-verify-1001', payload, True,
            config._context)
        repeated_id = Import.receive(
            'synthetic', 'orderweave-verify-1001', payload, False,
            config._context)
        if repeated_id != batch_id:
            raise AssertionError('Repeated identity created another import')
        evidence['actions'].append({
            'action': 'receive same source identity twice',
            'batch_id': batch_id,
            'result': 'one import ID returned twice',
            })
        original_context = config._context
        config._context = dict(
            original_context, _orderweave_internal=True)
        try:
            create_error = expect_rpc_fault(
                lambda: Import.create([{
                        'source': 'synthetic',
                        'external_order_id': 'forged-create',
                        'payload': json.dumps(payload),
                        'payload_hash': '0' * 64,
                        'state': 'completed',
                        'expected_total': 24.60,
                        }], config._context),
                'Order imports can only be created by receive')
            write_error = expect_rpc_fault(
                lambda: Import.write(
                    [batch_id], {'state': 'completed'}, config._context),
                'Order import checkpoints are read-only')
            delete_error = expect_rpc_fault(
                lambda: Import.delete([Import(batch_id)]),
                'Order imports cannot be deleted')
        finally:
            config._context = original_context
        unchanged = Import(batch_id)
        if unchanged.state != 'received':
            raise AssertionError('A denied direct mutation changed the import')
        evidence['actions'].append({
            'action': (
                'attempt direct create, checkpoint write, and delete with a '
                'forged private context'),
            'result': ('RPC denied each request and ignored the '
                'private context key'),
            'errors': [create_error, write_error, delete_error],
            })
        changed_payload = dict(payload, expected_total='24.61')
        conflict_error = expect_rpc_fault(
            lambda: Import.receive(
                'synthetic', 'orderweave-verify-1001', changed_payload,
                False, config._context),
            'different payload')
        evidence['actions'].append({
            'action': 'reuse source identity with changed total',
            'result': 'RPC rejected the conflicting payload',
            'error': conflict_error,
            })

        batch = Import(batch_id)
        batch.click('advance')
        batch.reload()
        evidence['actions'].append({
            'action': 'advance received order',
            'import_state': batch.state,
            'sale_state': batch.sale.state,
            'sale_id': batch.sale.id,
            })
        batch.click('advance')
        batch.reload()
        evidence['actions'].append({
            'action': 'confirm quoted Tryton sale',
            'import_state': batch.state,
            'sale_state': batch.sale.state,
            })
        lost_ack_error = expect_rpc_fault(
            lambda: batch.click('advance'), 'acknowledgment was lost')
        batch.reload()
        evidence['actions'].append({
            'action': 'receiver persists receipt and loses acknowledgment',
            'import_state_after_rpc_rollback': batch.state,
            'receipt_count': receipt_count(),
            'error': lost_ack_error,
            })
        if batch.state != 'confirmed' or receipt_count() != 1:
            raise AssertionError(
                'Lost-ack boundary did not preserve expected state')

    stop_server(runtime)
    start_server(runtime)
    doctor()
    with client(password) as config:
        from proteus import Model

        Import = Model.get('orderweave.import', config=config)
        batch = Import(batch_id)
        batch.click('advance')
        batch.reload()
        if batch.state != 'completed':
            raise AssertionError(
                f'Import did not complete after replay: {batch.state}')
        total = Import.reconcile(batch.id, config._context)
        if not total['matches']:
            raise AssertionError(f'Sale total did not reconcile: {total}')
        batch.reload()
        evidence['actions'].append({
            'action': 'restart Tryton and replay receiver request',
            'import_state': batch.state,
            'sale_state': batch.sale.state,
            'reconciliation': total,
            'receipt_count': receipt_count(),
            })

        compensation_id = Import.receive(
            'synthetic', 'orderweave-verify-1002', payload, False,
            config._context)
        compensation = Import(compensation_id)
        compensation.click('advance')
        compensation.reload()
        sale = compensation.sale
        compensation.click('compensate')
        compensation.reload()
        if compensation.state != 'compensated' or sale.state != 'cancelled':
            raise AssertionError('Eligible quotation was not compensated')
        evidence['actions'].append({
            'action': 'compensate task-owned quotation',
            'import_state': compensation.state,
            'sale_state': sale.state,
            })

        manual_id = Import.receive(
            'synthetic', 'orderweave-verify-1003', payload, False,
            config._context)
        manual = Import(manual_id)
        manual.click('advance')
        manual.click('advance')
        manual.reload()
        sale = manual.sale
        manual.click('compensate')
        manual.reload()
        if manual.state != 'manual_required' or sale.state != 'processing':
            raise AssertionError(
                'Confirmed sale was not routed for manual review: '
                f'import={manual.state}, sale={sale.state}')
        evidence['actions'].append({
            'action': 'request compensation after sale confirmation',
            'import_state': manual.state,
            'sale_state': sale.state,
            'last_error': manual.last_error,
            })

    evidence['receiver_rows'] = receiver_rows()
    evidence_path = EVIDENCE / 'orderweave-rpc-run.json'
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, indent=2, default=str) + '\n', encoding='utf-8')
    print(f'RPC verification passed; evidence saved to {evidence_path}')


def receiver_rows():
    path = TOOLING / 'receiver.sqlite3'
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        return [
            {'source': row[0], 'external_order_id': row[1],
                'sale_id': row[2]}
            for row in connection.execute(
                'SELECT source, external_order_id, sale_id FROM receipt')]


def receipt_count():
    return len(receiver_rows())


def evidence():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    server_log = TOOLING / 'server.log'
    if server_log.exists():
        shutil.copy2(server_log, EVIDENCE / 'orderweave-server.log')
    if not (EVIDENCE / 'orderweave-rpc-run.json').exists():
        raise RuntimeError('Drive has not produced the RPC evidence yet')
    print(f'Evidence retained in {EVIDENCE}')


def cleanup():
    if runtime_path().exists():
        runtime = read_runtime()
        stop_server(runtime)
    if TOOLING.exists():
        shutil.rmtree(TOOLING)
    if not (EVIDENCE / 'orderweave-rpc-run.json').exists():
        raise RuntimeError('Cleanup ran before evidence was captured')
    print('Owned server and scratch database removed; '
        f'evidence remains in {EVIDENCE}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'action',
        choices=['launch', 'doctor', 'drive', 'evidence', 'cleanup', 'run'])
    action = parser.parse_args().action
    try:
        if action == 'launch':
            launch()
        elif action == 'doctor':
            doctor()
        elif action == 'drive':
            drive()
        elif action == 'evidence':
            evidence()
        elif action == 'cleanup':
            cleanup()
        else:
            try:
                launch()
                doctor()
                drive()
                evidence()
            finally:
                if TOOLING.exists():
                    cleanup()
    except Exception as exception:
        print(f'Orderweave verification failed: {exception}', file=sys.stderr)
        raise


if __name__ == '__main__':
    main()
