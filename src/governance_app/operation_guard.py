from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from governance_app.config import AppConfig


class OperationConflict(ValueError):
    pass


_registry_lock = Lock()
_workspace_locks: dict[str, Lock] = {}


@contextmanager
def exclusive_operation(config: AppConfig, operation: str) -> Iterator[None]:
    key = str(config.workspace_dir.resolve())
    with _registry_lock:
        lock = _workspace_locks.setdefault(key, Lock())
    if not lock.acquire(blocking=False):
        raise OperationConflict("系统正在执行其他数据操作，请稍后重试")
    try:
        yield
    finally:
        lock.release()
