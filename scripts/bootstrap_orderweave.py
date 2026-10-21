#!/usr/bin/env python3
"""Create the local pinned Python environment for Orderweave."""

import os
import subprocess
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.venv'
PYTHON = ENV / 'Scripts' / 'python.exe'
PACKAGES = [
    ROOT / 'trytond',
    ROOT / 'proteus',
    ROOT / 'modules' / 'account',
    ROOT / 'modules' / 'account_invoice',
    ROOT / 'modules' / 'account_invoice_stock',
    ROOT / 'modules' / 'account_product',
    ROOT / 'modules' / 'company',
    ROOT / 'modules' / 'country',
    ROOT / 'modules' / 'currency',
    ROOT / 'modules' / 'party',
    ROOT / 'modules' / 'product',
    ROOT / 'modules' / 'sale',
    ROOT / 'modules' / 'stock',
    ROOT / 'modules' / 'orderweave',
    ]


def run(arguments, env):
    subprocess.run(arguments, cwd=ROOT, check=True, env=env)


def main():
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(ENV)
    env = os.environ.copy()
    env['PIP_CONSTRAINT'] = str(ROOT / 'requirements-orderweave.lock.txt')
    run([str(PYTHON), '-m', 'pip', 'install', '-r',
        str(ROOT / 'requirements-orderweave.lock.txt')], env)
    run([str(PYTHON), '-m', 'pip', 'install', *map(str, PACKAGES)], env)
    print(f'Installed the pinned Orderweave environment in {ENV}')


if __name__ == '__main__':
    main()
