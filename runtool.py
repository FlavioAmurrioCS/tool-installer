#!/usr/bin/env bash
"""":
exec "${LATEST_PYTHON:-$(which python3.11 || which python3.10 || which python3.9 || which python3.8 || which python3.7 || which python3 || which python)}" "${0}" "${@}"
"""
from __future__ import annotations

import argparse
import glob
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from contextlib import contextmanager
from functools import lru_cache
from typing import Callable
from typing import Generator
from typing import NamedTuple
from typing import Sequence
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Protocol  # python3.8+
else:
    Protocol = object


def newest_python() -> str:
    return os.path.realpath(
        subprocess.run(
            ('which python3.11 || which python3.10 || which python3.9 || which python3.8 || which python3.7 || which python3 || which python'),
            shell=True,
            capture_output=True,
            encoding='utf-8',
        )
        .stdout
        .strip(),
    )


class ToolInstaller(NamedTuple):
    bin_dir: str = os.environ.get('TOOL_INSTALLER_BIN_DIR', os.path.join(os.path.expanduser('~'), 'opt', 'bin'))
    package_dir: str = os.environ.get('TOOL_INSTALLER_PACKAGE_DIR', os.path.join(os.path.expanduser('~'), 'opt', 'packages'))
    git_project_dir: str = os.environ.get('TOOL_INSTALLER_GIT_PROJECT_DIR', os.path.join(os.path.expanduser('~'), 'opt', 'git_projects'))

    def __make_executable__(self, filename: str) -> str:
        os.chmod(filename, os.stat(filename).st_mode | stat.S_IEXEC)
        return filename

    def __unpackager__(self, filename: str) -> zipfile.ZipFile | tarfile.TarFile:
        return zipfile.ZipFile(filename) if filename.endswith('.zip') else tarfile.open(filename)

    def __files_in_dir__(self, directory: str) -> list[str]:
        return [
            file
            for file in glob.glob(os.path.join(directory, '**', '*'), recursive=True)
            if os.path.isfile(file)
        ]

    def __executable_from_dir__(self, directory: str, executable_name: str) -> str | None:
        return next((file for file in self.__files_in_dir__(directory) if os.path.basename(file) == executable_name), None) or next((file for file in self.__files_in_dir__(directory) if os.path.basename(file).startswith), None)

    @contextmanager
    def __download__(self, url: str) -> Generator[str, None, None]:
        derive_name = os.path.basename(url)
        with tempfile.TemporaryDirectory() as tempdir:
            download_path = os.path.join(tempdir, derive_name)
            with open(download_path, 'wb') as file:
                with urllib.request.urlopen(url) as f:
                    file.write(f.read())
            yield download_path

    def __get_html__(self, url: str) -> str:
        with urllib.request.urlopen(url) as f:
            html = f.read().decode('utf-8')
            return html

    def executable_from_url(self, url: str, rename: str | None = None) -> str:
        rename = rename or os.path.basename(url)
        target_dir = os.path.join(self.bin_dir, rename)
        if not os.path.exists(target_dir):
            os.makedirs(self.bin_dir, exist_ok=True)
            with self.__download__(url) as download_file:
                shutil.move(download_file, target_dir)
        return self.__make_executable__(target_dir)

    def executable_from_package(
        self,
        package_url: str,
        executable_name: str,
        package_name: str | None = None,
        rename: str | None = None,
    ) -> str:
        package_name = package_name or os.path.basename(package_url)
        package_path = os.path.join(self.package_dir, package_name)
        if not os.path.exists(package_path) or self.__executable_from_dir__(package_path, executable_name) is None:
            with self.__download__(package_url) as tar_zip_file:
                with tempfile.TemporaryDirectory() as tempdir:
                    temp_extract_path = os.path.join(tempdir, 'temp_package')
                    with self.__unpackager__(tar_zip_file) as untar_unzip_file:
                        untar_unzip_file.extractall(temp_extract_path)
                    os.makedirs(self.package_dir, exist_ok=True)
                    shutil.move(temp_extract_path, package_path)

        result = self.__executable_from_dir__(package_path, executable_name)
        if not result:
            print(f'{executable_name} not found in {package_path}', file=sys.stderr)
            raise SystemExit(1)

        executable = self.__make_executable__(result)
        rename = rename or executable_name
        os.makedirs(self.bin_dir, exist_ok=True)
        symlink_path = os.path.join(self.bin_dir, rename)
        if os.path.isfile(symlink_path):
            if not os.path.islink(symlink_path):
                print(f'File is already in {self.bin_dir} with name {os.path.basename(executable)}', file=sys.stderr)
                return executable
            elif os.path.realpath(symlink_path) == os.path.realpath(executable):
                return symlink_path
            else:
                os.remove(symlink_path)

        os.symlink(executable, symlink_path, target_is_directory=False)
        return symlink_path

    def git_install_script(
        self,
        user: str,
        project: str,
        path: str | None = None,
        tag: str = 'master',
        rename: str | None = None,
    ) -> str:
        path = path or project
        url = f'https://raw.githubusercontent.com/{user}/{project}/{tag}/{path}'
        return self.executable_from_url(url=url, rename=rename)

    def git_install_repo(self, git_url: str, path: str, tag: str = 'master') -> str:
        git_project_location = os.path.join(self.git_project_dir, '_'.join(git_url.split('/')[-2:]))
        git_bin = os.path.join(git_project_location, path)
        if not os.path.exists(git_bin):
            command = ('git', 'clone', '-b', tag, git_url, git_project_location)
            subprocess.run(command, check=True)
        return self.__make_executable__(git_bin)

    def __best_url__(self, links: Sequence[str]) -> str | None:
        ignore_pattern = self.__system_ignore_pattern__()
        possible_downloads: list[str] = []

        for download_link in sorted(links, key=len, reverse=True):
            filename = os.path.basename(download_link).lower()
            search_result = ignore_pattern.search(filename)
            if search_result is None:
                possible_downloads.append(download_link)
        return next(iter(possible_downloads), None)

    def git_install_release(
        self,
        user: str,
        project: str,
        tag: str = 'latest',
        binary: str | None = None,
        rename: str | None = None,
    ) -> str:
        binary = binary or project
        rename = rename or binary
        bin_install_path = os.path.join(self.bin_dir, rename)
        package_name = f'{user}_{project}'

        if os.path.exists(bin_install_path):
            return bin_install_path
        possible = self.__executable_from_dir__(os.path.join(self.package_dir, package_name), binary)
        if possible is not None:
            return possible

        url = f'https://github.com/{user}/{project}/releases/{"latest" if tag == "latest" else f"tag/{tag}"}'
        html = self.__get_html__(url)
        download_links: list[str] = ['https://github.com' + link for link in re.findall(f'/{user}/{project}/releases/download/[^"]+', html)]

        download_url = self.__best_url__(download_links)
        if not download_url:
            print(f'Could not find appropiate download from {url}', file=sys.stderr)
            raise SystemExit(1)
        basename = os.path.basename(download_url)
        if basename.endswith('.zip') or '.tar' in basename or basename.endswith('.tgz') or basename.endswith('.tbz'):
            return self.executable_from_package(
                package_url=download_url,
                executable_name=binary,
                package_name=f'{user}_{project}',
                rename=rename,
            )
        return self.executable_from_url(download_url, rename=rename)

    @staticmethod
    @lru_cache(maxsize=1)
    def __system_ignore_pattern__() -> re.Pattern[str]:
        ignore_patterns: set[str] = {
            # invalid file types
            '.txt',
            'license',
            '.md',
            '.sha256',
            '.sha256sum',
            'checksums',
            'sha256sums',
            '.asc',
            '.sig',
            'src',

            # compressed
            # '.tar.gz',
            # '.zip',
            # '.tar.xz',
            # '.tgz',

            # packages
            '.deb',
            '.rpm',

            # operating system
            'darwin',
            'macos',
            'linux',
            'windows',
            'freebsd',
            'netbsd',
            'openbsd',


            # cpu
            'x86_64',

            '32-bit',
            'amd64',
            'x86',

            'i386',
            '386',

            'armv6hf',
            'aarch64',
            'arm64',
            'armhf',
            'armv5',
            'armv5l',
            'armv6',
            'armv6l',
            'armv7',
            'armv7l',

            'mips',
            'mips64',
            'mips64le',
            'mipsle',
            'ppc64',
            'ppc64le',
            's390x',
            'i686',
            'powerpc',
            'i486',



            # extensions
            # '.pyz',
            '.exe',
        }

        system = platform.system().lower()

        valid_tags: list[str] = []
        if system == 'darwin':
            valid_tags.extend(('darwin', 'apple', 'macos'))
        elif system == 'linux':
            valid_tags.extend(('linux', '.deb', '.rpm'))
        elif system == 'windows':
            valid_tags.extend(('windows', '.exe'))

        machine = platform.machine().lower()

        if machine == 'x86_64':
            valid_tags.extend(('x86_64', 'amd64', 'x86'))
        elif machine == 'armv7l':
            valid_tags.extend(('armv7', 'armv6'))
        ignore_patterns.difference_update(valid_tags)
        return re.compile(f"({'|'.join(re.escape(x) for x in ignore_patterns)})")


PRE_CONFIGURED_TOOLS: dict[str, Callable[[ToolInstaller], str]] = {
    'theme.sh': lambda tool_installer: tool_installer.git_install_script(user='lemnos', project='theme.sh', path='bin/theme.sh'),
    'neofetch': lambda tool_installer: tool_installer.git_install_script(user='dylanaraps', project='neofetch'),
    'adb-sync': lambda tool_installer: tool_installer.git_install_script(user='google', project='adb-sync'),
    'adb': lambda tool_installer: tool_installer.executable_from_package(package_url=f'https://dl.google.com/android/repository/platform-tools-latest-{platform.system().lower()}.zip', executable_name='adb', package_name='platform-tools'),
    'repo': lambda tool_installer: tool_installer.executable_from_url(url='https://storage.googleapis.com/git-repo-downloads/repo'),
    'shiv': lambda tool_installer: tool_installer.git_install_release(user='linkedin', project='shiv'),
    'pre-commit': lambda tool_installer: tool_installer.git_install_release(user='pre-commit', project='pre-commit'),
    'fzf': lambda tool_installer: tool_installer.git_install_release(user='junegunn', project='fzf'),
    'rg': lambda tool_installer: tool_installer.git_install_release(user='BurntSushi', project='ripgrep', binary='rg'),
    'docker-compose': lambda tool_installer: tool_installer.git_install_release(user='docker', project='compose', binary='docker-compose'),
    'gdu': lambda tool_installer: tool_installer.git_install_release(user='dundee', project='gdu'),
    'tldr': lambda tool_installer: tool_installer.git_install_release(user='isacikgoz', project='tldr'),
    'lazydocker': lambda tool_installer: tool_installer.git_install_release(user='jesseduffield', project='lazydocker'),
    'lazygit': lambda tool_installer: tool_installer.git_install_release(user='jesseduffield', project='lazygit'),
    'lazynpm': lambda tool_installer: tool_installer.git_install_release(user='jesseduffield', project='lazynpm'),
    'shellcheck': lambda tool_installer: tool_installer.git_install_release(user='koalaman', project='shellcheck'),
    'shfmt': lambda tool_installer: tool_installer.git_install_release(user='mvdan', project='sh', rename='shfmt'),
    'bat': lambda tool_installer: tool_installer.git_install_release(user='sharkdp', project='bat'),
    'fd': lambda tool_installer: tool_installer.git_install_release(user='sharkdp', project='fd'),
    'delta': lambda tool_installer: tool_installer.git_install_release(user='dandavison', project='delta'),
    'btop': lambda tool_installer: tool_installer.git_install_release(user='aristocratos', project='btop'),
    'deno': lambda tool_installer: tool_installer.git_install_release(user='denoland', project='deno'),
    'hadolint': lambda tool_installer: tool_installer.git_install_release(user='hadolint', project='hadolint'),
    'clang-format': lambda tool_installer: tool_installer.git_install_release(user='llvm', project='llvm-project', binary='clang-format'),
    'clang-tidy': lambda tool_installer: tool_installer.git_install_release(user='llvm', project='llvm-project', binary='clang-tidy'),
    'pyenv': lambda tool_installer: tool_installer.git_install_repo(git_url='https://github.com/pyenv/pyenv', path='libexec/pyenv'),
    'nodenv': lambda tool_installer: tool_installer.git_install_repo(git_url='https://github.com/nodenv/nodenv', path='libexec/nodenv'),
}


class __ToolInstallerArgs__(Protocol):
    @property
    def tool(self) -> str:
        ...

    @classmethod
    def __parser__(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('tool', choices=PRE_CONFIGURED_TOOLS.keys())
        return parser

    @classmethod
    def parse_args(cls, argv: Sequence[str] | None = None) -> tuple[__ToolInstallerArgs__, list[str]]:
        return cls.__parser__().parse_known_args(argv)  # type:ignore


def __run_which__(argv: Sequence[str] | None = None, print_tool: bool = True) -> tuple[__ToolInstallerArgs__, list[str], str]:
    args, rest = __ToolInstallerArgs__.parse_args(argv)
    tool_installer = ToolInstaller()
    tool = PRE_CONFIGURED_TOOLS[args.tool](tool_installer)
    if print_tool:
        print(tool)
        raise SystemExit(0)
    return args, rest, tool


def main(argv: Sequence[str] | None = None) -> int:
    args, rest, tool = __run_which__(argv, print_tool=False)
    cmd = (tool, *rest)
    os.execvp(cmd[0], cmd)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
