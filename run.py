"""Local-only production server and offline maintenance commands."""
import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from moneyhub.db import data_dir, engine_for, backup, restore


def main():
    parser = argparse.ArgumentParser(description='MyMoney Hub · local personal finance')
    parser.add_argument('--demo', action='store_true', help='Use isolated demo database')
    parser.add_argument('--no-browser', action='store_true', default=os.environ.get('MYMONEYHUB_NO_BROWSER') == '1')
    parser.add_argument('--port', type=int, default=int(os.environ.get('MYMONEYHUB_PORT', '8765')))
    parser.add_argument('command', nargs='?', default='serve', choices=['serve', 'backup', 'restore', 'migrate'])
    parser.add_argument('file', nargs='?', help='Backup input/output filename')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535.')
    root = data_dir(args.demo)
    root.mkdir(parents=True, exist_ok=True)
    database = root / 'moneyhub.db'
    # Cross-process Windows lock. Restore and the server cannot share a data directory.
    lock_file = (root / 'application.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0)
            if lock_file.read(1) == b'':
                lock_file.write(b'0')
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                parser.exit(1, 'MyMoney Hub is already running for this database. Close its server window first.\n')
        else:
            import fcntl
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                parser.exit(1, 'MyMoney Hub is already running.\n')
        if args.command == 'restore':
            if not args.file:
                parser.error('Restore requires a backup file.')
            print('This replaces the active database, including its password. A safety copy is retained.')
            confirmation = input('Type RESTORE to continue: ')
            restore(args.file, database, confirmation)
            print('Restore verified. Start MyMoney Hub and use the password from the backup.')
            return
        if args.command == 'backup':
            if not args.file:
                parser.error('Backup requires a new destination filename.')
            if not database.exists():
                parser.error('No database exists yet.')
            print('Verified backup:', backup(database, args.file))
            return
        engine = engine_for(database)
        if args.command == 'migrate':
            engine.dispose()
            print('Schema is current.')
            return
        if args.demo:
            from moneyhub.demo import seed_demo
            seed_demo(engine)
            print('DEMO login password: ExploreMyMoney!2026')
        engine.dispose()
        from moneyhub.web import create_app
        from waitress import serve
        app = create_app(database=database, demo=args.demo)
        url = f'http://127.0.0.1:{args.port}'
        print(f'MyMoney Hub: {url}\nData: {database}\nClose this window or press Ctrl+C to stop.')
        if not args.no_browser:
            # Wait for the server to bind before opening the browser.
            def open_when_ready():
                import time
                for _ in range(50):
                    try:
                        with socket.create_connection(('127.0.0.1', args.port), timeout=.2):
                            webbrowser.open(url)
                            return
                    except OSError:
                        time.sleep(.1)
            threading.Thread(target=open_when_ready, daemon=True).start()
        serve(app, host='127.0.0.1', port=args.port, threads=4, max_request_body_size=2*1024*1024)
    finally:
        lock_file.close()


if __name__ == '__main__':
    main()
