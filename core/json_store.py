"""
JSON 定义文件的安全读写。

**这个模块存在的理由（踩过的坑）：**

这台机器跑在网络盘（SMB）上，而 Flask 的 `app.run()` 默认 `threaded=True`，
请求是并发的。于是「A 在读文件的同时 B 在写」是常态，读的一方会拿到半个文件。

原来的写法是各处各自 `except (JSONDecodeError, OSError): pass; return []` ——
**读失败和「确实一个条目都没有」返回同一个空列表**，后果有两个：

1. 误报「预设 'xxx' 不存在」之类的消息。用户拖动预设分类后编辑保存就会偶发撞上
   （读失败 → 找不到 key → 报「不存在」→ 重试一次又好了），极难排查。
2. **更糟**：调用方拿着空列表 save 回去，把整个定义文件清空 —— 静默丢数据。

所以这里的两条规矩：
- **读失败重试几次，仍失败就抛异常**，绝不返回空列表。宁可这次请求失败让用户重试。
- **写用「临时文件 + os.replace」**，读者只会看到旧的或新的完整内容，不会读到写了一半的。

另外配一把可重入锁，把「读 → 改 → 写回」整段串行化，避免并发请求互相覆盖（丢更新）。
"""

import os
import json
import time
import threading

_READ_RETRIES = 5
_READ_RETRY_DELAY = 0.12   # 秒，按次数递增（0.12 / 0.24 / 0.36 ...）

# 保护「读 JSON → 改 → 写回」的整段。可重入，容忍内部再入。
WRITE_LOCK = threading.RLock()


def read_json_list(path, what):
    """读一个 JSON 数组文件；失败重试后抛 RuntimeError（**绝不返回空列表**）。

    参数:
        path: 文件绝对路径
        what: 中文描述，拼进异常消息，如「项目通用预设定义」
    """
    last_err = None
    for attempt in range(_READ_RETRIES):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            last_err = ValueError("文件内容不是数组")
        except (json.JSONDecodeError, OSError) as e:
            last_err = e
        time.sleep(_READ_RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"读取{what}失败（已重试 {_READ_RETRIES} 次）：{last_err}")


def write_json_atomic(path, data):
    """原子写 JSON：先写临时文件再 os.replace。

    Windows 上目标文件可能正被别的线程读着，replace 会偶发失败，所以也重试。
    （这也正是原来 `open(path,'w')` 截断式写入的坏处 —— 它一定成功，
      但把正在读的人的文件内容截没了。）
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    last_err = None
    for attempt in range(_READ_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            last_err = e
            time.sleep(_READ_RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"写入 {os.path.basename(path)} 失败：{last_err}")
