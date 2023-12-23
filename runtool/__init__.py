#!/usr/bin/env bash
"""":
exec "${LATEST_PYTHON:-$(which python3.12 || which python3.11 || which python3.10 || which python3.9 || which python3.8 || which python3 || which python)}" "${0}" "${@}"
"""
from __future__ import annotations

import argparse
import configparser
import dataclasses
import glob
import itertools
import json
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
import zipfile
from collections import Counter
from contextlib import contextmanager
from contextlib import suppress
from dataclasses import asdict
from dataclasses import dataclass
from functools import lru_cache
from textwrap import dedent
from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import Literal
from typing import NamedTuple
from typing import overload
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union
from urllib.parse import urljoin
from urllib.parse import urlparse


if TYPE_CHECKING:
    from typing import Protocol  # python3.8+
    from typing_extensions import Self
    from typing_extensions import TypeAlias
    from _collections_abc import dict_keys
    JSON_TYPE: TypeAlias = Union[str, int, float, bool, None, List[Any], Dict[str, Any]]
else:
    Protocol = object

__VERSION__ = '1.0.2'


def gron(obj: JSON_TYPE) -> list[str]:
    def _gron_helper(obj: JSON_TYPE, path: str = 'json') -> Generator[tuple[str, str], None, None]:
        if isinstance(obj, dict):
            yield path, '{}'
            for key, value in obj.items():
                key = f'.{key}' if key.isalnum() else f'["{key}"]'
                yield from _gron_helper(value, f'{path}{key}')
        elif isinstance(obj, list):
            yield path, '[]'
            for i, value in enumerate(obj):
                yield from _gron_helper(value, f'{path}[{i}]')
        elif isinstance(obj, bool):
            yield path, 'true' if obj else 'false'
        elif obj is None:
            yield path, 'null'
        elif isinstance(obj, str):
            yield path, f'"{obj}"'
        else:
            yield path, str(obj)
    return sorted(
        f'{path} = {value};'
        for path, value in _gron_helper(obj)
    )


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'


def input_tty(prompt: str | None = None) -> str:
    with open('/dev/tty') as tty:
        if prompt:
            print(prompt, end='', file=sys.stderr)
        return tty.readline().strip()


def selection(options: list[str]) -> str | None:
    if len(options) == 1:
        return options[0]
    print(f'{bcolors.OKCYAN}{"#" * 100}\nPlease select one of the following options:\n{"#" * 100}{bcolors.RESET}', file=sys.stderr)
    try:
        return options[int(input_tty('\n'.join(f'{i}: {x}' for i, x in enumerate(options)) + '\nEnter Choice: ') or 0)]
    except IndexError:
        return None


@lru_cache(maxsize=1)
def all_pythons() -> tuple[str, ...]:
    return tuple(
        x
        for x in (
            'python3',
            'python3.8',
            'python3.9',
            'python3.10',
            'python3.11',
            'python3.12',
            'python3.13',
        )
        if shutil.which(x)
    ) or (sys.executable,)


@lru_cache(maxsize=1)
def get_request(url: str) -> str:
    import urllib.request
    headers = {}
    if 'github' in url and 'GITHUB_TOKEN' in os.environ:
        headers['Authorization'] = f'token {os.environ["GITHUB_TOKEN"]}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as f:
        return f.read().decode('utf-8')


@contextmanager
def download_context(url: str) -> Generator[str, None, None]:
    import urllib.request
    logging.info(f'Downloading: {url}')
    derive_name = os.path.basename(url)
    with tempfile.TemporaryDirectory() as tempdir:
        download_path = os.path.join(tempdir, derive_name)
        headers = {}
        if 'github' in url and 'GITHUB_TOKEN' in os.environ:
            headers['Authorization'] = f'token {os.environ["GITHUB_TOKEN"]}'
        req = urllib.request.Request(url, headers=headers)
        with open(download_path, 'wb') as file:
            with urllib.request.urlopen(req) as f:
                file.write(f.read())
        yield download_path

# region core


@dataclass
class ToolInstallerConfig:
    OPT_DIR: str
    BIN_DIR: str
    PACKAGE_DIR: str
    GIT_PROJECT_DIR: str
    PIPX_HOME: str

    def __init__(self) -> None:
        self.OPT_DIR = os.path.expanduser(os.environ.get('TOOL_INSTALLER_OPT_DIR', '~/opt/runtool'))
        self.BIN_DIR = os.path.expanduser(os.environ.get('TOOL_INSTALLER_BIN_DIR', os.path.join(self.OPT_DIR, 'bin')))
        self.PACKAGE_DIR = os.path.expanduser(os.environ.get('TOOL_INSTALLER_PACKAGE_DIR', os.path.join(self.OPT_DIR, 'packages')))
        self.GIT_PROJECT_DIR = os.path.expanduser(os.environ.get('TOOL_INSTALLER_GIT_PROJECT_DIR', os.path.join(self.OPT_DIR, 'git_projects')))
        self.PIPX_HOME = os.path.expanduser(os.environ.get('TOOL_INSTALLER_PIPX_HOME', os.path.join(self.OPT_DIR, 'pipx_home')))


TOOL_INSTALLER_CONFIG = ToolInstallerConfig()


class ExecutableProvider(Protocol):
    def get_executable(self) -> str:
        ...

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        ...

    def _mdict(self) -> dict[str, Any]:
        ...


class _ToolInstallerBase(Protocol):
    @staticmethod
    def make_executable(filename: str) -> str:
        os.chmod(filename, os.stat(filename).st_mode | stat.S_IEXEC)
        return filename

    def get_executable(self) -> str:
        ...

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (self.get_executable(), *args),
            text=True,
            errors='ignore',
            encoding='utf-8',
            capture_output=True,
        )

    def _mdict(self) -> dict[str, Any]:
        class_name = self.__class__.__name__

        m_asdict: dict[str, str] = (
            asdict(self)  # type:ignore
            if dataclasses.is_dataclass(self)
            else self._asdict()  # type:ignore
        )

        with suppress(Exception):
            anno: dict[str, dataclasses.Field] = self.__class__.__dataclass_fields__  # type:ignore
            for k, v in anno.items():
                if m_asdict[k] == v.default:
                    del m_asdict[k]

        return {
            'class': class_name,
            **{
                key: value
                for key, value in m_asdict.items()
                if value is not None and not key.isupper()
            },
        }


class InternetInstaller(_ToolInstallerBase, Protocol):
    @staticmethod
    def uncompress(filename: str) -> zipfile.ZipFile | tarfile.TarFile:
        return zipfile.ZipFile(filename) if filename.endswith('.zip') else tarfile.open(filename)

    @staticmethod
    def find_executable(directory: str, executable_name: str) -> str | None:
        glob1 = glob.iglob(
            os.path.join(
                directory, '**', executable_name,
            ), recursive=True,
        )
        glob2 = glob.iglob(
            os.path.join(
                directory, '**', f'{executable_name}*',
            ), recursive=True,
        )
        return next((x for x in itertools.chain(glob1, glob2) if (os.path.isfile(x)) and not os.path.islink(x)), None)

    @classmethod
    def executable_from_url(cls, url: str, rename: str | None = None) -> str:
        """
        url must point to executable file.
        """
        rename = rename or os.path.basename(url)
        executable_path = os.path.join(TOOL_INSTALLER_CONFIG.BIN_DIR, rename)
        if not os.path.exists(executable_path):
            os.makedirs(TOOL_INSTALLER_CONFIG.BIN_DIR, exist_ok=True)
            with download_context(url) as download_file:
                shutil.move(download_file, executable_path)
        return cls.make_executable(executable_path)

    @classmethod
    def executable_from_package(
        cls,
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
        package_path = os.path.join(TOOL_INSTALLER_CONFIG.PACKAGE_DIR, package_name)
        if not os.path.exists(package_path) or cls.find_executable(package_path, executable_name) is None:
            with download_context(package_url) as tar_zip_file:
                with tempfile.TemporaryDirectory() as tempdir:
                    temp_extract_path = os.path.join(tempdir, 'temp_package')
                    with cls.uncompress(tar_zip_file) as untar_unzip_file:
                        untar_unzip_file.extractall(temp_extract_path)
                    os.makedirs(TOOL_INSTALLER_CONFIG.PACKAGE_DIR, exist_ok=True)
                    shutil.move(temp_extract_path, package_path)

        result = cls.find_executable(package_path, executable_name)
        if not result:
            logging.error(f'{executable_name} not found in {package_path}')
            raise SystemExit(1)

        executable = cls.make_executable(result)
        rename = rename or executable_name
        os.makedirs(TOOL_INSTALLER_CONFIG.BIN_DIR, exist_ok=True)
        symlink_path = os.path.join(TOOL_INSTALLER_CONFIG.BIN_DIR, rename)
        if os.path.isfile(symlink_path):
            if not os.path.islink(symlink_path):
                logging.info(
                    f'File is already in {TOOL_INSTALLER_CONFIG.BIN_DIR} with name {os.path.basename(executable)}',
                )
                return executable
            elif os.path.realpath(symlink_path) == os.path.realpath(executable):
                return symlink_path
            else:
                os.remove(symlink_path)

        os.symlink(executable, symlink_path, target_is_directory=False)
        return symlink_path


@dataclass
class UrlInstallSource(InternetInstaller):
    url: str
    rename: str | None = None

    def get_executable(self) -> str:
        return self.executable_from_url(url=self.url, rename=self.rename)


class BestLinkService(NamedTuple):
    uname: platform.uname_result = platform.uname()

    def pick(self, links: Sequence[str]) -> str | None:
        links = self.filter(links)
        return selection(links) or sorted(links, key=len)[-1]

    def filter(self, links: Sequence[str]) -> list[str]:
        """
        Will look at the urls and based on the information it has will try to pick the best one.

        links   links to consider.
        """
        if not links:
            return []
        if len(links) == 1:
            return [links[0]]

        links = self.filter_out_invalid(links)
        links = self.filter_system(links, self.uname.system)
        links = [x for x in links if not x.endswith('.rpm')] or links
        links = [x for x in links if not x.endswith('.deb')] or links
        links = self.filter_machine(links, self.uname.machine)
        links = [x for x in links if 'musl' in x.lower()] or links
        links = [x for x in links if 'armv7' not in x.lower()] or links
        links = [x for x in links if '32-bit' not in x.lower()] or links
        links = [x for x in links if '.pkg' not in x.lower()] or links
        links = [x for x in links if 'manifest' not in x.lower()] or links

        if len(links) == 2:
            a, b = sorted(links, key=len)
            suffix = b.lower().removeprefix(a.lower())
            if (a + suffix).lower() == b.lower():
                return [a]
            if len(a) == len(b) and a.replace('.tar.gz', '.tar.xz') == b.replace('.tar.gz', '.tar.xz'):
                return [a]

        return sorted(links, key=len)

    def filter_system(self, links: list[str], system: str) -> list[str]:
        """
        links
        system  darwin,linux,windows
        """
        system_patterns = {
            'darwin': 'darwin|apple|macos|osx',
            'linux': 'linux|\\.deb',
            'windows': 'windows|\\.exe',
        }

        system = system.lower()
        if system not in system_patterns or not links or len(links) == 1:
            return links

        pat = re.compile(system_patterns[system])
        filtered_links = [
            x for x in links if pat.search(
                os.path.basename(x).lower(),
            )
        ]
        return filtered_links or links

    def filter_machine(self, links: list[str], machine: str) -> list[str]:
        machine_patterns = {
            'x86_64': 'x86_64|amd64|x86',
            'arm64': 'arm64|arch64',
            'aarch64': 'aarch64|armv7l|armv7|arm64',
        }

        if not links or len(links) == 1:
            return links

        machine = machine.lower()
        pat = re.compile(machine_patterns.get(machine, machine))
        filtered_links = [
            x for x in links if pat.search(
                os.path.basename(x).lower(),
            )
        ]

        return filtered_links or links

    def filter_out_invalid(self, links: Sequence[str]) -> list[str]:
        return [
            x
            for x in links
            if not re.search(
                '\\.txt|license|\\.md|\\.sha256|\\.sha256sum|checksums|\\.asc|\\.sig|src|\\.sbom',
                os.path.basename(x).lower(),
            )
        ]


_BEST_LINK_SERVICE = BestLinkService()


class LinkInstaller(InternetInstaller, Protocol):
    binary: str
    rename: str | None = None
    package_name: str | None = None

    def links(self) -> list[str]:
        ...

    def get_executable(self) -> str:
        executable_path = os.path.join(
            TOOL_INSTALLER_CONFIG.BIN_DIR, self.rename or self.binary,
        )
        if os.path.exists(executable_path):
            return executable_path

        return self.install_best(
            links=self.links(),
            binary=self.binary,
            rename=self.rename,
            package_name=self.package_name,
        )

    def install_best(self, links: Sequence[str], binary: str, rename: str | None = None, package_name: str | None = None) -> str:
        rename = rename or binary
        download_url = _BEST_LINK_SERVICE.pick(links)
        if not download_url:
            logging.error(
                f'Could not choose appropiate download from {rename}',
            )
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


@dataclass
class _GitHubSource:
    hostname: str
    is_public_github: bool
    api_url: str
    owner: str
    repo: str
    tag: str
    project_url: str

    def __init__(self, url: str) -> None:
        urlparse_result = urlparse(url)
        self.hostname = urlparse_result.hostname or urlparse_result.netloc
        self.is_public_github = self.hostname in ('github.com', 'www.github.com')
        self.api_url = 'https://api.github.com' if self.is_public_github else f'https://{self.hostname}/api/v3'
        _, self.owner, self.repo, *rest, = urlparse_result.path.split('/', maxsplit=3)
        self.repo = self.repo.split('.git', maxsplit=1)[0]
        self.project_url = f'https://{self.hostname}/{self.owner}/{self.repo}'
        self.tag = 'latest'
        if rest and rest[0].startswith('releases/tag/'):
            _, _, self.tag, *_ = rest[0].split('/')
        self.tag = self.tag or 'latest'

    @classmethod
    def _from_owner_repo(cls, owner: str, repo: str) -> _GitHubSource:
        return cls(f'https://github.com/{owner}/{repo}')

    def _links_from_html(self) -> list[str]:
        url = f'{self.project_url}/releases/{"latest" if self.tag == "latest" else f"tag/{self.tag}"}'
        html = get_request(url)
        download_links: list[str] = []
        if not download_links:
            assets_urls = [
                self.project_url + '/' + link.split('/', maxsplit=3)[3]
                for link in re.findall(f'/{self.owner}/{self.repo}/releases/expanded_assets/[^"]+', html)
            ]
            if assets_urls:
                html = get_request(assets_urls[0])
                download_links = [
                    self.project_url + '/' + link.split('/', maxsplit=3)[3]
                    for link in re.findall(f'/{self.owner}/{self.repo}/releases/download/[^"]+', html)
                ]
            else:
                logging.error('Not assets urls')
        return download_links

    def _links_from_api(self) -> list[str]:
        try:
            data = json.loads(get_request(f'{self.api_url}/repos/{self.owner}/{self.repo}/releases'))
            return [x['browser_download_url'] for x in data[0]['assets']]
        except Exception:
            logging.error('Not able to get releases from github api')
            return []

    def links(self) -> list[str]:
        if self.is_public_github:
            return self._links_from_html()
        else:
            return self._links_from_api()

    # https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#get-a-repository
    def _repo_info(self) -> dict[str, Any]:
        return json.loads(get_request(f'{self.api_url}/repos/{self.owner}/{self.repo}'))

    def _description_from_api(self) -> str | None:
        description = self._repo_info().get('description')
        return description or None

    def _description_from_html(self) -> str | None:
        html = get_request(self.project_url)
        description = re.search(rf'<title>GitHub - {self.owner}/{self.repo}: (.*)</title>', html)
        return description.group(1) if description else None

    def description(self) -> str | None:
        if self.is_public_github:
            return self._description_from_html()
        else:
            return self._description_from_api()


@dataclass
class GithubReleaseLinks(LinkInstaller):
    github_source: _GitHubSource
    binary: str
    rename: str | None = None

    def __init__(
        self,
        url: str,
        binary: str | None = None,
        rename: str | None = None,
    ) -> None:
        self.github_source = _GitHubSource(url=url)
        self.binary = binary or self.github_source.repo
        self.rename = rename
        self.package_name = f'{self.github_source.owner}_{self.github_source.repo}'

    def links(self) -> list[str]:
        return self.github_source.links()


@dataclass
class ShivInstallSource(_ToolInstallerBase):
    SHIV_EXECUTABLE_PROVIDER = UrlInstallSource(url='https://github.com/linkedin/shiv/releases/download/1.0.4/shiv', rename=',shiv')
    package: str
    command: str | None = None

    def get_executable(self) -> str:
        command = self.command or self.package
        bin_path = os.path.join(TOOL_INSTALLER_CONFIG.BIN_DIR, command)
        if not os.path.exists(bin_path):
            shiv_executable = self.SHIV_EXECUTABLE_PROVIDER.get_executable()
            subprocess.run(
                (
                    all_pythons()[0],
                    shiv_executable,
                    '-c', command,
                    '-o', bin_path,
                    self.package,
                ),
                check=True,
            )
        return self.make_executable(bin_path)


# VIRTUALENV_EXECUTABLE_PROVIDER = UrlInstallSource(url='https://bootstrap.pypa.io/virtualenv.pyz', rename=',virtualenv')
# PIP_EXECUTABLE_PROVIDER = UrlInstallSource(url='https://bootstrap.pypa.io/pip/pip.pyz', rename=',pip')
FZF_EXECUTABLE_PROVIDER = GithubReleaseLinks(url='https://github.com/junegunn/fzf', rename=',fzf')
# GUM_EXECUTABLE_PROVIDER = GithubReleaseLinks(url='https://github.com/charmbracelet/gum', rename=',gum')
# YQ_EXECUTABLE_PROVIDER = GithubReleaseLinks(url='https://github.com/mikefarah/yq', rename=',yq')
# GRON_EXECUTABLE_PROVIDER = GithubReleaseLinks(url='https://github.com/tomnomnom/gron', rename=',gron')
# HTMLQ_EXECUTABLE_PROVIDER = GithubReleaseLinks(url='https://github.com/mgdm/htmlq', rename=',htmlq')

# endregion core


@dataclass
class GitProjectInstallSource(_ToolInstallerBase):
    git_url: str
    path: str
    tag: str = 'master'
    pull: bool = False

    def get_executable(self) -> str:
        git_project_location = os.path.join(
            TOOL_INSTALLER_CONFIG.GIT_PROJECT_DIR, '_'.join(self.git_url.split('/')[-1:]),
        )
        git_bin = os.path.join(git_project_location, self.path)
        if not os.path.exists(git_bin):
            subprocess.run(
                (
                    'git', 'clone', '-b', self.tag,
                    self.git_url, git_project_location,
                ), check=True,
            )
        elif self.pull:
            subprocess.run(('git', '-C', git_project_location, 'pull'))
        return self.make_executable(git_bin)


@dataclass
class ZipTarInstallSource(InternetInstaller):
    package_url: str
    executable_name: str
    package_name: str | None = None
    rename: str | None = None

    def get_executable(self) -> str:
        return self.executable_from_package(
            package_url=self.package_url,
            executable_name=self.executable_name,
            package_name=self.package_name,
            rename=self.rename,
        )


def pipecmd(cmd: Sequence[str], input: str) -> str:
    return subprocess.run(
        cmd,
        input=input,
        check=True,
        stdout=subprocess.PIPE,
        encoding='utf-8',
    ).stdout.strip()


@dataclass
class GronInstaller(LinkInstaller):
    url: str
    gron_pattern: str
    binary: str
    package_name: str
    rename: str | None = None

    def links(self) -> list[str]:
        response = get_request(self.url)
        pattern = re.compile(self.gron_pattern)
        gron_lines: list[str] = []
        gron_lines.extend(gron(json.loads(response)))

        ret = []

        base_url_path = urlparse(self.url)._replace(params=None, query=None, fragment=None).geturl()  # type:ignore

        for _, value in (line.rstrip(';').split(' = ', maxsplit=1) for line in gron_lines if pattern.search(line)):
            value = value[1:-1]
            if value.startswith('http'):
                ret.append(value)
            else:
                ret.append(urljoin(base_url_path, value))

        return ret


@dataclass
class LinkScraperInstaller(LinkInstaller):
    url: str
    binary: str
    package_name: str
    rename: str | None = None
    base_url: str | None = None
    link_contains: str | None = None

    def links(self) -> list[str]:
        response = get_request(self.url)
        href_pattern = re.compile(r'href="([^"]+)"')
        base_url = self.base_url or self.url

        ret = []
        for tag in re.findall(r'<a [^>]*>', response.replace('\n', ' ')):
            href_match = href_pattern.search(tag)
            if href_match:
                url = href_match.group(1)
                if self.link_contains and self.link_contains not in url:
                    continue
                if url.startswith('http'):
                    ret.append(url)
                else:
                    ret.append(urljoin(base_url, url))
        return ret


# @dataclass
# class PipxInstallSource2(_ToolInstallerBase):
#     package: str
#     command: str | None = None

#     def get_executable(self) -> str:
#         command = self.command or self.package
#         bin_path = os.path.join(TOOL_INSTALLER_CONFIG.BIN_DIR, command)
#         if not os.path.exists(bin_path):
#             pipx_cmd = PIPX_EXECUTABLE_PROVIDER.get_executable()
#             env = {
#                 **os.environ,
#                 'PIPX_DEFAULT_PYTHON': latest_python(),
#                 'PIPX_BIN_DIR': TOOL_INSTALLER_CONFIG.BIN_DIR,
#                 'PIPX_HOME': TOOL_INSTALLER_CONFIG.PIPX_HOME,
#             }
#             subprocess.run(
#                 (
#                     pipx_cmd, 'install', '--force',
#                     self.package,
#                 ), check=True, env=env,
#             )
#         return bin_path


@dataclass
class PipxInstallSource(_ToolInstallerBase):
    PIPX_EXECUTABLE_PROVIDER = ShivInstallSource(package='pipx')
    package: str
    command: str | None = None

    def get_executable(self) -> str:
        command = self.command or self.package
        bin_path = os.path.join(TOOL_INSTALLER_CONFIG.BIN_DIR, command)
        if not os.path.exists(bin_path):
            pipx_cmd = self.PIPX_EXECUTABLE_PROVIDER.get_executable()
            env = {
                **os.environ,
                'PIPX_DEFAULT_PYTHON': all_pythons()[0],
                'PIPX_BIN_DIR': TOOL_INSTALLER_CONFIG.BIN_DIR,
                'PIPX_HOME': TOOL_INSTALLER_CONFIG.PIPX_HOME,
            }
            subprocess.run(
                (
                    pipx_cmd, 'install', '--force',
                    self.package,
                ), check=True, env=env,
            )
        return bin_path

# @dataclass
# class ScriptInstaller(InternetInstaller):
#     """
#     Download setup script
#     Source script
#     Add Environment variables
#     Command could be executable or bash function

#     """
#     scritp_url: str
#     command: str

#     def get_executable(self) -> str:
#         with download_context(self.scritp_url) as path:
#             self.make_executable(path)
#             subprocess.run([path, '--help'])

#         # return super().get_executable()


@dataclass
class GroupUrlInstallSource(LinkInstaller):
    _links: list[str]
    binary: str
    rename: str | None = None
    package_name: str | None = None

    def links(self) -> list[str]:
        return self._links


# 'rustup': ScriptInstaller(scritp_url='https://sh.rustup.rs', command='rustup'),
# 'sdk': ScriptInstaller(scritp_url='https://get.sdkman.io', source_script='$HOME/.sdkman/bin/sdkman-init.sh', command='sdk'),


class _RunToolConfig:
    __INSTANCE__: _RunToolConfig | None = None
    _config: configparser.ConfigParser | None = None

    @property
    def config(self) -> configparser.ConfigParser:
        if self._config is None:
            self._config = configparser.ConfigParser()
            self._config.read(x for x in self.config_files() if os.path.exists(x))
        return self._config

    @classmethod
    @lru_cache(maxsize=1)
    def config_files(cls) -> list[str]:
        CONFIG_FILENAME = 'runtool.ini'
        foo = [
            os.path.realpath(CONFIG_FILENAME),
            os.path.expanduser(f'~/.config/runtool/{CONFIG_FILENAME}'),
            os.path.dirname(__file__) + f'/{CONFIG_FILENAME}',
        ]
        if 'RUNTOOL_CONFIG' in os.environ:
            path = os.path.expanduser(os.environ['RUNTOOL_CONFIG'])
            if os.path.exists(path):
                foo.insert(0, path)

        with suppress(Exception):
            import warnings
            warnings.simplefilter('ignore')
            from importlib.resources import path as importlib_path
            with importlib_path(__package__, CONFIG_FILENAME) as ipath:
                foo.append(ipath.as_posix())
        return list({x: None for x in foo}.keys())

    @lru_cache(maxsize=1)
    def tools_descriptions(self) -> dict[str, str]:
        return {k: v.get('description', '') for k, v in sorted(self.config.items()) if k != 'DEFAULT'}

    @lru_cache(maxsize=1)
    def tools(self) -> dict_keys[str, None]:
        return {x: None for x in sorted(self.config.sections())}.keys()

    @lru_cache()
    def get_executable_provider(self, command: str) -> ExecutableProvider:
        obj = dict(self.config[command])
        class_name = obj.pop('class')
        obj.pop('description', None)
        return getattr(sys.modules[__name__], class_name)(**obj)

    def run(self, command: str, *args: str) -> subprocess.CompletedProcess[str]:
        return self.get_executable_provider(command).run(*args)

    def save(self) -> None:
        with open('/tmp/runtool.ini', 'w') as f:
            self.config.write(f)

    def __getitem__(self, key: str) -> ExecutableProvider:
        return self.get_executable_provider(key)

    def __contains__(self, key: str) -> bool:
        return key in self.config

    @classmethod
    def get_instance(cls) -> _RunToolConfig:
        if not cls.__INSTANCE__:
            cls.__INSTANCE__ = cls()
        return cls.__INSTANCE__


RUNTOOL_CONFIG = _RunToolConfig.get_instance()

# region: cli


class CLIApp(Protocol):
    COMMAND_NAME: str
    ADD_HELP: bool = True

    @classmethod
    def _short_description(cls) -> str:
        return (cls.__doc__ or cls.__name__).splitlines()[0]

    @classmethod
    def parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description=cls._short_description(), add_help=cls.ADD_HELP)
        with suppress(Exception):
            if sys.argv[1] == cls.COMMAND_NAME:
                parser.prog = f'{parser.prog} {cls.COMMAND_NAME}'
        for field, ztype in cls.__annotations__.items():
            if field in ('COMMAND_NAME',):
                continue
            ztype = str(ztype)
            kwargs = {}

            field_arg = field.replace('_', '-')
            if ztype.startswith('list['):
                kwargs['nargs'] = '+'
            if hasattr(cls, field):
                kwargs['default'] = getattr(cls, field)
                field_arg = f'--{field.replace("_", "-")}'
            if 'None' in ztype:
                field_arg = f'--{field.replace("_", "-")}'
            if 'Literal' in ztype:
                kwargs['choices'] = eval(ztype.split('Literal')[1].split('[')[1].split(']')[0])
            parser.add_argument(field_arg, **kwargs)  # type:ignore
        return parser

    @overload
    @classmethod
    def parse_args(cls, argv: Sequence[str] | None) -> Self:
        ...

    @overload
    @classmethod
    def parse_args(cls, argv: Sequence[str] | None, *, allow_unknown_args: Literal[False]) -> Self:
        ...

    @overload
    @classmethod
    def parse_args(cls, argv: Sequence[str] | None, *, allow_unknown_args: Literal[True]) -> tuple[Self, list[str]]:
        ...

    @classmethod
    def parse_args(cls, argv: Sequence[str] | None = None, *, allow_unknown_args: bool = False) -> tuple[Self, list[str]] | Self:
        return cls.parser().parse_known_args(argv) if allow_unknown_args else cls.parser().parse_args(argv)  # type:ignore

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        ...


class CLIRun(CLIApp):
    """Run tool."""
    COMMAND_NAME = 'run'
    ADD_HELP = False
    tool: str

    @classmethod
    def parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description=cls._short_description(), add_help=cls.ADD_HELP)
        with suppress(Exception):
            if sys.argv[1] == cls.COMMAND_NAME:
                parser.prog = f'{parser.prog} {cls.COMMAND_NAME}'
        parser.add_argument('tool', choices=RUNTOOL_CONFIG.tools())
        return parser

    @classmethod
    def check_help(cls, argv: Sequence[str] | None = None) -> None:
        help_call = False
        if argv is None and sys.argv[1] in ('--help', '-h'):
            help_call = True
        elif argv is not None and argv[0] in ('--help', '-h'):
            help_call = True

        if help_call:
            help_text = dedent(f"""\
                {cls.parser().prog} <tool> [args...]

                {cls._short_description()}

                Available tools:
                """) + '\n'.join(f'  {tool:30} {description[:100]}' for tool, description in RUNTOOL_CONFIG.tools_descriptions().items())

            help_text += dedent(f"""\


                Environment variables:
                    TOOL_INSTALLER_OPT_DIR:         {TOOL_INSTALLER_CONFIG.OPT_DIR}
                    TOOL_INSTALLER_BIN_DIR:         {TOOL_INSTALLER_CONFIG.BIN_DIR}
                    TOOL_INSTALLER_PIPX_HOME:       {TOOL_INSTALLER_CONFIG.PIPX_HOME}
                    TOOL_INSTALLER_PACKAGE_DIR:     {TOOL_INSTALLER_CONFIG.PACKAGE_DIR}
                    TOOL_INSTALLER_GIT_PROJECT_DIR: {TOOL_INSTALLER_CONFIG.GIT_PROJECT_DIR}
                    RUNTOOL_CONFIG:                 {os.environ.get('RUNTOOL_CONFIG', '')}
                    """)

            help_text += '\n\nConfig files:\n' + '\n'.join(f'  {x}' for x in RUNTOOL_CONFIG.config_files()) + '\n'

            print(help_text)
            raise SystemExit(0)

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        cls.check_help(argv)
        args, rest = cls.parse_args(argv, allow_unknown_args=True)
        tool = RUNTOOL_CONFIG[args.tool].get_executable()
        cmd = (tool, *rest)
        os.execvp(cmd[0], cmd)


class CLIWhich(CLIRun, CLIApp):
    """Show executable file path."""
    COMMAND_NAME = 'which'
    tool: str

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        cls.check_help(argv)
        args = cls.parse_args(argv)
        print(RUNTOOL_CONFIG[args.tool].get_executable())
        return 0


class CLIMultiInstaller(CLIApp):
    """Multi installer."""
    COMMAND_NAME = 'multi-installer'

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        _FZF_EXECUTABLE = shutil.which('fzf') or shutil.which(',fzf') or FZF_EXECUTABLE_PROVIDER.get_executable()

        result = subprocess.run(
            (_FZF_EXECUTABLE or 'fzf', '--multi'),
            input='\n'.join(f'{tool:30} {description}' for tool, description in RUNTOOL_CONFIG.tools_descriptions().items()),
            text=True,
            stdout=subprocess.PIPE,
        )
        for tool in (line.split(maxsplit=1)[0] for line in result.stdout.splitlines()):
            print('#' * 100)
            print(f' {tool} '.center(100))
            print('#' * 100)
            print(RUNTOOL_CONFIG[tool].get_executable())
        return 0


class CLIFilterLinks(CLIApp):
    'Filter links by system.'
    COMMAND_NAME = 'filter-links'
    selector: Literal['filter', 'pick'] = 'pick'

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        stdin_lines = []
        if not sys.stdin.isatty():
            stdin_lines = [x.strip() for x in sys.stdin]

        args, rest = cls.parse_args(argv, allow_unknown_args=True)
        options = [*stdin_lines, *rest]
        if not options:
            return 1
        if len(options) == 1:
            print(options[0])
            return 0
        service = BestLinkService()
        if args.selector == 'pick':
            result = service.pick(options)
            if not result:
                return 1
            print(result)
        elif args.selector == 'filter':
            results = service.filter(options)
            if not results:
                return 1
            for line in results:
                print(line)
        return 0


class CLILinkInstaller(CLIApp):
    'Install from links.'
    COMMAND_NAME = 'link-installer'
    links: list[str]
    binary: str | None = None
    rename: str | None = None
    package_name: str | None = None

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        args = cls.parse_args(argv)

        binary = args.binary
        if not binary:
            counter: Counter[str] = Counter()
            for link in args.links:
                for token in os.path.basename(link).split('-'):
                    counter[token] += 1
            binary = counter.most_common(1)[0][0]

        path = LinkInstaller.install_best(
            InternetInstaller,  # type:ignore
            links=args.links,
            binary=binary,
            rename=args.rename,
            package_name=args.package_name,
        )

        print(path)

        return 0


class GhLinks(CLIApp):
    'Show github release links.'
    COMMAND_NAME = 'gh-links'
    url: str

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        args = cls.parse_args(argv)
        gh = _GitHubSource(
            url=args.url,
        )
        for link in gh.links():
            print(link)

        return 0


class GhInstall(CLIApp):
    'Install from github release.'
    COMMAND_NAME = 'gh-install'
    url: str
    binary: str | None = None
    rename: str | None = None

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        args = cls.parse_args(argv)
        gh = GithubReleaseLinks(
            url=args.url,
            binary=args.binary,
            rename=args.rename,
        )

        print(gh.get_executable())
        return 0


class CLIFormatIni(CLIApp):
    'Format ini file.'
    COMMAND_NAME = 'format-ini'
    file: list[str]
    output: str = '/dev/stdout'

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        args = cls.parse_args(argv)

        config = configparser.ConfigParser()
        config.read(args.file)

        order_config = configparser.ConfigParser()
        dct = {
            k: config[k]
            for k in sorted(config.sections(), key=lambda x: (config[x].get('class'), config[x].get('url', ''), config[x].get('package')))
        }
        for _, v in dct.items():
            if v.get('description', '').strip():
                continue
            clz = v['class']
            github = ''
            if clz == 'PipxInstallSource':
                package = v['package'].split('[')[0]
                if 'github' in package:
                    clz = 'GithubReleaseLinks'
                    github = package
                else:
                    try:
                        pypi_info = json.loads(get_request(f'https://www.pypi.org/pypi/{package}/json'))
                        description = pypi_info['info']['summary']
                        if description:
                            v['description'] = description
                            continue
                        else:
                            github = github or next((x for x in pypi_info['info']['project_urls'].values() if 'github' in x), '')
                    except Exception:
                        print(f'Could not get description for {package}', file=sys.stderr)
            if not github and clz in ('GithubScriptInstallSource', 'GithubReleaseLinks'):
                github = v.get('base_url', 'https://github.com') + '/' + v['user'] + '/' + v['project']
            github = github or next((x for x in v.values() if 'github' in x), '')
            if github:
                d = _GitHubSource(url=github).description()
                if d:
                    v['description'] = d
                    continue
            else:
                print(f'Could not get description for {v["class"]}', file=sys.stderr)

        order_config.read_dict(dct)
        with open(args.output, 'w') as f:
            order_config.write(f)

        return 0


class CommaFixer(CLIApp):
    'Fix commands in path.'
    COMMAND_NAME = '__comma-fixer'

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        path_dir = os.path.dirname(sys.argv[0])
        for file_name in os.listdir(path_dir):
            file_path = os.path.join(path_dir, file_name)
            if file_name.startswith('-') and os.access(file_path, os.X_OK) and not os.path.isdir(file_path):
                shutil.move(file_path, os.path.join(path_dir, ',' + file_name[1:]))
        print('Fixed!', file=sys.stderr)
        return 0


class ValidateConfig(CLIApp):
    'Validate config.'
    COMMAND_NAME = '__validate-config'

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        for tool in RUNTOOL_CONFIG.tools():
            executable_provider = RUNTOOL_CONFIG[tool]
            print(f'{executable_provider=}')
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    dct = {
        x.COMMAND_NAME: x
        for x in CLIApp.__subclasses__()
        # for x in sorted(CLIApp.__subclasses__(), key=lambda x: x.COMMAND_NAME)
    }

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('command', choices=dct.keys())
    help_text = dedent(f"""\
        {parser.prog} <command> [options] [args...]

        Available commands:
        """) + '\n'.join(f'  {k:20} {v._short_description()}' for k, v in dct.items())
    if sys.argv[1] in ('--help', '-h'):
        print(help_text)
        return 0
    args, rest = parser.parse_known_args(argv)
    raise SystemExit(dct[args.command].run(rest))


if __name__ == '__main__':
    raise SystemExit(main())

# endregion: cli
