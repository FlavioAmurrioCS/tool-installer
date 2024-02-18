from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union


if TYPE_CHECKING:
    from runtool._types import PipxList
    from typing_extensions import TypeAlias

    JSON_TYPE: TypeAlias = Union[str, int, float, bool, None, List[Any], Dict[str, Any]]

from runtool import RUNTOOL_CONFIG, CLIApp, PipxInstallSource


class CommaFixer(CLIApp):
    """Fix commands in path."""

    COMMAND_NAME = "__comma-fixer"

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        path_dir = os.path.dirname(sys.argv[0])
        for file_name in os.listdir(path_dir):
            file_path = os.path.join(path_dir, file_name)
            if (
                file_name.startswith("-")
                and os.access(file_path, os.X_OK)
                and not os.path.isdir(file_path)
            ):
                shutil.move(file_path, os.path.join(path_dir, "," + file_name[1:]))
        print("Fixed!", file=sys.stderr)
        return 0


class ValidateConfig(CLIApp):
    """Validate config."""

    COMMAND_NAME = "__validate-config"

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        for tool in RUNTOOL_CONFIG.tools():
            executable_provider = RUNTOOL_CONFIG[tool]
            print(f"{executable_provider=}")
        return 0


class Hierarchy(CLIApp):
    """Show hierarchy."""

    COMMAND_NAME = "hierarchy"

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        relations: list[tuple[str, str]] = []
        import runtool as pkg

        for cls_name, clz in inspect.getmembers(sys.modules[pkg.__name__], inspect.isclass):
            _, *rest = inspect.getmro(clz)
            for x in rest[:1]:
                if x.__name__ in (
                    "object",
                    "Protocol",
                    "tuple",
                    "dict",
                    "list",
                    "AbstractContextManager",
                ):
                    continue
                relations.append((cls_name, x.__name__))
        for a, b in sorted(relations, key=lambda x: x[1]):
            # print(f"{a} <|-- {b}")
            print(f"{b} --|> {a}")
        return 0


class PipxConfigCLI(CLIApp):
    """Pipx config CLI."""

    COMMAND_NAME = "pipx-migrate"

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:  # noqa: ARG003
        result = subprocess.run(
            (PipxInstallSource.PIPX_EXECUTABLE_PROVIDER.get_executable(), "list", "--json"),  # noqa: S603
            check=True,
            capture_output=True,
        )
        pipx_list: PipxList = json.loads(result.stdout)
        ret: dict[str, PipxInstallSource] = {}
        for venv, v in pipx_list["venvs"].items():
            print(venv)
            main_package = v["metadata"]["main_package"]

            for app in main_package["apps"]:
                ret[app] = PipxInstallSource(package=main_package["package_or_url"], command=app)
                break

        print(json.dumps(ret, indent=2, default=str))
        return 0
