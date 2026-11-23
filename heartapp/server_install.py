"""服务端安装、升级、卸载。

所有步骤都在 Python 里完成，不再用 bash 拼 systemd 单元。
失败时回滚到安装前的状态（版本指针、配置、单元、服务运行状态）。
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

from . import __version__
from . import server_config as cfg
from . import server_system as system


class InstallError(RuntimeError):
    """安装过程中的可预期失败（信息直接给用户看）。"""


def _require_root() -> None:
    if not hasattr(os, 'geteuid'):
        raise InstallError('服务端只支持 Linux（需要 systemd 与 POSIX 权限模型）')
    if os.geteuid() != 0:
        raise InstallError('需要 root 权限，请用 sudo 运行')


def _require_systemd() -> None:
    if not system.has_systemd():
        raise InstallError('需要 systemd（/run/systemd/system 不存在）')


def _ensure_user(user: str) -> bool:
    """创建系统用户；返回是否新建。"""
    if subprocess.run(['id', user], capture_output=True).returncode == 0:
        return False
    result = subprocess.run(
        ['useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', user],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise InstallError(f'创建用户 {user} 失败：{result.stderr.strip() or "未知错误"}')
    return True


def _copy_release(package_root: Path, version: str) -> Path:
    runtime = package_root / 'runtime'
    if not runtime.is_dir():
        raise InstallError('安装包缺少 runtime/ 目录，请使用自包含安装包')
    for parent in (system.APP_ROOT, system.RELEASES):
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o755)  # 服务用户要能穿过这些目录
    target = system.RELEASES / version
    temporary = Path(tempfile.mkdtemp(prefix='.release-', dir=str(system.RELEASES)))
    # mkdtemp 建出来是 0700；不放开的话 systemd 起服务时 CHDIR 会 Permission denied。
    os.chmod(temporary, 0o755)
    try:
        shutil.copytree(runtime, temporary / 'runtime', symlinks=True)
        for item in (temporary / 'runtime').iterdir():
            destination = temporary / item.name
            item.rename(destination)
        (temporary / 'runtime').rmdir()
        for path in temporary.rglob('*'):
            path.chmod(path.stat().st_mode & ~0o022)
        if hasattr(os, 'chown'):
            os.chown(temporary, 0, 0)
        if target.exists():
            shutil.rmtree(target)
        temporary.rename(target)
        temporary = None  # type: ignore[assignment]
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    launcher = target / system.LAUNCHER_NAME
    if not launcher.is_file():
        raise InstallError('安装包缺少 heartapp-server 启动器')
    launcher.chmod(0o755)
    return target


def _unit_state(action: str, unit: str) -> bool:
    """systemctl 不存在（非 systemd 环境）时按未启用处理，不要抛 FileNotFoundError。"""
    try:
        return subprocess.run(['systemctl', action, '--quiet', unit],
                              check=False, capture_output=True).returncode == 0
    except OSError:
        return False


@contextmanager
def _snapshot(config: dict, user: str) -> Iterator[dict]:
    """记录安装前状态；失败时由调用方决定是否回滚。"""
    state = {
        'unit': system.UNIT_PATH.read_text(encoding='utf-8') if system.UNIT_PATH.exists() else None,
        'config': cfg.CONFIG_PATH.read_bytes() if cfg.CONFIG_PATH.exists() else None,
        'current': os.readlink(system.CURRENT) if system.CURRENT.is_symlink() else None,
        'launcher': os.readlink(system.SYSTEM_BIN) if system.SYSTEM_BIN.is_symlink() else None,
        'service_was_active': _unit_state('is-active', system.SERVICE),
        'service_was_enabled': _unit_state('is-enabled', system.SERVICE),
        'timer_was_enabled': _unit_state('is-enabled', 'heartapp-ip-renew.timer'),
        'user': user,
    }
    yield state


def _rollback(state: dict) -> None:
    """尽力恢复；每一步失败都不影响后续步骤。"""
    print('  正在回滚 …', flush=True)
    try:
        if state['current']:
            system.CURRENT.parent.mkdir(parents=True, exist_ok=True)
            if system.CURRENT.is_symlink() or system.CURRENT.exists():
                system.CURRENT.unlink()
            system.CURRENT.symlink_to(state['current'])
        else:
            if system.CURRENT.is_symlink():
                system.CURRENT.unlink()
        if state['unit'] is None:
            system.UNIT_PATH.unlink(missing_ok=True)
        else:
            system.UNIT_PATH.write_text(state['unit'], encoding='utf-8')
        if state['config'] is None:
            cfg.CONFIG_PATH.unlink(missing_ok=True)
        else:
            cfg.CONFIG_PATH.write_bytes(state['config'])
            os.chmod(cfg.CONFIG_PATH, 0o640)
        if state['launcher'] is None:
            system.SYSTEM_BIN.unlink(missing_ok=True)
        else:
            system.SYSTEM_BIN.unlink(missing_ok=True)
            system.SYSTEM_BIN.symlink_to(state['launcher'])
        system.systemctl('daemon-reload', check=False)
        if not state['service_was_enabled']:
            system.systemctl('disable', system.SERVICE, check=False)
        if state['service_was_active']:
            system.systemctl('restart', system.SERVICE, check=False)
        else:
            system.systemctl('stop', system.SERVICE, check=False)
        if not state['timer_was_enabled'] and system.RENEW_TIMER.exists():
            system.systemctl('disable', '--now', 'heartapp-ip-renew.timer', check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f'  回滚未完全成功：{exc}', flush=True)


def _print_journal_tail(lines: int = 20) -> None:
    result = subprocess.run(
        ['journalctl', '-u', system.SERVICE, '-n', str(lines), '--no-pager'],
        capture_output=True, text=True, check=False,
    )
    output = (result.stdout or '').strip()
    if output:
        print(output, flush=True)


def install(package_root: Path, config: dict, *, open_port: bool = True,
            dry_run: bool = False, version: str = '') -> int:
    """安装或重新配置，返回进程退出码。"""
    if dry_run:
        pass
    else:
        _require_root()
        _require_systemd()
    cfg.validate(config)
    version = version or __version__

    if dry_run:
        print('dry-run：将执行以下操作（未做任何修改）')
        print(f'  - 安装版本 {version} 到 {system.RELEASES / version}')
        print(f'  - 写入配置 {cfg.CONFIG_PATH}')
        print(f'  - 写入单元 {system.UNIT_PATH}')
        print(system.render_unit(config, version=version))
        return 0

    with _snapshot(config, config['user']) as state:
        try:
            print(f'  安装版本 {version} …', flush=True)
            created_user = _ensure_user(config['user'])

            release = _copy_release(package_root, version)
            if system.CURRENT.is_symlink():
                system.CURRENT.unlink()
            elif system.CURRENT.exists():
                raise InstallError(f'{system.CURRENT} 已存在且不是符号链接，请先备份或卸载')
            system.CURRENT.symlink_to(release)

            cert = key = None
            if cfg.uses_tls(config):
                from . import server_tls
                cert, key = server_tls.setup(config, user=config['user'],
                                             open_port=open_port)

            cfg.save(config, group=config['user'])
            system.write_unit(system.render_unit(config, cert=cert, key=key, version=version))

            system.SYSTEM_BIN.parent.mkdir(parents=True, exist_ok=True)
            system.SYSTEM_BIN.unlink(missing_ok=True)
            system.SYSTEM_BIN.symlink_to(system.CURRENT / system.LAUNCHER_NAME)

            system.systemctl('daemon-reload')
            system.systemctl('enable', system.SERVICE)
            system.systemctl('restart', system.SERVICE)

            ok, message = system.health(config, retries=5, delay=0.8)
            if not ok:
                _print_journal_tail()
                raise InstallError(f'健康检查失败：{message}')

            if open_port and cfg.needs_plaintext(config):
                if system.firewall_open(config['port']):
                    print(f'  已在防火墙放行 {config["port"]}/tcp', flush=True)
                else:
                    print('  未检测到启用的 firewalld/ufw，请手动放行端口', flush=True)
        except Exception:
            _rollback(state)
            if created_user:
                subprocess.run(['userdel', config['user']], capture_output=True)
            raise

    _print_summary(config, version)
    return 0


def _print_summary(config: dict, version: str) -> None:
    print()
    print('=' * 60)
    print(f' 安装完成（v{version}）')
    print('=' * 60)
    print(f' 监听      {config["host"]}:{config["port"]}')
    print(f' 代码      {system.CURRENT}')
    print(f' 配置      {cfg.CONFIG_PATH}')
    print(f' 运行用户  {config["user"]}')
    print()
    print(' 管理')
    print('   sudo heartapp-server status')
    print('   sudo heartapp-server config show')
    print('   sudo heartapp-server uninstall')
    print()
    print(' 客户端连接')
    print(f'   {cfg.connect_address(config)}')
    if cfg.uses_tls(config):
        print(f'   证书每 12 小时检查续期：systemctl status heartapp-ip-renew.timer')
    elif cfg.needs_plaintext(config):
        print('   ⚠ 未加密：密码和心率在网络上可见，仅建议在可信网络使用')
    print()


def uninstall(*, keep_config: bool = False, open_port: bool = True,
              remove_user: bool = False, dry_run: bool = False) -> int:
    """卸载服务端；默认保留数据和用户目录以外的内容都清掉。"""
    if not dry_run:
        _require_root()
    config = cfg.load()

    targets = [
        ('systemd 服务', system.UNIT_PATH),
        ('续期任务', system.RENEW_SERVICE),
        ('续期定时器', system.RENEW_TIMER),
        ('程序目录', system.APP_ROOT),
        ('命令入口', system.SYSTEM_BIN),
        ('TLS 目录', system.TLS_DIR),
        ('ACME 目录', system.ACME_DIR),
        ('ACME 工作目录', system.ACME_WORK),
        ('ACME 日志', system.ACME_LOGS),
    ]
    if not keep_config:
        targets.append(('配置文件', cfg.CONFIG_PATH))
        targets.append(('旧版配置', cfg.LEGACY_ENV_PATH))

    if dry_run:
        print('dry-run：将删除以下内容（未做任何修改）')
        for label, path in targets:
            if path.exists() or path.is_symlink():
                print(f'  - {label}: {path}')
        if remove_user:
            print(f'  - 系统用户: {config["user"]}')
        if open_port:
            print(f'  - 防火墙规则: {config["port"]}/tcp')
        return 0

    print('  停止并禁用服务 …', flush=True)
    system.systemctl('disable', '--now', system.SERVICE, check=False)
    system.systemctl('disable', '--now', 'heartapp-ip-renew.timer', check=False)

    for label, path in targets:
        if path == system.SYSTEM_BIN:
            path.unlink(missing_ok=True)
            print(f'  已删除 {label}', flush=True)
            continue
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            print(f'  已删除 {label}', flush=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            print(f'  已删除 {label}', flush=True)
    if not keep_config:
        try:
            cfg.CONFIG_PATH.parent.rmdir()
        except OSError:
            pass

    system.systemctl('daemon-reload', check=False)
    system.systemctl('reset-failed', check=False)

    if open_port:
        if system.firewall_close(config['port']):
            print(f'  已关闭防火墙 {config["port"]}/tcp', flush=True)

    if remove_user:
        result = subprocess.run(['userdel', config['user']], capture_output=True, text=True)
        if result.returncode == 0:
            print(f'  已删除用户 {config["user"]}', flush=True)
        else:
            print(f'  删除用户失败：{result.stderr.strip()}', flush=True)

    print()
    print('卸载完成。' + ('（配置已保留）' if keep_config else ''))
    return 0


def status() -> int:
    """打印当前状态；返回 0 表示服务健康。"""
    config = cfg.load()
    print('配置：')
    for key, value in cfg.redacted(config).items():
        print(f'  {key:12} {value}')
    print(f'  {"证书":12} {"启用" if cfg.uses_tls(config) else "未启用"}')

    active = subprocess.run(['systemctl', 'is-active', system.SERVICE],
                            capture_output=True, text=True, check=False).stdout.strip()
    enabled = subprocess.run(['systemctl', 'is-enabled', system.SERVICE],
                             capture_output=True, text=True, check=False).stdout.strip()
    print(f'\n服务：{active or "unknown"}（开机自启：{enabled or "unknown"}）')

    ok, message = system.health(config, retries=1)
    print(f'健康：{"正常" if ok else "异常"}' + (f'（{message}）' if message else ''))
    print(f'客户端地址：{cfg.connect_address(config)}')
    return 0 if ok else 1
