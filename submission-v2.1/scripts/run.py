"""无需安装包即可从源码运行 CLI（跨平台）。"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gm_provenance.cli import main
raise SystemExit(main())
