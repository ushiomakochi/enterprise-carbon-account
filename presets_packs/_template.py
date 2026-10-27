# 企业预设外载包模板（复制本文件改名，去掉开头下划线即生效）
#
# 一个企业专属包 = 企业专属预设（PRESETS）+ 企业级配置（ENTERPRISE_CONFIG）。
# 启动后这些会自动出现在「预设信息管理 - 企业预设」里（带「外载」标记，只读），
# 企业配置会覆盖「设置」页对应字段（只读）。不再需要时删除该 .py 文件即可恢复通用版。

PRESETS = [
    {
        "key": "enterprise_brand",       # key 唯一，供模板 {{ preset.enterprise_brand }} 引用
        "label": "企业品牌全称",
        "type": "text",
        "value": "某科技有限公司",
        "category": "企业信息",
        "sort_order": 30,
    },
    # code 类型示例：可根据其它预设推导
    # {"key": "xxx", "label": "...", "type": "code",
    #  "value": "return preset.enterprise_brand + '（总部）'",
    #  "category": "企业信息", "sort_order": 32},
]

# 企业级配置（可选）：覆盖「设置」页对应字段，只读、随包文件走。
# 支持字段：ef_grid_override（自定义电网排放因子 tCO₂/MWh）、report_logo_path（报告 Logo 路径）等。
ENTERPRISE_CONFIG = {
    # "ef_grid_override": 0.5777,
    # "report_logo_path": "data/preset_images/logo.png",
}
