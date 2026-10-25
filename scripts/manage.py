"""Local-only installer/operations. No elevated commands or host configuration changes."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.liquidheat-install.json'
IMAGES = ('liquidheat-app:1.0.0', 'liquidheat-web:1.0.0', 'liquidheat-timescale:2.19.3-pg16')


def run(args, **kw):
    return subprocess.run(args, cwd=ROOT, check=True, **kw)


def output(args):
    return run(args, stdout=subprocess.PIPE, text=True).stdout.strip()


def configuration():
    if not CONFIG.exists():
        raise RuntimeError('Run ./install.sh first.')
    return json.loads(CONFIG.read_text())


def compose(c, *args, **kw):
    return run(['docker', 'compose', '--project-name', c['project'], '--file', str(ROOT/'compose.yaml'), *args], **kw)


def valid_bind(value):
    address = ipaddress.IPv4Address(value)
    lan = any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if not (address.is_loopback or lan):
        raise ValueError('Use a loopback or private LAN IPv4 address; public/all-interface binding is disabled.')
    return str(address)


def free_port(bind, port):
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a port between 1024 and 65535.')
    with socket.socket() as s:
        s.bind((bind, port))  # No SO_REUSEADDR: occupied ports must fail.


def preflight():
    if sys.platform != 'linux':
        raise RuntimeError('Run this on the Linux server that will host the application.')
    if not shutil.which('docker'):
        raise RuntimeError('Docker is missing. Follow docs/INSTALL.md; the installer never changes your host runtime/firewall.')
    output(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'])
    output(['docker', 'compose', 'version'])
    print('\nExisting containers (these will not be changed):', flush=True)
    run(['docker', 'ps', '--format', 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'])
    free = shutil.disk_usage(ROOT).free
    docker_root = output(['docker', 'info', '--format', '{{.DockerRootDir}}'])
    if Path(docker_root).exists():
        free = min(free, shutil.disk_usage(docker_root).free)
    mem = next(int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
    print(f'Available RAM: {mem/1024**3:.1f} GiB; available storage: {free/1024**3:.1f} GiB', flush=True)
    if free < 6*1024**3 or mem < 1400*1024**2:
        raise RuntimeError('Need at least 6 GiB free disk and 1,400 MiB available RAM. Free capacity without touching other services first.')


def load_images():
    archive = ROOT / 'docker-images-linux-amd64.tar.gz'
    if archive.exists():
        arch = output(['docker', 'info', '--format', '{{.Architecture}}'])
        if arch not in ('x86_64', 'amd64'):
            raise RuntimeError('This image bundle supports Linux x86-64 only. See source-build instructions for other CPUs.')
        checks = ROOT / 'IMAGE-SHA256SUMS'
        expected = checks.read_text().split()[0]
        h = hashlib.sha256()
        with archive.open('rb') as f:
            for block in iter(lambda: f.read(1024*1024), b''):
                h.update(block)
        if h.hexdigest() != expected:
            raise RuntimeError('Image archive checksum failed; download a fresh release.')
        print('Loading checksum-verified, prebuilt Docker images…', flush=True)
        run(['docker', 'load', '--input', str(archive)])
    for image in IMAGES:
        try:
            output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
        except subprocess.CalledProcessError:
            raise RuntimeError('Prebuilt images missing. Download the complete Linux release bundle, or run scripts/build.sh.') from None


def setup_startup(c):
    if os.geteuid() == 0:
        print('Run as a regular user to configure the optional user-service backup timer. Containers already restart with Docker.')
        return
    options = output(['docker', 'info', '--format', '{{json .SecurityOptions}}'])
    if 'rootless' in options:
        if output(['systemctl', '--user', 'is-enabled', 'docker.service']) != 'enabled':
            raise RuntimeError('Enable your existing rootless Docker user service first: systemctl --user enable docker')
    linger = output(['loginctl', 'show-user', str(os.getuid()), '--property=Linger', '--value'])
    if linger != 'yes':
        raise RuntimeError('Enable user-service boot persistence first: sudo loginctl enable-linger "$USER"; then rerun install.sh.')
    folder = Path.home()/'.config/systemd/user'
    folder.mkdir(parents=True, exist_ok=True)
    name = c['project']
    # Restrict paths to avoid systemd specifier/newline interpretation.
    if re.search(r'[\s%"\\]', str(ROOT)):
        raise RuntimeError('For automatic services, install in a path without whitespace, %, quotes or backslashes.')
    docker = shutil.which('docker')
    env = f'Environment="PATH={Path(docker).parent}:/usr/local/bin:/usr/bin:/bin"\n'
    context = output(['docker', 'context', 'show'])
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', context):
        raise RuntimeError('Unsupported Docker context name for automatic services.')
    env += f'Environment="DOCKER_CONTEXT={context}"\n'
    if os.environ.get('DOCKER_HOST'):
        host = os.environ['DOCKER_HOST']
        if not re.fullmatch(r'unix:///[A-Za-z0-9_./-]+', host):
            raise RuntimeError('Automatic service supports a local Unix Docker socket only.')
        env += f'Environment="DOCKER_HOST={host}"\n'
    units = {
        f'{name}.service': f'[Unit]\nDescription=LiquidHeat isolated stack\nAfter=default.target\n[Service]\nType=oneshot\nRemainAfterExit=yes\nWorkingDirectory={ROOT}\n{env}ExecStart={ROOT}/liquidheat.sh start\nTimeoutStartSec=300\nRestart=on-failure\nRestartSec=30\n[Install]\nWantedBy=default.target\n',
        f'{name}-backup.service': f'[Unit]\nDescription=LiquidHeat bounded local backup\n[Service]\nType=oneshot\nWorkingDirectory={ROOT}\n{env}ExecStart={ROOT}/liquidheat.sh backup\n',
        f'{name}-backup.timer': f'[Unit]\nDescription=LiquidHeat daily backup\n[Timer]\nOnCalendar=*-*-* 04:17:00\nRandomizedDelaySec=600\nPersistent=true\n[Install]\nWantedBy=timers.target\n'
    }
    for unit, text in units.items():
        p = folder/unit
        if p.exists() and 'LiquidHeat' not in p.read_text():
            raise RuntimeError(f'Refusing to overwrite unrelated user unit {unit}')
    for unit, text in units.items():
        (folder/unit).write_text(text)
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'enable', '--now', f'{name}.service', f'{name}-backup.timer'])
    print('Project startup and daily backup user timer enabled. The Docker daemon must start at boot.', flush=True)


def install(args):
    preflight()
    if CONFIG.exists():
        c = configuration()
        if args.project != c['project'] or (args.bind and args.bind != c['bind']) or (args.port and args.port != c['port']):
            raise RuntimeError('Existing installation settings differ. Edit .env and the installation JSON deliberately, or use another directory.')
        print('Existing settings and database password preserved.')
    else:
        if (ROOT/'.env').exists():
            raise RuntimeError('An unmanaged .env exists. Move it aside or use a fresh release directory; it will not be overwritten.')
        project = args.project
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', project):
            raise ValueError('Invalid project name (lowercase letters, digits and hyphens; max 40).')
        if output(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}']):
            raise RuntimeError('That Compose project already exists. Choose --project with a unique name.')
        volumes = output(['docker', 'volume', 'ls', '-q', '--filter', f'name=^{project}_database$'])
        networks = output(['docker', 'network', 'ls', '-q', '--filter', f'name=^{project}_analytics$'])
        if volumes or networks:
            raise RuntimeError('Project-named storage/network already exists. Refusing to reuse unidentified resources.')
        bind = args.bind
        if not bind and not args.non_interactive:
            bind = input('Server LAN IPv4 address (Enter = local access only, 127.0.0.1): ').strip()
        bind = valid_bind(bind or '127.0.0.1')
        port = args.port or 18443
        free_port(bind, port)
        c = {'project': project, 'bind': bind, 'port': port}
        load_images()
        import secrets
        fd = os.open(ROOT/'.env', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(f'POSTGRES_PASSWORD={secrets.token_hex(24)}\nBIND_ADDRESS={bind}\nDASHBOARD_PORT={port}\nBACKFILL_DAYS={args.backfill_days}\n')
        CONFIG.write_text(json.dumps(c, indent=2)+'\n')
    compose(c, 'config', '--quiet')
    compose(c, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '900')
    if not args.no_startup:
        setup_startup(c)
    print(f'\nLiquidHeat is running: http://{c["bind"]}:{c["port"]}/\nInitial real-data backfill can take a while; the chart waits for usable observations.')
    if c['bind'] == '127.0.0.1':
        print('Loopback mode: open it on this server, or use an SSH tunnel. For LAN access choose --bind at first install.')
    if args.no_startup:
        print('Project startup/backup timers skipped; configure these before unattended use.')


def backup(c):
    folder = ROOT/'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder/('heatmap-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'.dump')
    tmp = path.with_suffix('.dump.tmp')
    fd = os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            compose(c, 'exec', '-T', 'db', 'pg_dump', '-U', 'heatmap', '-d', 'heatmap', '-Fc', stdout=f)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    path.with_suffix('.dump.sha256').write_text(f'{h.hexdigest()}  {path.name}\n')
    for old in sorted(folder.glob('heatmap-*.dump'), reverse=True)[3:]:
        old.unlink(); old.with_suffix('.dump.sha256').unlink(missing_ok=True)
    print(f'Backup saved: {path.name} (three newest retained).')


def doctor(c):
    compose(c, 'ps')
    base = f'http://{c["bind"]}:{c["port"]}'
    for endpoint in ('/healthz', '/api/v1/learning'):
        with urllib.request.urlopen(base+endpoint, timeout=30) as r:
            d = json.load(r)
        print(endpoint, json.dumps(d if endpoint == '/healthz' else {k:d.get(k) for k in ('status','activated_at','heatmap_calibration')}, indent=2))
    print('Disk free:', round(shutil.disk_usage(ROOT).free/1024**3, 1), 'GiB')


def main():
    parser = argparse.ArgumentParser(description='LiquidHeat installation and project-only operations')
    sub = parser.add_subparsers(dest='action', required=True)
    ins = sub.add_parser('install')
    ins.add_argument('--bind'); ins.add_argument('--port', type=int)
    ins.add_argument('--project', default='liquidheat')
    ins.add_argument('--backfill-days', type=int, choices=range(1,31), default=30)
    ins.add_argument('--non-interactive', action='store_true')
    ins.add_argument('--no-startup', action='store_true')
    for name in ('start','stop','status','logs','backup','verify-backup','doctor','test'):
        sub.add_parser(name)
    args = parser.parse_args()
    if args.action == 'install':
        install(args); return
    c = configuration()
    if args.action == 'start': compose(c, 'up','-d','--no-build','--pull','never')
    elif args.action == 'stop': compose(c, 'stop')
    elif args.action == 'status': compose(c, 'ps')
    elif args.action == 'logs': compose(c, 'logs','--tail=100')
    elif args.action == 'backup': backup(c)
    elif args.action == 'verify-backup': run([sys.executable, str(ROOT/'scripts/verify_backup.py')])
    elif args.action == 'doctor': doctor(c)
    elif args.action == 'test': compose(c, 'exec','-T','engine','python','-m','unittest','discover','-s','tests','-v')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        print(f'LiquidHeat: {e}', file=sys.stderr)
        sys.exit(1)
