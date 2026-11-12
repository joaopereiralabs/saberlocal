import argparse
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.configuration import render, validate, openrc, systemd
from scripts.system import detect, package_commands
from scripts.storage import edit_fstab, require, file_limit, FAT_LIMIT
from scripts.install import discover_interface, disable_interface_stanza, project_checkout
from scripts.display import show, text_for
from scripts.content import download
from scripts.files import managed_write, restore, check_conflicts

ROOT = Path(__file__).resolve().parents[1]


def config(backend='openrc'):
    result = json.loads((ROOT / 'config.example.json').read_text())
    result.update(interface='wlan0', password='test-password-unique', data_dir='/mnt/saberlocal/acervo',
                  backend=backend, os='alpine' if backend == 'openrc' else 'ubuntu',
                  arch='aarch64', version='3.24.2', storage_fstype='vfat')
    return result


class BackendTests(unittest.TestCase):
    def test_alpine_arm64_detection(self):
        system = detect('ID=alpine\nVERSION_ID=3.24.2\n', 'aarch64')
        self.assertEqual(system, dict(os='alpine', version='3.24.2', arch='aarch64', backend='openrc'))

    def test_debian_and_ubuntu_detection(self):
        for distro in ('debian', 'ubuntu'):
            self.assertEqual(detect(f'ID="{distro}"\nVERSION_ID="24.04"', 'aarch64')['backend'], 'systemd')
        with self.assertRaises(RuntimeError):
            detect('ID=fedora')

    def test_alpine_packages_never_use_apt_or_pip_builds(self):
        commands = package_commands(detect('ID=alpine', 'aarch64'))
        self.assertTrue(all(c[0] == 'apk' for c in commands))
        self.assertIn('kiwix-tools', commands[-1])
        self.assertIn('py3-waitress', commands[-1])
        self.assertNotIn('kiwix-tools', package_commands(detect('ID=alpine'), True)[-1])

    def test_debian_packages_keep_apt_backend(self):
        commands = package_commands(detect('ID=ubuntu'))
        self.assertEqual(commands[0], ['apt-get', 'update'])
        self.assertIn('python3-waitress', commands[-1])

    def test_alpine_service_order_and_finite_restarts(self):
        files = render(config())
        self.assertFalse(any('/systemd/' in p for p in files))
        self.assertIn('need localmount saberlocal-storage', files['/etc/init.d/saberlocal-network'][0])
        portal = files['/etc/init.d/saberlocal-portal'][0]
        self.assertIn('supervisor="supervise-daemon"', portal)
        self.assertIn('respawn_max=5', portal)
        self.assertIn('respawn_period=0', portal)
        self.assertIn('command_user="saberlocal"', portal)
        self.assertNotIn('dev.py', portal)
        self.assertEqual(files['/etc/saberlocal/hostapd.conf'][1], 0o600)

    def test_systemd_dependencies_and_restart_limits(self):
        files = render(config('systemd'))
        self.assertFalse(any('/init.d/' in p for p in files))
        portal = files['/etc/systemd/system/saberlocal-portal.service'][0]
        self.assertIn('Requires=saberlocal-storage.service', portal)
        self.assertIn('StartLimitBurst=5', portal)
        self.assertIn('User=saberlocal', portal)
        self.assertIn('production.py', portal)

    def test_port80_dns_mdns_and_no_eth_forward(self):
        files = render(config())
        self.assertIn('listen 10.42.0.1:80', files['/etc/saberlocal/nginx.conf'][0])
        self.assertIn('address=/saber.local/10.42.0.1', files['/etc/saberlocal/dnsmasq.conf'][0])
        self.assertIn('allow-interfaces=wlan0', files['/etc/saberlocal/avahi.conf'][0])
        self.assertIn('iifname "wlan0" drop', files['/etc/saberlocal/firewall.nft'][0])
        self.assertNotIn('flush ruleset', files['/etc/saberlocal/firewall.nft'][0])
        self.assertNotIn('eth0', '\n'.join(v[0] for v in files.values()))

    def test_ifupdown_does_not_change_ethernet(self):
        before = 'auto lo eth0 wlan0\niface lo inet loopback\niface eth0 inet dhcp\n    mtu 1500\niface wlan0 inet dhcp\n    wpa-conf /etc/wpa.conf\n'
        after = disable_interface_stanza(before, 'wlan0')
        self.assertIn('auto lo eth0\n', after)
        self.assertIn('iface eth0 inet dhcp\n    mtu 1500\n', after)
        self.assertNotIn('wlan0', after)
        self.assertEqual(disable_interface_stanza(after, 'wlan0'), after)

    @patch('scripts.install.check_ap')
    @patch('scripts.install.run')
    def test_interface_detection_selects_an_ap_capable_radio(self, run, check):
        run.return_value.stdout = 'Interface wlan0\nInterface wlan1\n'
        def check_interface(name):
            if name == 'wlan0':
                raise RuntimeError('managed only')
        check.side_effect = check_interface
        self.assertEqual(discover_interface(), 'wlan1')

    @patch('scripts.install.run')
    def test_migration_checks_origin_before_adopting_other_checkout(self, run):
        run.return_value.stdout = 'https://github.com/joaopereiralabs/saberlocal.git\n'
        self.assertTrue(project_checkout('/a-different-checkout'))
        run.return_value.stdout = 'https://github.com/someone/unrelated.git\n'
        self.assertFalse(project_checkout('/a-different-checkout'))


class StorageTests(unittest.TestCase):
    def test_uuid_fstab_idempotent_and_preserves_other_entries(self):
        old = '# sistema\nUUID=root / ext4 defaults 0 1\n'
        new = edit_fstab(old, config())
        self.assertIn(old.strip(), new)
        self.assertEqual(new, edit_fstab(new, config()))
        self.assertEqual(new.count('UUID=21B5-12FD'), 1)
        self.assertIn('fmask=0133,dmask=0022', new)
        self.assertIn('noauto,nosuid,nodev,noexec', new)

    def test_fstab_conflicts_are_not_overwritten(self):
        with self.assertRaises(RuntimeError):
            edit_fstab('UUID=other /mnt/saberlocal ext4 defaults 0 2\n', config())
        old = 'UUID=21B5-12FD /mnt/saberlocal vfat defaults 0 0\n'
        self.assertEqual(edit_fstab(old, config()), old)

    def test_rollback_only_removes_own_fstab_block(self):
        text = edit_fstab('UUID=root / ext4 defaults 0 1\n', config())
        text += 'UUID=added-later /data ext4 defaults 0 2\n'
        restored = edit_fstab(text)
        self.assertIn('UUID=added-later', restored)
        self.assertIn('UUID=root', restored)
        self.assertNotIn('21B5-12FD', restored)

    @patch('scripts.storage.mount_record')
    def test_absent_wrong_uuid_and_readonly_are_rejected(self, record):
        for value in [None, {'uuid':'WRONG','options':'rw'}, {'uuid':'21B5-12FD','options':'ro'}]:
            record.return_value = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                require(config())
        record.return_value = {'uuid':'21b5-12fd','options':'rw,nosuid'}
        self.assertEqual(require(config())['uuid'], '21b5-12fd')

    def test_fat32_exact_file_boundary(self):
        file_limit(config(), FAT_LIMIT)
        with self.assertRaisesRegex(RuntimeError, 'FAT32'):
            file_limit(config(), FAT_LIMIT + 1)
        ext = config()
        ext['storage_fstype'] = 'ext4'
        file_limit(ext, FAT_LIMIT + 1)

    def test_fat_oversized_download_is_rejected_before_transfer(self):
        class Head(io.BytesIO):
            headers = {'Content-Length': str(2**32)}
        calls = []
        def opener(request, timeout):
            calls.append(request.get_method())
            return Head()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'FAT32'):
                download({'url':'https://download.kiwix.org/zim/test.zim','sha256':'0'*64},
                         Path(folder)/'test.zim', 20*1024**3, opener, max_file_size=FAT_LIMIT)
            self.assertEqual(calls, ['HEAD'])
            self.assertFalse(list(Path(folder).iterdir()))


class DisplayTests(unittest.TestCase):
    def test_unsupported_display_is_explicit_and_nonblocking(self):
        self.assertEqual(show('ready', config())['state'], 'unsupported')

    def test_segment_fallback(self):
        cfg = config()
        self.assertEqual(text_for('ready', cfg), 'SAbE')
        cfg['display_width'] = 2
        self.assertEqual(text_for('ready', cfg), 'SL')
        cfg['display_width'] = 16
        self.assertEqual(text_for('ready', cfg), 'SaberLocal')

    @patch('scripts.display.subprocess.run')
    def test_explicit_command_has_no_shell_and_a_timeout(self, run):
        with tempfile.NamedTemporaryFile() as executable:
            cfg = config()
            cfg.update(display_backend='command', display_command=[executable.name, '{text}'])
            show('ready', cfg)
            self.assertEqual(run.call_args.args[0], [executable.name, 'SAbE'])
            self.assertEqual(run.call_args.kwargs['timeout'], 3)
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_arbitrary_device_is_not_a_sysfs_adapter(self):
        cfg = config()
        cfg.update(display_backend='sysfs', display_path='/dev/mem')
        with self.assertRaises(ValueError):
            show('ready', cfg)


class RecoveryTests(unittest.TestCase):
    def test_reinstall_and_rollback_preserve_original_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'interfaces'
            original.write_text('eth0: original\n')
            with patch('scripts.files.STATE', root / 'state'):
                managed_write(original, 'eth0: unchanged\nwlan0: project')
                managed_write(original, 'eth0: unchanged\nwlan0: project')
                check_conflicts()
                restore()
            self.assertEqual(original.read_text(), 'eth0: original\n')

    def test_rollback_does_not_overwrite_later_operator_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'config'
            with patch('scripts.files.STATE', root / 'state'):
                managed_write(target, 'installed')
                target.write_text('operator edit')
                with self.assertRaises(RuntimeError):
                    check_conflicts()
                with self.assertRaises(RuntimeError):
                    restore()
            self.assertEqual(target.read_text(), 'operator edit')


if __name__ == '__main__':
    unittest.main()
