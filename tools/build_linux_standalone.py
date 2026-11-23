"""用已校验的可移植 CPython 和离线 wheel 构建 Linux 自包含分发包。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--version', default='1.4.2-standalone-test')
    parser.add_argument('--output', type=Path, default=ROOT / '_test_output/standalone')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.version):
        raise SystemExit('版本号只能包含字母、数字、点、横线和下划线')
    if hashlib.sha256(args.python_archive.read_bytes()).hexdigest() != args.sha256.lower():
        raise SystemExit('可移植 Python 校验失败')
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='heartapp-build-') as temp:
        folder = Path(temp) / ('heartapp-server-' + args.version)
        runtime = folder / 'runtime'
        runtime.mkdir(parents=True)
        with tarfile.open(args.python_archive) as archive:
            archive.extractall(runtime, filter='data')
        python = runtime / 'python/bin/python3'
        subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--no-index',
                        '--find-links', str(args.wheelhouse.resolve()),
                        'certbot>=5.4,<6', 'websockets>=15,<16'], check=True)
        site = next((runtime / 'python/lib').glob('python3.*/site-packages'))
        package = site / 'heartapp'
        package.mkdir()
        # 安装/配置/证书逻辑全部在包里，安装时不需要任何 bash 脚本。
        modules = (
            '__init__.py', 'room_protocol.py', 'relay.py', 'lobby.py',
            'server_cli.py', 'server_config.py', 'server_system.py',
            'server_install.py', 'server_tls.py',
        )
        for name in modules:
            shutil.copyfile(ROOT / 'heartapp' / name, package / name)
        launcher = runtime / 'heartapp-server'
        launcher.write_text(
            '#!/bin/sh\nset -eu\n'
            'target=$(readlink -f "$0")\n'
            'base=$(dirname "$target")\n'
            'exec "$base/python/bin/python3" -I -m heartapp.server_cli "$@"\n',
            encoding='utf-8')
        launcher.chmod(0o755)
        server = folder / 'server'
        server.mkdir()
        for name in ('README.md', 'Linux说明.md', 'Linux自包含测试说明.md'):
            source = ROOT / 'server' / name
            if source.is_file():
                (server / name).write_text(source.read_text(encoding='utf-8'),
                                           encoding='utf-8', newline='\n')
        shutil.copyfile(ROOT / 'LICENSE', folder / 'LICENSE')
        subprocess.run([str(launcher), '--version'], check=True)
        subprocess.run([str(launcher), 'acme', '--version'], check=True)
        packages = subprocess.check_output(
            [str(python), '-I', '-m', 'pip', 'list', '--format=json'], text=True)
        (runtime / 'BUILD-INFO.json').write_text(json.dumps(
            dict(version=args.version, python_sha256=args.sha256, packages=json.loads(packages)),
            ensure_ascii=False, indent=2), encoding='utf-8')
        target = args.output / f'HeartApp-Server-v{args.version}-linux.tar.gz'
        with tarfile.open(target, 'w:gz') as archive:
            archive.add(folder, arcname=folder.name)
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        (args.output / 'SHA256SUMS.txt').write_text(
            checksum + '  ' + target.name + '\n', encoding='ascii')
        print(f'安装包 {target}，{target.stat().st_size / 1048576:.1f} MiB')


if __name__ == '__main__':
    main()
