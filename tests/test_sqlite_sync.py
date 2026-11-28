"""Restored native history must be usable without a live WAL or writer."""
import fnmatch
from pathlib import Path
import os
import sqlite3
import subprocess

import pytest

from qwenpaw_worker.sync import FileSync, push_local


@pytest.mark.parametrize('filename', ['history.db', 'messages.sqlite'])
@pytest.mark.parametrize('operation', ['background', 'selected', 'flush', 'directory'])
def test_active_native_database_is_uploaded_as_one_consistent_snapshot(tmp_path, monkeypatch, operation, filename):
    root = tmp_path / 'worker'
    workspace = root / '.qwenpaw/workspaces/default'
    workspace.mkdir(parents=True)
    database = workspace / filename
    writer = sqlite3.connect(database)
    writer.execute('PRAGMA page_size=512')
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('CREATE TABLE history(id INTEGER PRIMARY KEY, body TEXT)')
    writer.execute('INSERT INTO history(body) VALUES(?)', ('a' * 5000,))
    writer.commit()
    writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    writer.execute('INSERT INTO history(body) VALUES(?)', ('b' * 5000,))
    writer.commit()
    sync = FileSync(endpoint='http://storage', access_key='fixture', secret_key='fixture', bucket='bucket',
                    worker_name='worker', local_dir=root, shared_dir=tmp_path / 'shared')
    remote = {}
    checkpointed = False
    monkeypatch.setattr(sync, 'ensure_alias', lambda **kwargs: None)
    monkeypatch.setattr(sync, '_cat_bytes', lambda key, **kwargs: remote.get(key))

    def copy(source, key):
        nonlocal checkpointed
        remote[key] = source.read_bytes()
        # This interleaving was reproduced with the real SQLite WAL engine:
        # a separately copied main file and later WAL are different generations.
        if source.name == filename and not checkpointed:
            writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            writer.execute('INSERT INTO history(body) VALUES(?)', ('c' * 5000,))
            writer.commit()
            checkpointed = True

    def mc(*args, **kwargs):
        if args[0] == 'cp':
            copy(Path(args[1]), args[2].removeprefix('agentteams/bucket/'))
        elif args[0] == 'mirror':
            source, destination = Path(args[1]), args[2].removeprefix('agentteams/bucket/')
            exclusions = [args[i + 1] for i, arg in enumerate(args) if arg == '--exclude']
            for path in sorted(source.rglob('*')):
                if path.is_file() and not any(fnmatch.fnmatch(path.relative_to(source).as_posix(), pattern) for pattern in exclusions):
                    copy(path, destination + path.relative_to(source).as_posix())
        elif args[0] == 'rm':
            for key in args[1:]:
                remote.pop(key.removeprefix('agentteams/bucket/'), None)
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    monkeypatch.setattr(sync, '_mc', mc)
    try:
        if operation == 'background':
            os.utime(database, (0, 0))
            push_local(sync, since=1)
        elif operation == 'selected':
            sync.push_paths(list(workspace.iterdir()))
        elif operation == 'directory':
            sync.push_directories([workspace])
        else:
            assert sync.flush()['ok']
        key = 'agents/worker/.qwenpaw/workspaces/default/' + filename
        assert key in remote
        assert not any(name.endswith(('-wal', '-shm', '-journal')) for name in remote)
        restored = tmp_path / 'restored.db'
        restored.write_bytes(remote[key])
        with sqlite3.connect(restored) as reader:
            assert reader.execute('PRAGMA quick_check').fetchone() == ('ok',)
            assert reader.execute('SELECT count(*) FROM history').fetchone()[0] >= 2
    finally:
        writer.close()


def test_startup_restore_keeps_retained_wal_commits_and_ignores_old_remote_sidecars(tmp_path, monkeypatch):
    from qwenpaw_worker.sync import _file_bytes
    root = tmp_path / 'worker'
    workspace = root / '.qwenpaw/workspaces/default'
    workspace.mkdir(parents=True)
    database = workspace / 'history.db'
    writer = sqlite3.connect(database)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('CREATE TABLE history(value TEXT)')
    writer.execute("INSERT INTO history VALUES('retained')")
    writer.commit()
    retained = {path.name: path.read_bytes() for path in workspace.iterdir()}
    writer.close()
    for name, data in retained.items():
        (workspace / name).write_bytes(data)
    sync = FileSync(endpoint='http://storage', access_key='fixture', secret_key='fixture', bucket='bucket',
                    worker_name='worker', local_dir=root, shared_dir=tmp_path / 'shared')
    monkeypatch.setattr(sync, 'ensure_alias', lambda **kwargs: None)
    expected = _file_bytes(database)
    sync._record_pushed('worker', '.qwenpaw/workspaces/default/history.db', __import__('hashlib').sha256(expected).hexdigest())

    def mc(*args, **kwargs):
        exclusions = [args[i + 1] for i, arg in enumerate(args) if arg == '--exclude']
        for name, data in {'history.db': b'old main', 'history.db-wal': b'old wal', 'history.db-shm': b'old shm'}.items():
            relative = '.qwenpaw/workspaces/default/' + name
            if not any(fnmatch.fnmatch(relative, pattern) for pattern in exclusions):
                (root / relative).write_bytes(data)
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    monkeypatch.setattr(sync, '_mc', mc)
    sync._mirror_prefix(sync.remote_prefix, root)
    with sqlite3.connect(database) as reader:
        assert reader.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert reader.execute('SELECT value FROM history').fetchall() == [('retained',)]
    assert not any(path.name.endswith(('-wal', '-shm')) for path in workspace.iterdir())


def test_corrupt_native_history_never_replaces_the_last_remote_snapshot(tmp_path, monkeypatch):
    workspace = tmp_path / 'worker/.qwenpaw/workspaces/default'
    workspace.mkdir(parents=True)
    database = workspace / 'history.db'
    database.write_bytes(b'SQLite format 3\0' + b'broken history')
    sync = FileSync(endpoint='http://storage', access_key='fixture', secret_key='fixture', bucket='bucket',
                    worker_name='worker', local_dir=tmp_path / 'worker', shared_dir=tmp_path / 'shared')
    monkeypatch.setattr(sync, 'ensure_alias', lambda **kwargs: None)
    monkeypatch.setattr(sync, '_cat_bytes', lambda *args, **kwargs: b'previous valid snapshot')
    calls = []
    monkeypatch.setattr(sync, '_mc', lambda *args, **kwargs: calls.append(args))
    result = sync.flush(timeout=1)
    assert not result['ok']
    assert result['errors'][0]['path'].endswith('history.db')
    assert calls == []
    assert database.read_bytes().endswith(b'broken history')
