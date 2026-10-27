"""
系统日志：把 Python logging 的日志落到 SQLite，供「日志」页签查看。

为什么要自己写 handler：项目原先没有任何 logging 配置，诊断全靠 136 处 print()，
日志只进 stdout、重启就没了。这里给 root logger 挂一个落库的 handler，
现有的 print() 逐步改成 logger 调用，依赖库（werkzeug/urllib3/docxtpl）的日志也一并收进来。

设计要点（都是为了「日志不能拖垮主流程」）：

- **写入异步**：emit() 只把格式化好的记录塞进有界 deque，后台线程定期批量落库。
  这台机器是网络盘，SQLite 写入偶发变慢，绝不能在请求线程里同步写。
- **队列满就丢**：宁可丢日志也不能阻塞或者爆内存。
- **全程吞异常**：日志系统自己出错不能把业务搞崩，也不能递归触发自己的日志。

表结构见 database/schema_sqlite.sql 的 system_logs。
"""

import atexit
import logging
import threading
import time
from collections import deque
from datetime import datetime

from config import (LOG_MAX_ROWS, LOG_MAX_WARNINGS, LOG_MAX_ERRORS,
                    LOG_LEVEL, LOG_TRIM_INTERVAL_SECONDS)

# 缓冲：攒够这么多或到时间就落库
_BUFFER_MAX = 500
_FLUSH_INTERVAL = 2.0      # 秒


class SqliteLogHandler(logging.Handler):
    """把日志记录缓冲后批量写入 system_logs 表。"""

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self._buf = deque(maxlen=_BUFFER_MAX)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._dropped = 0
        self._last_trim = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True, name="app-log-flusher")
        self._thread.start()

    # ── logging.Handler 接口 ──

    def emit(self, record):
        # 记录本身出错（比如格式串有问题）绝不能抛出去
        try:
            # 把 logger 名字当来源，方便按模块过滤
            detail = ""
            if record.exc_info:
                detail = self.format(record) if self.formatter else ""
            with self._lock:
                if len(self._buf) == self._buf.maxlen:
                    self._dropped += 1
                self._buf.append((
                    datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                    record.levelno,
                    record.levelname,
                    record.name,
                    record.getMessage()[:2000],
                    detail[:8000],
                    # 调用方用 logging 的 extra={"project_id": pid} 带上；
                    # 不带的（启动、同步等全局事件）就是 NULL
                    getattr(record, "project_id", None),
                ))
        except Exception:
            pass

    # ── 后台落库 ──

    def _loop(self):
        while not self._stop.wait(_FLUSH_INTERVAL):
            self.flush()

    def flush(self):
        with self._lock:
            if not self._buf:
                return
            batch = list(self._buf)
            self._buf.clear()
            dropped, self._dropped = self._dropped, 0
        if dropped:
            batch.append((
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                logging.WARNING, "WARNING", "app_log",
                f"日志缓冲队列已满，丢弃了 {dropped} 条（写入速度跟不上产生速度）", "", None,
            ))
        try:
            from core.project_manager import get_db
            conn = get_db()
            try:
                conn.executemany(
                    "INSERT INTO system_logs (ts, level, level_name, logger, message, detail, "
                    "                         project_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                conn.commit()
                self._maybe_trim(conn)
            finally:
                conn.close()
        except Exception:
            pass   # 落库失败就丢这一批，不能反过来刷屏

    def _maybe_trim(self, conn):
        """按 LOG_TRIM_INTERVAL_SECONDS 限频调用 _trim。

        _trim 每次都要 COUNT + DELETE + commit，本机是网络盘
        （journal_mode=delete，每次 commit 都要 fsync + 建删 journal），
        原来每 2 秒跑一次纯属浪费。这里最多每 30 秒跑一次。
        """
        now = time.time()
        if now - self._last_trim < LOG_TRIM_INTERVAL_SECONDS:
            return
        self._last_trim = now
        self._trim(conn)

    @staticmethod
    def _trim(conn):
        """按级别分档保留：各档超出自己的上限时，只删**本档**最旧的。

        关键点：**绝不跨级别删**。原来是一个总上限、按 id 删最旧的，
        高频 INFO 会直接把 ERROR 挤出去 —— 100 个项目 / 5 秒写入时
        整张表只覆盖约 4 分钟，等于 ERROR 全丢。
        """
        # (档位下限含, 档位上限不含, 上限条数)
        bands = (
            (logging.DEBUG,   logging.WARNING, LOG_MAX_ROWS),
            (logging.WARNING, logging.ERROR,   LOG_MAX_WARNINGS),
            (logging.ERROR,   999,             LOG_MAX_ERRORS),
        )
        for lo, hi, cap in bands:
            try:
                total = conn.execute(
                    "SELECT COUNT(*) AS c FROM system_logs WHERE level >= ? AND level < ?",
                    (lo, hi),
                ).fetchone()["c"]
                if total <= cap:
                    continue
                # 一次多删一点（10%），免得每条日志都触发一次删除
                cut = total - cap + max(1, cap // 10)
                conn.execute(
                    "DELETE FROM system_logs WHERE id IN "
                    "(SELECT id FROM system_logs WHERE level >= ? AND level < ? "
                    " ORDER BY id ASC LIMIT ?)",
                    (lo, hi, cut),
                )
                conn.commit()
            except Exception:
                pass

    def close(self):
        self._stop.set()
        try:
            self.flush()
        except Exception:
            pass
        super().close()


_handler = None


def setup_logging():
    """给 root logger 挂上落库 handler（幂等）。在 app 启动流程里调用。"""
    global _handler
    if _handler is not None:
        return _handler

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(LOG_LEVEL).upper(), logging.INFO))

    # 控制台仍保留一份，方便直接在终端看
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, SqliteLogHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)

    _handler = SqliteLogHandler()
    # detail 只放 traceback，正文由 emit 自己取 getMessage()，所以这里用纯消息格式
    _handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(_handler)

    # 这些库默认很吵，INFO 级能刷屏；除非把 LOG_LEVEL 调到 DEBUG，否则压到 WARNING
    for noisy in ("urllib3", "werkzeug", "PIL", "docx", "docxtpl"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    atexit.register(_shutdown)
    logging.getLogger(__name__).info("日志系统已启动（落库 system_logs，上限 %d 条）", LOG_MAX_ROWS)
    return _handler


def _shutdown():
    try:
        if _handler is not None:
            _handler.close()
    except Exception:
        pass


def query_logs(level="all", limit=200, before_id=None, project_id=None):
    """查日志。返回 (rows, total)。

    level: "all" 全部；"warning" 及以上；"error" 及以上
    project_id: 只看某个项目的日志；None = 全部
    before_id: **键集分页**游标，取 id 小于它的那些（即更早的）。
               不用 OFFSET —— 保留策略会从表头删旧行，用 OFFSET 翻页会漏读或重复。

    rows 按 id 倒序（最新在前）。
    """
    from core.project_manager import get_db
    conds, params = [], []
    if level == "warning":
        conds.append("level >= ?"); params.append(logging.WARNING)
    elif level == "error":
        conds.append("level >= ?"); params.append(logging.ERROR)
    if project_id is not None:
        conds.append("project_id = ?"); params.append(project_id)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM system_logs {where}", params).fetchone()["c"]
        page_where, page_params = where, list(params)
        if before_id is not None:
            page_where = (page_where + " AND id < ?") if page_where else "WHERE id < ?"
            page_params.append(before_id)
        rows = conn.execute(
            f"SELECT * FROM system_logs {page_where} ORDER BY id DESC LIMIT ?",
            page_params + [limit],
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def list_log_projects():
    """有日志的项目 (id, 名称)，供前端「按项目筛选」下拉用。"""
    from core.project_manager import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT DISTINCT l.project_id AS id, p.project_name AS name
               FROM system_logs l
               LEFT JOIN carbon_projects p ON p.id = l.project_id
               WHERE l.project_id IS NOT NULL
               ORDER BY l.project_id"""
        ).fetchall()
        return [{"id": r["id"], "name": r["name"] or f"项目 #{r['id']}"} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()
