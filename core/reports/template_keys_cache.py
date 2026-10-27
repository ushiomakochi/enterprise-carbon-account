"""
模板字段识别缓存。

「这个 .docx 模板用了哪些占位符」需要把整个 docx 打开、遍历全部段落与表格单元格，
网络盘上单个大模板要 0.3~0.4 秒，而同一个模板在多个报告类型下会被重复解析
（文件名不含类型 key 的会被当成「通用模板」，三种类型各解析一遍）。
把结果按**文件 md5** 缓存到磁盘，模板没改就直接命中。

缓存文件：data/template_keys_cache.json —— 运行时缓存，删掉即可，会自动重建。

结构::

    {
      "version": 1,
      "entries": {
        "<md5>": {
          "name": "某企业/approval.docx",          # 最近一次解析时的相对路径，仅供排查
          "used_presets": ["contact_person"],     # 三命名空间并集，便于人肉查看
          "keys": {"preset": [...], "project_preset": [...], "other": [...]},
          "cached_at": "2026-09-17 16:00:00"
        }
      }
    }

**按 md5 做 key**（不是按路径）：同一个文件被多个报告类型引用时只解析一次；
模板改名/移动只要内容没变就不必重解析。

version 用于日后调整识别逻辑时整体失效——改了提取规则就把它 +1，
否则旧缓存会被一直复用、新规则永远不生效。
"""

import os
import json
import time
import hashlib
from datetime import datetime

# 识别逻辑变更时 +1，让所有旧缓存失效
_CACHE_VERSION = 1

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "template_keys_cache.json",
)

# 进程内缓存。三层，从快到慢：
#   _STAT  {rel_path: (size, mtime_ns, md5)}  —— stat 命中就完全不用读文件
#   _ENTRIES 整个缓存文件的内容（避免每请求读一次 JSON）
#   _MEMO  {md5: keys}
# 这台机器是网络盘：光是把 1.28MB 模板读一遍算 md5 就要 0.45s，
# 和重新解析同一个量级。所以热路径必须是「一次 stat」而不是「每次算 md5」。
_STAT = {}
_ENTRIES = None
_MEMO = {}

# 复查间隔（秒）。网络盘上连 os.stat 都是一次往返，三个模板就 0.25s。
# 这段时间内直接信任上次的记录、连 stat 都不做——模板改动极少，
# 高亮缓存晚几秒生效没有任何影响。
_STAT_TTL = 5.0


def _entries():
    """取缓存条目（进程内缓存，避免每个请求读一次网络盘上的 JSON）。"""
    global _ENTRIES
    if _ENTRIES is None:
        _ENTRIES = _load()
    return _ENTRIES


def file_md5(path, chunk=8192):
    """计算文件 md5（用于模板变更检测）。读不到返回空字符串。"""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
    except Exception:
        return ""
    return h.hexdigest()


def _load():
    """读缓存文件；损坏/缺失一律当作空缓存，绝不抛异常。"""
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
            return {}
        entries = data.get("entries")
        return entries if isinstance(entries, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(entries):
    global _ENTRIES
    _ENTRIES = entries
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": _CACHE_VERSION, "entries": entries},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 缓存写不进去不影响功能，下次重算而已


def get_keys(template_path, rel_path=None, extractor=None):
    """取模板的占位符分类结果，命中缓存则直接返回。

    参数:
        template_path: 模板绝对路径
        rel_path: 模板相对路径（写进缓存的 name 字段，仅供排查）
        extractor: 解析函数，签名 (path, strict=True) -> dict。
                   由调用方注入，避免这里反向依赖 report_generator。

    返回:
        dict: {"preset": [...], "project_preset": [...], "other": [...]}
              解析失败（文件读不了 / 不是合法 docx）时返回空 dict，
              **且不写缓存** —— 免得把一次偶发失败永久固化。
    """
    if extractor is None:
        return {}

    key = rel_path or template_path
    now = time.time()
    known = _STAT.get(key)

    if known and now - known[2] < _STAT_TTL:
        # TTL 内：连 stat 都省掉，直接用上次算出的 md5
        md5 = known[1]
    else:
        try:
            st = os.stat(template_path)
            sig = (st.st_size, st.st_mtime_ns)
        except OSError:
            return {}
        # size + mtime 没变 → 沿用上次算出的 md5，一个字节都不用读。
        # 模板是 Word 存的，改动必然动 mtime，所以这个判断是安全的。
        if known and known[0] == sig:
            md5 = known[1]
        else:
            md5 = file_md5(template_path)
            if not md5:
                return {}
        _STAT[key] = (sig, md5, now)

    if md5 in _MEMO:
        return _MEMO[md5]

    entries = _entries()
    hit = entries.get(md5)
    if hit and isinstance(hit.get("keys"), dict):
        _MEMO[md5] = hit["keys"]
        return hit["keys"]

    # 未命中：重新解析。strict 让异常抛出来，才能区分「真的没有占位符」和「解析失败」
    try:
        keys = extractor(template_path, strict=True)
    except Exception:
        return {}

    entries[md5] = {
        "name": rel_path or os.path.basename(template_path),
        "used_presets": sorted(set(keys.get("preset", []))
                              | set(keys.get("project_preset", []))
                              | set(keys.get("other", []))),
        "keys": keys,
        "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save(entries)
    _MEMO[md5] = keys
    return keys


def prune(keep_md5s):
    """清掉不再被任何现存模板引用的条目，避免缓存文件无限长大。"""
    keep = set(keep_md5s)
    entries = _entries()
    pruned = {k: v for k, v in entries.items() if k in keep}
    if len(pruned) != len(entries):
        _save(pruned)
    for k in list(_MEMO):
        if k not in keep:
            _MEMO.pop(k, None)
