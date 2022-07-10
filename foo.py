#!/usr/bin/env python3
from __future__ import annotations

import glob
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request
from functools import lru_cache
from typing import List
from typing import NamedTuple
from typing import Optional


def __get_html__(url: str) -> str:
    with urllib.request.urlopen(url) as f:
        html = f.read().decode('utf-8')
        return html


def __download_file__(url: str, filename: str):
    with open(filename, 'wb') as file:
        with urllib.request.urlopen(url) as f:
            file.write(f.read())
    return filename


def __make_excutable__(filename):
    st = os.stat(filename)
    os.chmod(filename, st.st_mode | stat.S_IEXEC)


class GithubProject(NamedTuple):
    user: str
    project_name: str
    tag: str = 'latest'
    install_dir: str = '/tmp/foo'
    binary_name: Optional[str] = None
    rename: Optional[str] = None
    # install_dir: str = os.path.join(os.path.expanduser('~'), '.local', 'bin')

    def get_release_tags(self) -> List[str]:
        releases_url = f'https://github.com/{self.user}/{self.project_name}/releases'
        html = __get_html__(releases_url)
        tags_links = re.findall(f'/{self.user}/{self.project_name}/releases/(latest|tag/[^"]+)', html)
        return sorted(set(x.split('/')[-1] for x in tags_links), reverse=True)

    def __get_all_assets__(self) -> List[str]:
        url = f'https://github.com/{self.user}/{self.project_name}/releases/{"latest" if self.tag == "latest" else f"tag/{self.tag}"}'
        html = __get_html__(url)
        links = re.findall(f'/{self.user}/{self.project_name}/releases/download/[^"]+', html)
        return links

    def get_download_target(self) -> Optional[str]:
        assets = self.__get_all_assets__()
        ignore_pattern = self.__system_ignore_pattern__()

        results = [
            x for x in sorted(assets, key=len, reverse=True)
            if ignore_pattern.search(x.lower().rsplit('/', maxsplit=1)[-1]) is None
        ]

        if not results:
            ret = None
        else:
            ret = 'https://github.com' + results[0]
        if len(results) > 2:
            print(f'Multiple matches for {self}', file=sys.stderr)
            raise SystemExit(1)

        return ret

    @staticmethod
    def from_https_url(https_url: str) -> GithubProject:
        user, project_name = https_url.strip().split('github.com/')[1].split('/')[:2]
        return GithubProject(user=user, project_name=project_name)

    @staticmethod
    @lru_cache(maxsize=1)
    def __system_ignore_pattern__() -> re.Pattern[str]:
        ignore_patterns: List[str] = [
            '.sha256',
            'checksums',
            'license',
            'sha256sums',
            '.src.',
        ]

        system = platform.system().lower()
        if system == 'darwin':
            ignore_patterns.extend(('windows', 'linux', 'openbsd', 'freebsd', 'netbsd', '.deb', '.rpm', '.exe'))
        elif system == 'linux':
            ignore_patterns.extend(('windows', 'darwin', 'openbsd', 'freebsd', 'netbsd', '.exe'))
        elif system == 'windows':
            ignore_patterns.extend(('linux', 'darwin', 'openbsd', 'freebsd', 'netbsd', '.deb', '.rpm'))

        machine = platform.machine().lower()

        if machine == 'x86_64':
            ignore_patterns.extend(('arm64', 'aarch64', '32-bit', 'powerpc'))

        return re.compile(f"({'|'.join(ignore_patterns)})")

    def install(self):
        print()
        print(f''.center(150, '='))
        print(f' {self} '.center(150, '='))
        print(f''.center(150, '='))
        download_url = self.get_download_target()
        if download_url is None:
            return
        basename: str = os.path.basename(download_url)
        with tempfile.TemporaryDirectory() as tempdir:
            downloaded_file = src_file = os.path.join(tempdir, basename)
            __download_file__(download_url, downloaded_file)
            if basename.endswith('.zip') or '.tar' in basename or basename.endswith('.tgz'):
                if basename.endswith('.zip'):
                    import zipfile
                    with zipfile.ZipFile(downloaded_file, 'r') as zip_ref:
                        zip_ref.extractall(tempdir)
                else:
                    import tarfile
                    with tarfile.open(downloaded_file) as file:
                        file.extractall(tempdir)
                os.remove(src_file)
                import glob
                files = [
                    x
                    for x in glob.glob(os.path.join(tempdir, '**', f'{self.bin_name}*'), recursive=True)
                    if os.path.isfile(x) and re.search('.(bash|zsh|fish|1)', os.path.basename(x)) is None
                ]
                src_file = files[0]
                # for file in files:
                #     print(file)
                # return

            os.makedirs(self.install_dir, exist_ok=True)
            dest_file = self.final_path
            os.replace(src_file, dest_file)
            __make_excutable__(dest_file)
            subprocess.run((dest_file, '-h'))
            print(dest_file)

    @property
    def bin_name(self):
        return self.binary_name or self.project_name

    @property
    def final_path(self):
        return os.path.join(self.install_dir, self.rename or self.binary_name or self.project_name)

# def install_from_url(url, )


def url_install(
    download_url: str,
    binary: Optional[str] = None,
    rename: Optional[str] = None,
    install_dir: Optional[str] = None,
):
    install_dir = install_dir or '/tmp/foor'
    binary = binary or os.path.basename(download_url)
    rename = rename or binary
    install_path = os.path.join(install_dir, rename)
    if os.path.exists(install_path):
        print(f'Already Downloaded: {install_path}', file=sys.stderr)
        return install_path

    basename: str = os.path.basename(download_url)
    with tempfile.TemporaryDirectory() as tempdir:
        downloaded_file = src_file = os.path.join(tempdir, basename)
        __download_file__(download_url, downloaded_file)
        if basename.endswith('.zip') or '.tar' in basename or basename.endswith('.tgz'):
            if basename.endswith('.zip'):
                import zipfile
                with zipfile.ZipFile(downloaded_file, 'r') as zip_ref:
                    zip_ref.extractall(tempdir)
            else:
                import tarfile
                with tarfile.open(downloaded_file) as file:
                    file.extractall(tempdir)
            os.remove(src_file)
            files = [
                x
                for x in glob.glob(os.path.join(tempdir, '**', f'{binary}*'), recursive=True)
                if os.path.isfile(x) and re.search('.(bash|zsh|fish|1)', os.path.basename(x)) is None
            ]
            src_file = files[0]
            # for file in files:
            #     print(file)
            # return

        __make_excutable__(src_file)
        r = subprocess.run((src_file, '--help'), capture_output=True)
        if r.returncode != 0 and not r.stdout.strip():
            print(f'INVALID: {src_file}', file=sys.stderr)
            return None
        os.makedirs(install_dir, exist_ok=True)
        os.replace(src_file, install_path)
        print(install_path)


def git_install_script(
    user: str,
    project: str,
    path: str,
    tag: str = 'master',
    rename: Optional[str] = None,
    install_dir: Optional[str] = None,
):
    rename = rename or os.path.basename(path)
    url = f'https://raw.githubusercontent.com/{user}/{project}/{tag}/{path}'
    return url_install(download_url=url, rename=rename, install_dir=install_dir)


def git_install_release(
    user: str,
    project: str,
    tag: str = 'latest',
    binary: Optional[str] = None,
    rename: Optional[str] = None,
    install_dir: Optional[str] = None,
):
    install_dir = install_dir or '/tmp/foor'
    binary = binary or project
    rename = rename or binary
    install_path = os.path.join(install_dir, rename)
    if os.path.exists(install_path):
        print(f'Already Downloaded: {install_path}', file=sys.stderr)
        return install_path
    url = f'https://github.com/{user}/{project}/releases/{"latest" if tag == "latest" else f"tag/{tag}"}'
    html = __get_html__(url)
    links: List[str] = re.findall(f'/{user}/{project}/releases/download/[^"]+', html)
    ignore_pattern = GithubProject.__system_ignore_pattern__()

    results = [
        x for x in sorted(links, key=len, reverse=True)
        if ignore_pattern.search(x.lower().rsplit('/', maxsplit=1)[-1]) is None
    ]
    if not results:
        return None
    download_url = 'https://github.com' + results[0]
    return url_install(
        download_url=download_url,
        binary=binary,
        rename=rename,
        install_dir=install_dir,
    )


def main() -> None:
    git_install_script(user='lemnos', project='theme.sh', path='bin/theme.sh')
    git_install_script(user='dylanaraps', project='neofetch', path='neofetch')
    git_install_release(user='linkedin', project='shiv')
    git_install_release(user='pre-commit', project='pre-commit')
    git_install_release(user='junegunn', project='fzf')
    git_install_release(user='BurntSushi', project='ripgrep', binary='rg')
    git_install_release(user='docker', project='compose', binary='docker-compose')
    git_install_release(user='dundee', project='gdu')
    # git_install_release(user='hadolint', project='hadolint', tag='v1.23.0')
    git_install_release(user='isacikgoz', project='tldr')
    git_install_release(user='jesseduffield', project='lazydocker')
    git_install_release(user='jesseduffield', project='lazygit')
    git_install_release(user='jesseduffield', project='lazynpm')
    git_install_release(user='koalaman', project='shellcheck')
    git_install_release(user='mvdan', project='sh', rename='shfmt')
    git_install_release(user='sharkdp', project='bat')
    git_install_release(user='sharkdp', project='fd')
    # git_install_release(user='llvm', project='llvm-project', binary='clang-format')
    git_install_release(user='dandavison', project='delta')


if __name__ == '__main__':
    main()
