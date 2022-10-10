#!/usr/bin/env bash
"""":
exec "${LATEST_PYTHON:-$(which python3.11 || which python3.10 || which python3.9 || which python3.8 || which python3.7 || which python3 || which python)}" "${0}" "${@}"
"""
from __future__ import annotations

import argparse
import glob
import itertools
import logging
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
from typing import Generator
from typing import NamedTuple
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union

if TYPE_CHECKING:
    from typing import Protocol  # python3.8+
else:
    Protocol = object


def newest_python() -> str:
    return os.path.realpath(
        subprocess.run(
            ('{ which python3.11 || which python3.10 || which python3.9 || which python3.8 || which python3.7 || which python3 || which python; } 2>/dev/null'),
            shell=True,
            capture_output=True,
            encoding='utf-8',
        )
        .stdout
        .strip(),
    )


class GithubScriptInstallSource(NamedTuple):
    user: str
    project: str
    path: str | None = None
    tag: str = 'master'
    rename: str | None = None


class GithubReleaseInstallSource(NamedTuple):
    user: str
    project: str
    tag: str = 'latest'
    binary: str | None = None
    rename: str | None = None


class GitProjectInstallSource(NamedTuple):
    git_url: str
    path: str
    tag: str = 'master'
    pull: bool = False


class UrlInstallSource(NamedTuple):
    url: str
    rename: str | None = None


class ZipTarInstallSource(NamedTuple):
    package_url: str
    executable_name: str
    package_name: str | None = None
    rename: str | None = None


class ShivInstallSource(NamedTuple):
    package: str
    command: str | None = None


class PipxInstallSource(NamedTuple):
    package: str
    command: str | None = None


class GroupUrlInstallSource(NamedTuple):
    links: Sequence[str]
    binary: str
    rename: str | None = None
    package_name: str | None = None


ToolInstallerInstallSource = Union[
    GithubScriptInstallSource,
    GithubReleaseInstallSource,
    GitProjectInstallSource,
    UrlInstallSource,
    ZipTarInstallSource,
    ShivInstallSource,
    PipxInstallSource,
    GroupUrlInstallSource,
]


__node_download_links___ = [
    f'https://nodejs.org/dist/v16.16.0/node-v16.16.0{x}'
    for x in (
        '-aix-ppc64.tar.gz', '-darwin-arm64.tar.gz', '-darwin-arm64.tar.xz',
        '-darwin-x64.tar.gz', '-darwin-x64.tar.xz', '-headers.tar.gz',
        '-headers.tar.xz', '-linux-arm64.tar.gz', '-linux-arm64.tar.xz',
        '-linux-armv7l.tar.gz', '-linux-armv7l.tar.xz', '-linux-ppc64le.tar.gz',
        '-linux-ppc64le.tar.xz', '-linux-s390x.tar.gz', '-linux-s390x.tar.xz',
        '-linux-x64.tar.gz', '-linux-x64.tar.xz', '-win-x64.7z', '-win-x64.zip',
        '-win-x86.7z', '-win-x86.zip', '-x64.msi', '-x86.msi', '.pkg', '.tar.gz', '.tar.xz',
    )
]

__rclone_download_links___ = [
    f'https://downloads.rclone.org/rclone-current-{x}'
    for x in (
        'windows-amd64.zip', 'osx-amd64.zip', 'linux-amd64.zip', 'linux-amd64.deb',
        'linux-amd64.rpm', 'freebsd-amd64.zip', 'netbsd-amd64.zip', 'openbsd-amd64.zip',
        'plan9-amd64.zip', 'solaris-amd64.zip', 'windows-386.zip', 'linux-386.zip',
        'linux-386.deb', 'linux-386.rpm', 'freebsd-386.zip', 'netbsd-386.zip',
        'openbsd-386.zip', 'plan9-386.zip', 'linux-arm.zip', 'linux-arm.deb',
        'linux-arm.rpm', 'freebsd-arm.zip', 'netbsd-arm.zip', 'linux-arm-v7.zip',
        'linux-arm-v7.deb', 'linux-arm-v7.rpm', 'freebsd-arm-v7.zip', 'netbsd-arm-v7.zip',
        'osx-arm64.zip', 'linux-arm64.zip', 'linux-arm64.deb', 'linux-arm64.rpm',
        'linux-mips.zip', 'linux-mips.deb', 'linux-mips.rpm', 'linux-mipsle.zip',
        'linux-mipsle.deb', 'linux-mipsle.rpm',
    )
]

__heroku_download_links___ = [
    f'https://cli-assets.heroku.com/heroku-{x}.tar.gz'
    for x in ('darwin-x64', 'linux-x64', 'linux-arm', 'win32-x64', 'win32-x86')
]


PRE_CONFIGURED_TOOLS: dict[str, ToolInstallerInstallSource] = {
    # GithubScriptInstallSource
    'theme.sh': GithubScriptInstallSource(user='lemnos', project='theme.sh', path='bin/theme.sh'),
    'neofetch': GithubScriptInstallSource(user='dylanaraps', project='neofetch'),
    'adb-sync': GithubScriptInstallSource(user='google', project='adb-sync'),
    'bb': GithubScriptInstallSource(user='FlavioAmurrioCS', project='dot', path='.dot/bin/scripts/bb'),

    # GithubReleaseInstallSource
    'shiv': GithubReleaseInstallSource(user='linkedin', project='shiv'),
    'fzf': GithubReleaseInstallSource(user='junegunn', project='fzf'),
    'rg': GithubReleaseInstallSource(user='BurntSushi', project='ripgrep', binary='rg'),
    'docker-compose': GithubReleaseInstallSource(user='docker', project='compose', binary='docker-compose'),
    'gdu': GithubReleaseInstallSource(user='dundee', project='gdu'),
    'tldr': GithubReleaseInstallSource(user='isacikgoz', project='tldr'),
    'lazydocker': GithubReleaseInstallSource(user='jesseduffield', project='lazydocker'),
    'lazygit': GithubReleaseInstallSource(user='jesseduffield', project='lazygit'),
    'lazynpm': GithubReleaseInstallSource(user='jesseduffield', project='lazynpm'),
    'shellcheck': GithubReleaseInstallSource(user='koalaman', project='shellcheck'),
    'shfmt': GithubReleaseInstallSource(user='mvdan', project='sh', rename='shfmt'),
    'bat': GithubReleaseInstallSource(user='sharkdp', project='bat'),
    'fd': GithubReleaseInstallSource(user='sharkdp', project='fd'),
    'delta': GithubReleaseInstallSource(user='dandavison', project='delta'),
    'btop': GithubReleaseInstallSource(user='aristocratos', project='btop'),
    'deno': GithubReleaseInstallSource(user='denoland', project='deno'),
    'hadolint': GithubReleaseInstallSource(user='hadolint', project='hadolint'),
    'code-server': GithubReleaseInstallSource(user='coder', project='code-server', binary='code-server'),
    'geckodriver': GithubReleaseInstallSource(user='mozilla', project='geckodriver'),
    'termscp': GithubReleaseInstallSource(user='veeso', project='termscp'),
    'gh': GithubReleaseInstallSource(user='cli', project='cli', binary='gh'),

    # GitProjectInstallSource
    'pyenv': GitProjectInstallSource(git_url='https://github.com/pyenv/pyenv', path='libexec/pyenv'),
    'nodenv': GitProjectInstallSource(git_url='https://github.com/nodenv/nodenv', path='libexec/nodenv'),

    # UrlInstallSource
    'repo': UrlInstallSource(url='https://storage.googleapis.com/git-repo-downloads/repo'),

    # ZipTarInstallSource
    'adb': ZipTarInstallSource(package_url=f'https://dl.google.com/android/repository/platform-tools-latest-{platform.system().lower()}.zip', executable_name='adb', package_name='platform-tools'),
    'fastboot': ZipTarInstallSource(package_url=f'https://dl.google.com/android/repository/platform-tools-latest-{platform.system().lower()}.zip', executable_name='fastboot', package_name='platform-tools'),

    # ShivInstallSource
    'pipx': ShivInstallSource(package='pipx'),

    # PipxInstallSource
    'autopep8': PipxInstallSource(package='autopep8'),
    'babi': PipxInstallSource(package='babi'),
    'bpython': PipxInstallSource(package='bpython'),
    'clang-format': PipxInstallSource(package='clang-format'),
    'clang-tidy': PipxInstallSource(package='clang-tidy'),
    'gcovr': PipxInstallSource(package='gcovr'),
    'jupyter-lab': PipxInstallSource(package='jupyterlab', command='jupyter-lab'),
    'jupyter-notebook': PipxInstallSource(package='notebook', command='jupyter-notebook'),
    'mypy': PipxInstallSource(package='mypy'),
    'pre-commit': PipxInstallSource(package='pre-commit'),
    'ptpython': PipxInstallSource(package='ptpython'),
    'run': PipxInstallSource(package='runtool', command='run'),
    'run-which': PipxInstallSource(package='runtool', command='run-which'),
    'tox': PipxInstallSource(package='tox'),
    'tuna': PipxInstallSource(package='tuna'),
    'virtualenv': PipxInstallSource(package='virtualenv'),
    'ranger': PipxInstallSource(package='ranger-fm', command='ranger'),
    'rifle': PipxInstallSource(package='ranger-fm', command='rifle'),
    'http': PipxInstallSource(package='httpie', command='http'),
    'https': PipxInstallSource(package='httpie', command='https'),
    'youtube-dl': PipxInstallSource(package='youtube-dl', command='youtube-dl'),
    'virtualenvwrapper': PipxInstallSource(package='virtualenvwrapper', command='virtualenvwrapper'),
    'typer': PipxInstallSource(package='typer-cli', command='typer'),
    'vd': PipxInstallSource(package='visidata', command='vd'),
    'log-tool': PipxInstallSource(package='git+https://github.com/FlavioAmurrioCS/log-tool.git', command='log-tool'),
    'twine': PipxInstallSource(package='twine', command='twine'),

    # GroupUrlInstallSource
    'heroku': GroupUrlInstallSource(links=__heroku_download_links___, binary='heroku', package_name='heroku'),
    'rclone': GroupUrlInstallSource(links=__rclone_download_links___, binary='rclone', package_name='rclone'),
    'node': GroupUrlInstallSource(links=__node_download_links___, binary='node', package_name='nodejs'),
    'npm': GroupUrlInstallSource(links=__node_download_links___, binary='npm', package_name='nodejs'),
    'npx': GroupUrlInstallSource(links=__node_download_links___, binary='npx', package_name='nodejs'),
}


class ToolInstaller(NamedTuple):
    bin_dir: str = os.environ.get('TOOL_INSTALLER_BIN_DIR', os.path.join(os.path.expanduser('~'), '.local', 'bin'))
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
        glob1 = glob.iglob(os.path.join(directory, '**', executable_name), recursive=True)
        glob2 = glob.iglob(os.path.join(directory, '**', f'{executable_name}*'), recursive=True)
        return next((x for x in itertools.chain(glob1, glob2) if (os.path.isfile(x)) and not os.path.islink(x)), None)

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
        """
        url must point to executable file.
        """
        rename = rename or os.path.basename(url)
        executable_path = os.path.join(self.bin_dir, rename)
        if not os.path.exists(executable_path):
            os.makedirs(self.bin_dir, exist_ok=True)
            with self.__download__(url) as download_file:
                shutil.move(download_file, executable_path)
        return self.__make_executable__(executable_path)

    def executable_from_package(
        self,
        package_url: str,
        executable_name: str,
        package_name: str | None = None,
        rename: str | None = None,
    ) -> str:
        """
        Get the executable from a online package.
        package_url         points to zip/tar file.
        executable_name     file to looked for in package.
        package_name        what should the package be rename to.
        rename              The name of the file place in bin directory
        """
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
        """
        Download file from github repo.

        user        github username.
        project     github project name.
        path        relative path of the file in github repo.
        tag         branch/tag name.
        rename      what should the file be rename as.
        """
        path = path or project
        url = f'https://raw.githubusercontent.com/{user}/{project}/{tag}/{path}'
        return self.executable_from_url(url=url, rename=rename)

    def git_install_repo(self, git_url: str, path: str, tag: str = 'master', pull: bool = False) -> str:
        git_project_location = os.path.join(self.git_project_dir, '_'.join(git_url.split('/')[-1:]))
        git_bin = os.path.join(git_project_location, path)
        if not os.path.exists(git_bin):
            subprocess.run(('git', 'clone', '-b', tag, git_url, git_project_location), check=True)
        elif pull:
            subprocess.run(('git', '-C', git_project_location, 'pull'))
        return self.__make_executable__(git_bin)

    def __best_url__(self, links: Sequence[str], uname_result: platform.uname_result = platform.uname()) -> str | None:
        """
        Will look at the urls and based on the information it has will try to pick the best one.

        links   links to consider.
        """
        if not links:
            return None
        if len(links) == 1:
            return links[0]

        links = self.filter_out_invalid(links)
        links = self.filter_system(links, uname_result.system)
        links = self.filter_machine(links, uname_result.machine)

        return sorted(links, key=len)[-1]

    def filter_system(self, links: list[str], system: str) -> list[str]:
        """
        links
        system  darwin,linux,windows
        """
        system_patterns = {
            'darwin': 'darwin|apple|macos|osx',
            'linux': 'linux|\\.deb|\\.rpm',
            'windows': 'windows|\\.exe',
        }

        system = system.lower()
        if system not in system_patterns or not links or len(links) == 1:
            return links

        pat = re.compile(system_patterns[system])
        filtered_links = [x for x in links if pat.search(os.path.basename(x).lower())]
        return filtered_links or links

    def filter_machine(self, links: list[str], machine: str) -> list[str]:
        machine_patterns = {
            'x86_64': 'x86_64|amd64|x86',
            'arm64': 'arm64|arch64',
        }

        if not links or len(links) == 1:
            return links

        machine = machine.lower()
        pat = re.compile(machine_patterns.get(machine, machine))
        filtered_links = [x for x in links if pat.search(os.path.basename(x).lower())]
        return filtered_links or links

    def filter_out_invalid(self, links: Sequence[str]) -> list[str]:
        return [
            x
            for x in links
            if not re.search(
                '\\.txt|license|\\.md|\\.sha256|\\.sha256sum|checksums|\\.asc|\\.sig|src',
                os.path.basename(x).lower(),
            )
        ]

    def __github_get_release_links__(
        self,
        user: str,
        project: str,
        tag: str = 'latest',
    ) -> list[str]:

        url = f'https://github.com/{user}/{project}/releases/{"latest" if tag == "latest" else f"tag/{tag}"}'
        html = self.__get_html__(url)
        download_links = ['https://github.com' + link for link in re.findall(f'/{user}/{project}/releases/download/[^"]+', html)]
        if not download_links:
            logging.error('Github is now using lazy loading fragments :(')
            assets_urls = ['https://github.com' + link for link in re.findall(f'/{user}/{project}/releases/expanded_assets/[^"]+', html)]
            if assets_urls:
                html = self.__get_html__(assets_urls[0])
                download_links = ['https://github.com' + link for link in re.findall(f'/{user}/{project}/releases/download/[^"]+', html)]
            else:
                logging.error('Not assets urls')

        return download_links

    def install_best(self, links: Sequence[str], binary: str, rename: str | None = None, package_name: str | None = None) -> str:
        rename = rename or binary
        download_url = self.__best_url__(links)
        if not download_url:
            print(f'Could not choose appropiate download from {rename}', file=sys.stderr)
            raise SystemExit(1)
        basename = os.path.basename(download_url)
        if basename.endswith('.zip') or '.tar' in basename or basename.endswith('.tgz') or basename.endswith('.tbz'):
            return self.executable_from_package(
                package_url=download_url,
                executable_name=binary,
                package_name=package_name,
                rename=rename,
            )
        return self.executable_from_url(download_url, rename=rename)

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

        # Check to see if binary already exist in bin directory
        bin_install_path = os.path.join(self.bin_dir, rename)
        if os.path.exists(bin_install_path):
            return self.__make_executable__(bin_install_path)

        # Check if binary exist is downloaded package
        package_name = f'{user}_{project}'
        possible = self.__executable_from_dir__(os.path.join(self.package_dir, package_name), binary)
        if possible is not None:
            return self.__make_executable__(possible)

        # Get all download links from github release page
        download_links: list[str] = self.__github_get_release_links__(
            user=user,
            project=project,
            tag=tag,
        )
        return self.install_best(download_links, binary=binary, rename=rename, package_name=package_name)

    def get_executable(self, source: ToolInstallerInstallSource) -> str:
        if isinstance(source, GithubScriptInstallSource):
            return self.git_install_script(**source._asdict())

        if isinstance(source, GithubReleaseInstallSource):
            return self.git_install_release(**source._asdict())

        if isinstance(source, GitProjectInstallSource):
            return self.git_install_repo(**source._asdict())

        if isinstance(source, UrlInstallSource):
            return self.executable_from_url(**source._asdict())

        if isinstance(source, ZipTarInstallSource):
            return self.executable_from_package(**source._asdict())

        if isinstance(source, ShivInstallSource):
            return self.shiv_install(**source._asdict())

        if isinstance(source, PipxInstallSource):
            return self.pipx_install(**source._asdict())

        if isinstance(source, GroupUrlInstallSource):
            return self.install_best(**source._asdict())

        raise SystemExit(1)

    def shiv_install(self, package: str, command: str | None = None) -> str:
        command = command or package
        bin_path = os.path.join(self.bin_dir, command)
        if not os.path.exists(bin_path):
            shiv_executable = self.git_install_release(user='linkedin', project='shiv', tag='1.0.1')
            subprocess.run(
                (
                    newest_python(),
                    shiv_executable,
                    '-c', command,
                    '-o', bin_path,
                    package,
                ),
                check=True,
            )
        return self.__make_executable__(bin_path)

    def pipx_install(self, package: str, command: str | None = None) -> str:
        command = command or package
        bin_path = os.path.join(self.bin_dir, command)
        if not os.path.exists(bin_path):
            pipx_cmd = self.shiv_install('pipx')
            env = {
                **os.environ,
                'PIPX_DEFAULT_PYTHON': newest_python(),
                'PIPX_BIN_DIR': self.bin_dir,
                # 'PIPX_HOME': self.bin_dir,
            }
            subprocess.run((pipx_cmd, 'install', '--force', package), check=True, env=env)
        return bin_path


class __ToolInstallerArgs__(Protocol):
    @property
    def tool(self) -> str:
        ...

    @classmethod
    def __parser__(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('tool', choices=sorted(PRE_CONFIGURED_TOOLS.keys()))
        return parser

    @classmethod
    def parse_args(cls, argv: Sequence[str] | None = None) -> tuple[__ToolInstallerArgs__, list[str]]:
        return cls.__parser__().parse_known_args(argv)  # type:ignore


def __run_which__(argv: Sequence[str] | None = None, print_tool: bool = True) -> tuple[__ToolInstallerArgs__, list[str], str]:
    args, rest = __ToolInstallerArgs__.parse_args(argv)
    tool_installer = ToolInstaller()
    tool = tool_installer.get_executable(PRE_CONFIGURED_TOOLS[args.tool])
    if print_tool:
        print(tool)
        raise SystemExit(0)
    return args, rest, tool


def main(argv: Sequence[str] | None = None) -> int:
    args, rest, tool = __run_which__(argv, print_tool=False)
    cmd = (tool, *rest)
    os.execvp(cmd[0], cmd)

    # for k, v in PRE_CONFIGURED_TOOLS.items():
    #     if isinstance(v, GithubReleaseInstallSource):
    #         __run_which__(argv=(k,), print_tool=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
