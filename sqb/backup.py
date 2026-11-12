"""Backups: automatic daily copies, download, and restore of the company file."""
import datetime
import glob
import os
import sqlite3
import tempfile

from . import db
from .util import ApiError

KEEP = 30           # automatic backups kept per company file
REQUIRED = ('accounts', 'transactions', 'lines', 'names', 'settings')


def backup_dir(db_path):
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'backups')
    os.makedirs(d, exist_ok=True)
    return d


def _copy(src_path, dst_path):
    """Consistent copy using SQLite's online backup (safe while the app is running)."""
    src, dst = sqlite3.connect(src_path), sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _stem(db_path):
    return os.path.splitext(os.path.basename(db_path))[0]


def make_backup(db_path, label='manual'):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = os.path.join(backup_dir(db_path), '%s-%s-%s.db' % (_stem(db_path), stamp, label))
    _copy(db_path, path)
    return path


def auto_backup(db_path):
    """One automatic backup per day, keeping the most recent KEEP."""
    if not os.path.exists(db_path):
        return None
    today = datetime.date.today().strftime('%Y%m%d')
    pattern = os.path.join(backup_dir(db_path), '%s-*-auto.db' % _stem(db_path))
    existing = sorted(glob.glob(pattern))
    if any(os.path.basename(p).startswith('%s-%s' % (_stem(db_path), today)) for p in existing):
        return None
    path = make_backup(db_path, 'auto')
    for old in sorted(glob.glob(pattern))[:-KEEP]:
        os.remove(old)
    return path


def list_backups(db_path):
    out = []
    for p in sorted(glob.glob(os.path.join(backup_dir(db_path), '%s-*.db' % _stem(db_path))), reverse=True):
        st = os.stat(p)
        out.append({'file': os.path.basename(p), 'size': st.st_size,
                    'created': datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')})
    return out


def download_bytes(db_path):
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        _copy(db_path, tmp)
        with open(tmp, 'rb') as f:
            return f.read()
    finally:
        os.remove(tmp)


def _validate(path):
    try:
        conn = sqlite3.connect(path)
        try:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ApiError(400, 'The backup file is damaged')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise ApiError(400, 'That file is not a Simplified QuickBooks backup')
    if not set(REQUIRED) <= tables:
        raise ApiError(400, 'That file is not a Simplified QuickBooks backup')


def restore(db_path, data=None, backup_file=None):
    """Replace the company file with an uploaded backup (bytes) or a file from the backups folder.
    The current books are backed up first, so a restore can itself be undone."""
    if backup_file is not None:
        name = os.path.basename(str(backup_file))
        src = os.path.join(backup_dir(db_path), name)
        if name != backup_file or not os.path.isfile(src):
            raise ApiError(404, 'Backup not found')
        _validate(src)
        safety = make_backup(db_path, 'before-restore')
        _copy(src, db_path)
    else:
        if not data or data[:16] != b'SQLite format 3\x00':
            raise ApiError(400, 'That file is not a Simplified QuickBooks backup')
        fd, tmp = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            with open(tmp, 'wb') as f:
                f.write(data)
            _validate(tmp)
            safety = make_backup(db_path, 'before-restore')
            _copy(tmp, db_path)
        finally:
            os.remove(tmp)
    db.init(db_path)      # bring an older backup up to the current schema
    return os.path.basename(safety)
