"""Generate or explicitly install a user LaunchAgent on the destination Mac."""
import argparse
from pathlib import Path
import plistlib
import subprocess
import sys
import os

LABEL = 'dev.medo.reddit-pilot'


def make_plist(root, python):
    root = Path(root).resolve()
    return {'Label': LABEL,
            'ProgramArguments': ['/usr/bin/caffeinate', '-i', str(Path(python).resolve()), '-m', 'pilot', 'serve'],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
            'ProcessType': 'Background', 'Umask': 63,
            'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
            'StandardOutPath': str(root / 'runtime' / 'launch.out.log'),
            'StandardErrorPath': str(root / 'runtime' / 'launch.err.log')}


def main():
    p = argparse.ArgumentParser(description='Generate a LaunchAgent; only --install registers it on THIS Mac')
    p.add_argument('--install', action='store_true')
    p.add_argument('--uninstall', action='store_true')
    p.add_argument('--output')
    args = p.parse_args()
    if args.install and args.uninstall:
        p.error('Choose install or uninstall')
    root = Path(__file__).resolve().parents[1]
    if args.install or args.uninstall:
        if sys.platform != 'darwin' or os.getuid() == 0:
            p.error('Run as your normal logged-in macOS user, not root')
        dest = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
        domain = 'gui/' + str(os.getuid())
        if args.uninstall:
            subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=False)
            if dest.exists():
                dest.unlink()
            print('LaunchAgent removed; app data and browser profile retained.')
            return
        if dest.exists():
            p.error('Existing launcher found. Uninstall it explicitly before replacing it.')
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        if not args.output:
            p.error('Provide --output FILE to generate, or --install to register on this Mac')
        dest = Path(args.output)
    (root / 'runtime').mkdir(exist_ok=True, mode=0o700)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plistlib.dumps(make_plist(root, sys.executable)))
    dest.chmod(0o600)
    if args.install:
        subprocess.run(['launchctl', 'bootstrap', domain, str(dest)], check=True)
        print('Installed. Runs after login and restarts after a crash. It does not enable Reddit access.')
    else:
        print('Generated only; no service installed: ' + str(dest))


if __name__ == '__main__':
    main()
