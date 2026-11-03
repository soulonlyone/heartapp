"""执行生成的分发入口，避免桌面功能全绿但部署脚本不可用。"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SH = (str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/sh.exe")
      if os.name == "nt" else shutil.which("sh"))
POWERSHELL = shutil.which("powershell.exe")


class TestDockerDistribution(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        spec = importlib.util.spec_from_file_location("relay_builder", ROOT / "tools/build_relay.py")
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        with patch.object(self.builder, "OUT", self.folder), contextlib.redirect_stdout(io.StringIO()):
            archive = self.builder.build_docker_tarball()
        with tarfile.open(archive) as tar:
            # 只读取本测试刚生成的普通文件，不解压链接或其他特殊条目。
            for member in tar.getmembers():
                target = (self.folder / member.name).resolve()
                self.assertTrue(target.is_relative_to(self.folder.resolve()))
                self.assertTrue(member.isfile())
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source:
                    target.write_bytes(source.read())
        self.package = next(self.folder.glob("heartapp-server-*"))

    def run_helper(self, fail_build=False, arguments=()):
        if not SH or not Path(SH).exists():
            self.skipTest("需要 POSIX sh（Windows 可使用 Git 自带的 sh）")
        executable = self.package / "docker"
        executable.write_bytes(
            b'#!/bin/sh\nprintf "%s\\n" "$*" >> calls.txt\n'
            b'if [ "$1" = build ]; then\n'
            b'  [ "$2" = -f ] && [ -f "$3" ] || exit 23\n'
            b'  [ "${FAIL_BUILD:-}" != 1 ] || exit 42\n'
            b'fi\n'
        )
        executable.chmod(0o755)
        env = dict(os.environ, FAIL_BUILD="1" if fail_build else "0")
        # POSIX 的相对 PATH 同时适用于原生 sh 和 Git for Windows sh。
        env["PATH"] = os.pathsep.join((".", str(Path(SH).parent), env.get("PATH", "")))
        result = subprocess.run([SH, "build_and_run.sh", *arguments], cwd=self.package,
                                env=env, capture_output=True, timeout=20)
        calls = self.package / "calls.txt"
        return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    def test_generated_archive_runs_with_correct_dockerfile_and_lf(self):
        helper = (self.package / "build_and_run.sh").read_bytes()
        self.assertNotIn(b"\r", helper)
        result, calls = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(calls[0], "build -f server/Dockerfile -t heartapp-server .")
        self.assertTrue(any(line.startswith("run ") for line in calls))

    def test_failed_build_does_not_remove_or_start_container(self):
        result, calls = self.run_helper(fail_build=True)
        self.assertEqual(result.returncode, 42)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("build "))

    def test_help_does_not_build_or_replace_container(self):
        result, calls = self.run_helper(arguments=("--help",))
        self.assertEqual(result.returncode, 0)
        self.assertIn("HEARTAPP_RELAY_PASSWORD", result.stdout.decode("utf-8"))
        self.assertEqual(calls, [])


class TestDeploymentHelp(unittest.TestCase):
    def run_installer(self, *arguments):
        bash = str(Path(SH).with_name("bash.exe")) if os.name == "nt" and SH else shutil.which("bash")
        if not bash or not Path(bash).exists():
            self.skipTest("需要 Bash")
        env = dict(os.environ)
        env["PATH"] = str(Path(bash).parent) + os.pathsep + env.get("PATH", "")
        return subprocess.run([bash, "server/install-linux.sh", *arguments], cwd=ROOT,
                              env=env, capture_output=True, timeout=10)

    def test_installer_help_lists_parameters_without_root(self):
        result = self.run_installer("--help")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        for argument in ("--host", "--port", "--max-clients", "--room-size", "--user", "--password"):
            self.assertIn(argument, result.stdout.decode("utf-8"))

    def test_installer_missing_value_or_unknown_option_fails_before_install(self):
        for argument in ("--port", "--unknown"):
            result = self.run_installer(argument)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--help", result.stderr.decode("utf-8"))

    def test_relay_help_and_version_exit_without_starting(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        for argument in ("--help", "--version"):
            result = subprocess.run([sys.executable, "-m", "heartapp.relay", argument],
                                    cwd=ROOT, env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            output = result.stdout.decode("utf-8")
            self.assertIn("HEARTAPP_RELAY_PASSWORD" if argument == "--help" else "protocol 2", output)


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "需要 Windows PowerShell")
class TestWindowsServiceLauncher(unittest.TestCase):
    def test_unicode_and_special_password_survive_real_launcher(self):
        def literal(value):
            return "'" + str(value).replace("'", "''") + "'"

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            app = folder / "演示 one's app"
            package = app / "heartapp"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "relay.py").write_text(
                "import os,sys,json\nfrom pathlib import Path\n"
                "Path(os.environ['LAUNCHER_RESULT']).write_text(json.dumps({"
                "'password':os.environ.get('HEARTAPP_RELAY_PASSWORD'),"
                "'args':sys.argv[1:],'cwd':os.getcwd()},ensure_ascii=False),encoding='utf-8')\n"
                "sys.exit(7)\n", encoding="utf-8",
            )
            for password in ("演示口令 %PATH% $() ` & | ' \"", ""):
                with self.subTest(password_provided=bool(password)):
                    launcher = folder / "service-run.ps1"
                    setup = folder / "generate.ps1"
                    setup.write_text(
                        ". " + literal(ROOT / "server/service-windows.ps1") + "\n"
                        "Write-ServiceLauncher -Path " + literal(launcher)
                        + " -WorkingDirectory " + literal(app) + " -Python " + literal(sys.executable)
                        + " -AccessPassword " + literal(password)
                        + " -ListenPort 9000 -ClientLimit 20 -RoomLimit 4\n",
                        encoding="utf-8-sig",
                    )
                    generated = subprocess.run(
                        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(setup)], capture_output=True, timeout=20,
                    )
                    self.assertEqual(generated.returncode, 0, generated.stderr.decode(errors="replace"))
                    self.assertTrue(launcher.read_bytes().startswith(b"\xef\xbb\xbf"))
                    output = folder / "result.json"
                    env = dict(os.environ, LAUNCHER_RESULT=str(output),
                               HEARTAPP_RELAY_PASSWORD="inherited-password")
                    executed = subprocess.run(
                        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(launcher)], env=env, capture_output=True, timeout=20,
                    )
                    self.assertEqual(executed.returncode, 7, executed.stderr.decode(errors="replace"))
                    result = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(result["password"], password or "inherited-password")
                    self.assertEqual(Path(result["cwd"]), app)
                    self.assertEqual(result["args"], ["--host", "0.0.0.0", "--port", "9000",
                                                     "--max-clients", "20", "--room-size", "4"])
