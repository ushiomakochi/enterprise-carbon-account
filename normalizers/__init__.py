"""
normalizers/ — 数据前置归一化 hook 目录（与 hooks/ 公式目录平级）

每个企业 / 每个平台写一个转换文件，系统启动时自动发现注册，
机制与公式 hook（hooks/）完全一致：

    1. 复制 TEMPLATE.py 到当前企业包 normalizers/，重命名为平台标识（如 my_corp.py）
    2. 类继承 BaseDataNormalizer，normalizer_name 必须与文件名一致
    3. 实现所需的 convert_mqtt / convert_api 及请求解析方法
    4. 机器 normalizer_config 中写: {"hook": "my_corp"}
    5. 重启系统即生效，改转换逻辑只动这一个文件

为什么字段名要对上公式：转换输出的 key 会直接作为 raw_data 喂给
该项目的公式 hook（required_raw_fields），所以输出什么字段名
以项目公式要求的为准（如空压站公式要求 cumulative_flow / cumulative_power，
其他项目类型以自己的公式为准）。
"""

from enterprise_paths import NORMALIZERS_DIR
import os as _os
if _os.path.isdir(NORMALIZERS_DIR):
    __path__.append(NORMALIZERS_DIR)

from .base_normalizer import BaseDataNormalizer

__all__ = ["BaseDataNormalizer"]
