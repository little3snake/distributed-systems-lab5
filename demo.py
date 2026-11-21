"""Run all five experiments with real HTTP and owned child processes."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

ROOT = Path(__file__).resolve().parent
PRIMARY = 'http://127.0.0.1:5001'
REPLICA = 'http://127.0.0.1:5002'
ORDERS = 'http://127.0.0.1:5003'


def main():
    # Never stop an unrelated process already using a lab port.
    for port in (5001, 5002, 5003):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                raise SystemExit(f'Port {port} is occupied; stop existing lab services first.')
    (ROOT / 'logs').mkdir(exist_ok=True)
    processes = {}
    with (ROOT / 'logs/experiments.log').open('w', encoding='utf-8', buffering=1) as output:
        def record(message):
            line = f'{datetime.now(timezone.utc).isoformat()} [demo] {message}'
            print(line, flush=True)
            output.write(line + '\n')

        def call(label, method, url, status=200, **kwargs):
            start = time.monotonic()
            response = requests.request(method, url, timeout=8, **kwargs)
            record(f'{label}: HTTP {response.status_code}, {time.monotonic()-start:.3f}s {response.text.strip()}')
            assert response.status_code == status, response.text
            return response.json()

        def start(name, *extra):
            script = 'orders_service.py' if name == 'orders' else 'catalog_service.py'
            args = [] if name == 'orders' else ['--role', name]
            process = subprocess.Popen([sys.executable, '-u', str(ROOT / script), *args, *extra],
                                       cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            processes[name] = process
            url = {'primary': PRIMARY, 'replica': REPLICA, 'orders': ORDERS}[name]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'{name} exited early')
                try:
                    if requests.get(url + '/health', timeout=.3).status_code == 200:
                        record(f'Started {name}, PID={process.pid}, arguments={extra}')
                        return
                except requests.RequestException:
                    pass
                time.sleep(.1)
            raise RuntimeError(f'{name} did not become ready')

        def stop(name):
            process = processes.pop(name)
            process.terminate()
            process.wait(timeout=5)
            record(f'Stopped {name}, PID={process.pid}')

        def seed():
            result = call('Synchronize baseline 450000/v2', 'PUT', PRIMARY + '/products/1',
                          json={'price': 450000, 'mode': 'sync'})
            assert result['product']['version'] == 2

        def order(source, price=450000, version=2):
            result = call('Create order', 'POST', ORDERS + '/orders', status=201,
                          json={'product_id': 1, 'quantity': 1})
            assert (result['catalog_source'], result['price'], result['catalog_version']) == (source, price, version), result
            return result

        try:
            start('replica')
            start('primary')
            start('orders')
            call('Primary health', 'GET', PRIMARY + '/health')
            call('Replica health', 'GET', REPLICA + '/health')
            seed()
            record('EXPERIMENT 1: normal operation')
            order('primary')
            record('PASS experiment 1')
            record('EXPERIMENT 2: primary stopped')
            stop('primary')
            order('replica')
            record('PASS experiment 2')
            record('EXPERIMENT 3: recovery')
            start('primary')
            seed()  # in-memory service restarts at v1; restore the shared baseline
            order('primary')
            record('PASS experiment 3')
            record('EXPERIMENT 4: running primary, response delay 3s, Order timeout 1s')
            stop('primary')
            start('primary', '--response-delay', '3')
            seed()
            order('replica')
            call('Primary is still alive', 'GET', PRIMARY + '/health')
            current = call('Direct primary read completes after 3s', 'GET', PRIMARY + '/products/1')
            assert current['price'] == 450000
            record('PASS experiment 4: timeout creates suspicion, not proof of a stopped service')
            record('EXPERIMENT 5: async replication delay 5s')
            stop('primary')
            start('primary', '--replication-delay', '5')
            seed()
            before = call('Replica before update', 'GET', REPLICA + '/products/1')
            assert before['price'] == 450000 and before['version'] == 2
            started = time.monotonic()
            changed = call('Primary async price 400000/v3', 'PUT', PRIMARY + '/products/1',
                           json={'price': 400000, 'mode': 'async'})
            assert changed['product']['price'] == 400000 and changed['product']['version'] == 3
            stop('primary')
            assert time.monotonic() - started < 5, 'Primary must stop before replication delivery'
            order('replica')
            time.sleep(5.1)
            old = call('Replica still stale after delivery deadline', 'GET', REPLICA + '/products/1')
            assert old['price'] == 450000 and old['version'] == 2
            record('PASS experiment 5: old price 450000/v2; latest primary price was 400000/v3')
            call('Replica rejects client writes', 'PUT', REPLICA + '/products/1', status=405,
                 json={'price': 1, 'mode': 'sync'})
            record('EXTRA CHECK: both catalogs stopped')
            stop('replica')
            call('No catalogs available', 'POST', ORDERS + '/orders', status=503,
                 json={'product_id': 1, 'quantity': 1})
            record('ALL FIVE EXPERIMENTS PASSED; read-only and total-outage checks passed')
        finally:
            for name in list(processes):
                stop(name)


if __name__ == '__main__':
    main()
