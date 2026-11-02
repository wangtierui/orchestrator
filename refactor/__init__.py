# refactor 包：爬虫重构的整体构建目录（平行对照，不修改旧代码）。

# 导入即加载统一配置并回填环境变量（单一配置源：refactor/config.yaml）
try:
    import refactor.config  # noqa: E402,F401
except ImportError:  # 兼容从 refactor/ 目录直接运行（collectors 上下文）
    import config  # noqa: E402,F401
