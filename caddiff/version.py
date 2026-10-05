"""版本号——全项目唯一出处。

`pyproject.toml` 从这里读（`dynamic = ["version"]`），`caddiff --version` 也从这里读。
不要在任何其它文件里再写一遍版本号：两处写必然漂移，而版本号漂移会让「镜像 tag ↔
源码 tag ↔ 构建脚本一一对应」这条合规要求失效（见 AGENTS.md §6）。
"""

__version__ = "0.3.2"
