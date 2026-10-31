"""打包入口。

PyInstaller 需要一个**顶层脚本**作为入口，而本项目的正式入口是
``python -m cedas``（包内 ``__main__``）。若直接把 ``cedas/__main__.py``
交给 PyInstaller，它会以「剧本脚本」而非「包内模块」的身份执行，
``from . import __version__`` 这类相对导入会立即失败。

因此这里做一层薄壳：由它导入包并转发到真正的 ``main()``。

禁止在此文件写任何业务逻辑——它是构建产物的一部分，逻辑应当留在 ``src/cedas``。
"""

from __future__ import annotations

import sys


def run() -> int:
    from cedas.__main__ import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
