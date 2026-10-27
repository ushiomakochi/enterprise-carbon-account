"""
企业预设外载包目录。

每个 .py 文件定义一个企业专属预设包（类似公式 hook），启动/加载时自动合并进企业预设。
删掉对应文件即可恢复通用版；通用系统只保留基本预设（企业名称等）。

包文件格式（示例见 _template.py）：
    PRESETS = [
        {"key": "xxx", "label": "...", "type": "text", "value": "...",
         "category": "...", "sort_order": 99},
        ...
    ]

说明：
- 文件名（不含 .py）即包名，_ 或 . 开头的文件不加载。
- 包内预设只读，不可在界面中编辑/删除（需改文件）。
- type 支持 text / richtext / image / code。
"""

from enterprise_paths import PRESET_PACKS_DIR
import os as _os
if _os.path.isdir(PRESET_PACKS_DIR):
    __path__.append(PRESET_PACKS_DIR)
