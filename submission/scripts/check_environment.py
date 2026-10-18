"""检查项目使用的 Python 版本和已安装依赖。"""

from __future__ import annotations

import importlib.util
import sys
from importlib.metadata import PackageNotFoundError, version


REQUIRED = {
    "numpy": "numpy",
    "Pillow": "PIL",
    "scipy": "scipy",
    "reedsolo": "reedsolo",
    "rfc8785": "rfc8785",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pypdfium2": "pypdfium2",
    "reportlab": "reportlab",
    "pypdf": "pypdf",
    "pytest": "pytest",
    "httpx": "httpx",
    "gmssl": "gmssl",
}


def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 11):
        print("结果：失败，项目要求 Python 3.11 或更高版本。")
        return 1

    missing: list[str] = []
    for distribution, module in REQUIRED.items():
        if importlib.util.find_spec(module) is None:
            print(f"缺少：{distribution}")
            missing.append(distribution)
            continue
        try:
            installed = version(distribution)
        except PackageNotFoundError:
            installed = "版本未知"
        print(f"已安装：{distribution} {installed}")

    if missing:
        print("结果：失败。请运行 python -m pip install -e \".[test]\"。")
        return 1
    print("结果：环境满足项目运行和测试要求。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
