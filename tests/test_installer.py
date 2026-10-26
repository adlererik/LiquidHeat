"""Installer safeguards with disposable files, never a Docker daemon."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('manage', Path(__file__).parents[1]/'scripts/manage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class InstallerSafety(unittest.TestCase):
    def test_backup_schema_gate_supports_older_valid_backups_and_rejects_future_or_broken(self):
        for count in range(3,8):
            self.assertTrue(m.valid_backup_checks('\n'.join([str(count)]+['t']*5+['apache']),7))
        for count in [0,2,8]:
            self.assertFalse(m.valid_backup_checks('\n'.join([str(count)]+['t']*5+['apache']),7))
        self.assertFalse(m.valid_backup_checks('7\nt\nt\nf\nt\nt\napache',7))
        self.assertFalse(m.valid_backup_checks('',7))

    def test_public_and_wildcard_addresses_rejected(self):
        for address in ('0.0.0.0', '8.8.8.8', '169.254.0.1', '::1'):
            with self.assertRaises(ValueError): m.valid_bind(address)
        self.assertEqual(m.valid_bind('127.0.0.1'), '127.0.0.1')
        self.assertEqual(m.valid_bind('10.1.2.3'), '10.1.2.3')

    def test_occupied_port_refused_without_reuse(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            with self.assertRaises(OSError):
                m.free_port('127.0.0.1', listener.getsockname()[1])

    def test_privileged_and_invalid_ports_rejected(self):
        for port in (0, 22, 65536):
            with self.assertRaises(ValueError): m.free_port('127.0.0.1', port)

    def test_closed_connection_time_wait_does_not_block_safe_reuse(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0));listener.listen()
            port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port)) as client:
                accepted,_=listener.accept()
                accepted.close()  # The accepting side initiates close/TIME_WAIT.
                self.assertEqual(client.recv(1),b'')
        m.free_port('127.0.0.1',port)

    def test_unmanaged_env_is_never_overwritten(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); p = root/'.env'; p.write_text('private-existing-value')
            with patch.object(m,'ROOT',root), patch.object(m,'CONFIG',root/'.liquidheat-install.json'), patch.object(m,'preflight'):
                with self.assertRaisesRegex(RuntimeError,'unmanaged'):
                    m.install(Namespace(project='liquidheat'))
            self.assertEqual(p.read_text(),'private-existing-value')

    def test_existing_install_preserves_password_and_skips_image_load(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); c={'project':'liquidheat','bind':'127.0.0.1','port':18443}
            (root/'.env').write_text('private-existing-value')
            cfg=root/'.liquidheat-install.json';cfg.write_text(json.dumps(c))
            with patch.object(m,'ROOT',root),patch.object(m,'CONFIG',cfg),patch.object(m,'preflight'),patch.object(m,'load_images') as load,patch.object(m,'compose') as compose:
                m.install(Namespace(project='liquidheat',bind=None,port=None,no_startup=True))
                load.assert_not_called()
                self.assertEqual(compose.call_args_list[-1].args[1:4],('up','-d','--no-build'))
            self.assertEqual((root/'.env').read_text(),'private-existing-value')

    def test_unknown_compose_project_refused(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(m,'ROOT',root),patch.object(m,'CONFIG',root/'missing'),patch.object(m,'preflight'),patch.object(m,'output',return_value='existing-container'):
                with self.assertRaisesRegex(RuntimeError,'already exists'):
                    m.install(Namespace(project='liquidheat'))
            self.assertFalse((root/'.env').exists())

    def test_unknown_existing_volume_refused(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(m,'ROOT',root),patch.object(m,'CONFIG',root/'missing'),patch.object(m,'preflight'),patch.object(m,'output',side_effect=['','liquidheat_database','']):
                with self.assertRaisesRegex(RuntimeError,'unidentified'):
                    m.install(Namespace(project='liquidheat'))
            self.assertFalse((root/'.env').exists())

    def test_backup_failure_never_replaces_completed_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(m,'ROOT',root),patch.object(m,'compose',side_effect=subprocess.CalledProcessError(1,['pg_dump'])):
                with self.assertRaises(subprocess.CalledProcessError): m.backup({'project':'test'})
            self.assertEqual(list((root/'backups').iterdir()),[])

    def test_unrelated_systemd_unit_prevents_any_unit_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); units=root/'.config/systemd/user'; units.mkdir(parents=True)
            unrelated=units/'demo-backup.service'; unrelated.write_text('Unrelated service')
            def fake_output(args):
                if args[:2]==['docker','info']: return '[]'
                if args[0]=='loginctl': return 'yes'
                if args[:3]==['docker','context','show']: return 'default'
                raise AssertionError(args)
            with patch.object(m,'ROOT',root),patch.object(m.Path,'home',return_value=root),patch.object(m.os,'geteuid',return_value=1000),patch.object(m,'output',side_effect=fake_output),patch.object(m.shutil,'which',return_value='/usr/bin/docker'),patch.dict(m.os.environ,{},clear=True),patch.object(m,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'unrelated user unit'):
                    m.setup_startup({'project':'demo'})
                run.assert_not_called()
            self.assertFalse((units/'demo.service').exists())
            self.assertEqual(unrelated.read_text(),'Unrelated service')


if __name__ == '__main__': unittest.main()
