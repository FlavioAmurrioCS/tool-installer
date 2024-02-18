from __future__ import annotations

import inspect
import itertools
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field
from functools import lru_cache
from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union


if TYPE_CHECKING:
    from typing_extensions import Self
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


@dataclass(unsafe_hash=True)
class Node:
    """Node for tree."""

    name: str = field(hash=True)
    parent: Self | None = field(default=None, hash=False)
    children: list[Self] = field(default_factory=list, hash=False)

    @classmethod
    def build_tree(cls, parent_child_connections: list[tuple[str, str]]) -> list[Self]:
        """Build tree from connections."""
        nodes: dict[str, Self] = {}
        for parent, child in parent_child_connections:
            if parent not in nodes:
                nodes[parent] = cls(name=parent)
            if child not in nodes:
                nodes[child] = cls(name=child)
            nodes[parent].children.append(nodes[child])
            nodes[child].parent = nodes[parent]

        for node in nodes.values():
            node.children = sorted(node.children, key=lambda x: (cls.children_count(x), x.name))
        return sorted(
            (v for v in nodes.values() if v.parent is None),
            key=lambda x: (cls.children_count(x), x.name),
        )

    def connections(self) -> Generator[tuple[str, str], None, None]:
        """Get connections."""
        for child in self.children:
            yield (self.name, child.name)
            yield from child.connections()

    def nodes(self) -> Generator[Self, None, None]:
        """Get nodes."""
        yield self
        for child in self.children:
            yield from child.nodes()

    @classmethod
    @lru_cache(maxsize=None)
    def children_count(cls, node: Self) -> int:
        """Count children."""
        return sum(1 + cls.children_count(child) for child in node.children)

    def print_node(self, level: int = 0) -> None:
        """Print node."""
        print("  " * level + "- " + self.name)
        for child in self.children:
            child.print_node(level + 1)

    def print_tree(self, *, prefix: str = "") -> None:
        if not prefix:
            print(self.name)
        for i, child in enumerate(self.children):
            if i == len(self.children) - 1:
                print(f"{prefix}└── {child.name}")
                child.print_tree(prefix=prefix + "    ")
            else:
                print(f"{prefix}├── {child.name}")
                child.print_tree(prefix=f"{prefix}│   ")


class Hierarchy(CLIApp):
    """Show hierarchy."""

    COMMAND_NAME = "hierarchy"

    @classmethod
    def run(cls, argv: Sequence[str] | None = None) -> int:
        _ = cls.parse_args(argv)
        relations: set[tuple[str, str]] = set()
        import runtool as pkg

        for cls_name, clz in itertools.chain(
            *(inspect.getmembers(sys.modules[x], inspect.isclass) for x in [pkg.__name__, __name__])
        ):
            if not clz.__module__.startswith("runtool"):
                continue
            _, *rest = inspect.getmro(clz)
            for parent in rest[:1]:
                relations.add((parent.__name__, cls_name))
        tree_top_nodes = Node.build_tree(list(relations))
        Node(name="runtool", children=tree_top_nodes).print_tree()
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
        ret: dict[str, dict[str, str]] = {}
        for _venv, v in pipx_list["venvs"].items():  # noqa: PERF102
            # print(venv)
            main_package = v["metadata"]["main_package"]

            package = main_package["package_or_url"]
            if package in main_package["apps"]:
                ret[package] = PipxInstallSource(  # noqa: SLF001
                    package=package, command=package
                )._mdict()
            else:
                ret[main_package["apps"][0]] = PipxInstallSource(  # noqa: SLF001
                    package=package, command=main_package["apps"][0]
                )._mdict()

            # for app in main_package["apps"]:
            #     ret[app] = PipxInstallSource(
            #         package=main_package["package_or_url"], command=app
            #     )._mdict()
            #     break

        print(json.dumps(ret, indent=2, default=str))
        return 0
