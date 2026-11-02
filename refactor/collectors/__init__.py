# 爬虫重构主包：每个源一个子包（mof/gov/nfra/pbc），共享 base.py 契约。

# 导入即加载统一配置并回填环境变量（单一配置源：refactor/config.yaml）
try:
    import refactor.config  # noqa: E402,F401
except ImportError:  # 兼容从 refactor/ 目录直接运行（collectors 上下文）
    import config  # noqa: E402,F401
