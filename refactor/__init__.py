# refactor 包：爬虫重构的整体构建目录（平行对照，不修改旧代码）。

# 导入即加载统一配置并回填环境变量（单一配置源：refactor/config.yaml）。
# 用相对导入：无论把本目录当项目根还是当父工程的子包，都指向同一份 config.py。
from . import config as _config  # noqa: E402,F401
