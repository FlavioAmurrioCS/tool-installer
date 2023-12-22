# runtool

[![PyPI - Version](https://img.shields.io/pypi/v/runtool.svg)](https://pypi.org/project/runtool)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/runtool.svg)](https://pypi.org/project/runtool)

-----

**Table of Contents**

- [runtool](#runtool)
  - [Installation](#installation)
  - [License](#license)
- [Run Tool](#run-tool)

## Installation

```console
pip install runtool
```

## License

`runtool` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.


# Run Tool

This run tool will install and run a specific list of tools.

```bash
# Run one of the pre-configured tools
run gdu

# Show path the the tool
run-which gdu

# Install all
for tool in $(run 2>&1 | grep '{' | grep -oE '[0-9a-z\.-]+'); do run-which "${tool}"; done
```
