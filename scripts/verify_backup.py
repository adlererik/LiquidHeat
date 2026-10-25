"""Restore a completed backup into a disposable DB in this project's container.

Never overwrites the running database or restores into an operator-supplied name.
"""
import hashlib
import secrets
from manage import ROOT, compose, configuration


def main():
    c = configuration()
    archives = sorted((ROOT/'backups').glob('heatmap-*.dump'), reverse=True)
    if not archives:
        raise RuntimeError('Run ./liquidheat.sh backup first.')
    archive = archives[0]
    expected = archive.with_suffix('.dump.sha256').read_text().split()[0]
    h = hashlib.sha256()
    with archive.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    if h.hexdigest() != expected:
        raise RuntimeError('Backup checksum failed. No restore attempted.')
    import subprocess
    def sql(database, statement):
        return compose(c, 'exec', '-T', 'db', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'heatmap', '-d', database,
                       '-Atc', statement, stdout=subprocess.PIPE, text=True).stdout.strip()
    name = 'liquidheat_restore_'+secrets.token_hex(6)
    if sql('heatmap', f"SELECT count(*) FROM pg_database WHERE datname='{name}'") != '0':
        raise RuntimeError('Unexpected verification DB exists; refusing to reuse it.')
    sql('heatmap', f'CREATE DATABASE {name}')
    try:
        sql(name, 'CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();')
        with archive.open('rb') as f:
            compose(c, 'exec', '-T', 'db', 'pg_restore', '--exit-on-error', '-U', 'heatmap', '-d', name, stdin=f)
        sql(name, 'SELECT timescaledb_post_restore();')
        checks = sql(name, """SELECT count(*) FROM schema_migrations;
            SELECT count(*)>0 FROM heatmap_snapshots;
            SELECT NOT EXISTS(SELECT 1 FROM model_checkpoints c LEFT JOIN heatmap_snapshots s
                ON s.exchange=c.exchange AND s.ts=c.ts AND s.model_version=c.model_version
                WHERE s.ts IS NULL OR s.matrix<>c.matrix);
            SELECT NOT EXISTS(SELECT 1 FROM clld_history WHERE abs(clld_usd-(long_usd-short_usd))>.01);
            SELECT NOT EXISTS(SELECT 1 FROM learning_forecasts WHERE issued_at>=target_start OR source_ts>issued_at);
            SELECT count(*)=1 FROM learning_control;
            SHOW timescaledb.license;""")
        if checks != '3\nt\nt\nt\nt\nt\napache':
            raise RuntimeError('Restored integrity checks failed: '+repr(checks))
        print('PASS: complete backup restored into a disposable DB; migrations, snapshots/checkpoints, CLLD, forecast timing and Apache-only extension verified.')
    finally:
        sql('heatmap', f'DROP DATABASE {name}')


if __name__ == '__main__':
    main()
