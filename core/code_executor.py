"""
安全代码执行器 — 供预设「代码(code)」类型与网页内 Python 控制台共用。

设计:
- 代码按「函数体」语义执行：可直接写 return xxx，也支持 if/else/循环。
- 通过 exec 把代码包进一个函数再调用，函数返回值即结果。
- 注入受限 builtins（保留 type/str/int/float/len/min/max/abs/round/sum 等，
  剔除 __import__/open/eval/exec/compile 等危险项），并注入 datetime/date/timedelta。
- 执行环境 env 由调用方提供（报告 context + 公式参数扁平化），可引用
  report.end_date、dew_point 等。

安全说明: 这是「防误操作」级别的沙箱，面向系统管理员/模板作者，不构成
对抗恶意代码的强安全边界；不要在不可信输入上使用。
"""

import builtins
import textwrap
from datetime import datetime, date, timedelta, time

# 剔除的危险/无关内建
_BLOCKED_BUILTINS = {
    "__import__", "open", "eval", "exec", "compile", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "input", "breakpoint", "memoryview",
    "help", "exit", "quit", "copyright", "credits", "license",
    "classmethod", "staticmethod", "property", "super", "__build_class__",
    "object", "type",
}

_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in dir(builtins)
    if name not in _BLOCKED_BUILTINS and not name.startswith("__")
}
# 单独放回 type（用户在控制台要 type(report.end_date)）
_SAFE_BUILTINS["type"] = builtins.type


def _forbidden_import(*args, **kwargs):
    raise ImportError("import 已禁用（代码类型预设/控制台沙箱不支持导入模块）")


# 黑名单：即使允许 import 也拦截这些危险/重量级模块
_FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "shutil", "socket", "importlib", "pathlib",
    "builtins", "ctypes", "marshal", "pickle", "shelve", "dbm", "signal",
    "multiprocessing", "threading", "ftplib", "smtplib", "http", "urllib",
    "requests", "openpyxl", "docx", "flask", "runpy", "pty", "fcntl",
    "winreg", "_winreg", "glob", "io", "tempfile", "gc", "traceback",
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """允许导入标准库（datetime.strptime 需 import _strptime），拦截危险模块。"""
    top = str(name).split(".")[0]
    if top in _FORBIDDEN_MODULES:
        raise ImportError(f"模块 '{top}' 已禁用")
    return builtins.__import__(name, globals, locals, fromlist, level)


# __builtins__ 字典必须含 __import__ 键（否则 exec 时某些内部操作会 KeyError）
_SAFE_BUILTINS["__import__"] = _safe_import


class AttrDict(dict):
    """支持属性访问的 dict：d.end_date 等价 d['end_date']，两者皆可用。"""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


def _to_attr(obj):
    """递归把 dict 转成 AttrDict、list 元素递归转换，使代码里可用 . 访问嵌套字段。"""
    if isinstance(obj, dict):
        return AttrDict({k: _to_attr(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_attr(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_to_attr(v) for v in obj)
    return obj


def run(code, env=None):
    """执行一段代码并返回 (result, error)。

    参数:
        code: 代码文本（函数体语义，可含 return）
        env: 变量环境 dict（如 {"report": {...}, "dew_point": 0.3}）
             dict 会被递归包装为支持属性访问的对象（report.end_date 或
             report['end_date'] 均可）。

    返回:
        (result, error) — 成功时 error=None；失败时 result=None，error 为错误信息字符串。
    """
    if not code or not str(code).strip():
        return "", None

    env = _to_attr(env or {})

    # 执行环境：把变量 env 合并进 globals（函数体解析名字时用 globals）
    globals_dict = dict(env)
    globals_dict["__builtins__"] = _SAFE_BUILTINS
    globals_dict["datetime"] = datetime
    globals_dict["date"] = date
    globals_dict["timedelta"] = timedelta
    globals_dict["time"] = time

    # 把代码包成函数（支持 return）
    try:
        # 先尝试作为「表达式」求值（单表达式场景：type(x)、report.end_date、dew_point 等）
        try:
            return eval(compile(str(code), "<preset-code>", "eval"), globals_dict), None
        except SyntaxError:
            pass
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

        indented = textwrap.indent(str(code).rstrip(), "    ")
        wrapper = "def __run_code__():\n" + indented
        exec(compile(wrapper, "<preset-code>", "exec"), globals_dict)
        func = globals_dict.get("__run_code__")
        if not callable(func):
            return None, "代码未定义可执行的函数体"
        result = func()
        return result, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def run_to_string(code, env=None):
    """执行代码并把结果转成字符串（None→''，数字/日期→str，str→原样）。"""
    result, error = run(code, env)
    if error:
        return f"[代码错误] {error}"
    if result is None:
        return ""
    if isinstance(result, bool):
        return "是" if result else "否"
    return str(result)
