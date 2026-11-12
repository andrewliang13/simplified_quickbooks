#!/usr/bin/env python3
"""Start Simplified QuickBooks.  Usage: python3 run.py [--port 8000] [--db books.db]"""
import argparse
import os

from sqb.server import serve

if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='Simplified QuickBooks')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8000)))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--db', default=os.environ.get('SQB_DB', os.path.join(here, 'books.db')),
                    help='path of the company data file (SQLite)')
    args = ap.parse_args()
    serve(args.db, args.host, args.port)
