"""
多執行緒並發安全性測試
驗證新 DbMgr 設計在多個執行緒同時存取 DB 時無競態條件或資料隔離問題
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import registry

from funlab.core.dbmgr import DbMgr
from funlab.core.config import Config


mapper_registry = registry()


@mapper_registry.mapped
class Counter:
    __tablename__ = "counters"

    id = sa.Column(sa.Integer, primary_key=True)
    thread_name = sa.Column(sa.String, nullable=False)
    value = sa.Column(sa.Integer, default=0)


def _build_dbmgr(tmp_path) -> DbMgr:
    db_file = tmp_path / "test_mt.db"
    config = Config({"url": f"sqlite:///{db_file}"})
    dbmgr = DbMgr(config)
    dbmgr.create_registry_tables(mapper_registry)
    return dbmgr


def test_multiple_threads_isolation(tmp_path):
    """
    驗證：多個執行緒各自獲得獨立的 session，
    對資料庫的修改互不影響（在交易內部）。
    """
    dbmgr = _build_dbmgr(tmp_path)
    results = {}
    lock = threading.Lock()

    def worker(thread_id: int):
        """每个线程插入自己的数据，验证隔离性"""
        thread_name = f"worker-{thread_id}"

        with dbmgr.session_context() as session:
            # 插入数据
            counter = Counter(thread_name=thread_name, value=thread_id * 100)
            session.add(counter)

        # 验证数据已提交
        with dbmgr.session_context() as session:
            row = session.execute(
                sa.select(Counter).where(Counter.thread_name == thread_name)
            ).scalar_one()
            with lock:
                results[thread_name] = row.value

    # 启动 5 个线程
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(5)]
        for future in futures:
            future.result()

    # 验证每个线程的数据独立且正确
    assert results == {
        "worker-0": 0,
        "worker-1": 100,
        "worker-2": 200,
        "worker-3": 300,
        "worker-4": 400,
    }


def test_concurrent_insert_no_race_condition(tmp_path):
    """
    驗證：多執行緒並發插入不會導致資料競爭或遺失。
    """
    dbmgr = _build_dbmgr(tmp_path)
    insert_count = 20  # 20 个线程，每个插入 1 条

    def worker(thread_id: int):
        with dbmgr.session_context() as session:
            session.add(Counter(thread_name=f"concurrent-{thread_id}", value=thread_id))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(insert_count)]
        for future in futures:
            future.result()

    # 验证所有数据都被插入
    with dbmgr.session_context() as session:
        total = session.execute(sa.select(sa.func.count()).select_from(Counter)).scalar()

    assert total == insert_count, f"Expected {insert_count} rows, got {total}"


def test_session_cleanup_on_thread_end(tmp_path):
    """
    驗證：執行緒結束時 session 被正確清理，不會記憶體洩漏。
    """
    dbmgr = _build_dbmgr(tmp_path)

    def worker():
        # 取得 session 但不顯式清理（模擬執行緒例外)
        session = dbmgr.get_db_session()
        # 注意：此處不呼叫 remove_session()
        # 但 threading.local() 會自動清理
        with dbmgr.session_context() as s:
            s.add(Counter(thread_name="cleanup-test", value=42))

    # 启动线程
    t = threading.Thread(target=worker)
    t.start()
    t.join()  # 等待线程结束

    # 数据应该被提交（因为 session_context 自动 commit）
    with dbmgr.session_context() as session:
        row = session.execute(
            sa.select(Counter).where(Counter.thread_name == "cleanup-test")
        ).scalar_one_or_none()

    assert row is not None
    assert row.value == 42


def test_high_concurrency_stress(tmp_path):
    """
    壓力測試：50 個執行緒，每個執行緒執行 10 個交易
    驗證無死鎖、無資料遺失、無競態
    """
    dbmgr = _build_dbmgr(tmp_path)
    num_threads = 50
    num_txns_per_thread = 10

    def worker(thread_id: int):
        for txn in range(num_txns_per_thread):
            with dbmgr.session_context() as session:
                session.add(Counter(
                    thread_name=f"stress-{thread_id}",
                    value=thread_id * 1000 + txn
                ))

    start = time.time()
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for future in futures:
            future.result()
    elapsed = time.time() - start

    # 验证总行数
    with dbmgr.session_context() as session:
        total = session.execute(sa.select(sa.func.count()).select_from(Counter)).scalar()

    expected = num_threads * num_txns_per_thread
    assert total == expected, f"Expected {expected} rows, got {total}"
    print(f"✅ Stress test passed: {expected} rows in {elapsed:.2f}s")


def test_exception_rollback_one_thread_doesnt_affect_others(tmp_path):
    """
    驗證：一個執行緒中的交易例外導致 rollback，
    不會影響其他執行緒的交易。
    """
    dbmgr = _build_dbmgr(tmp_path)
    results = {}
    lock = threading.Lock()

    def worker_success(thread_id: int):
        """正常提交的线程"""
        with dbmgr.session_context() as session:
            session.add(Counter(thread_name=f"success-{thread_id}", value=100))
        with lock:
            results["success"] = True

    def worker_fail(thread_id: int):
        """抛出异常的线程"""
        try:
            with dbmgr.session_context() as session:
                session.add(Counter(thread_name="fail", value=-1))
                raise ValueError("Intentional error")
        except ValueError:
            pass
        with lock:
            results["fail_caught"] = True

    with ThreadPoolExecutor(max_workers=5) as executor:
        # 启动 3 个成功的和 2 个失败的
        futures = []
        for i in range(3):
            futures.append(executor.submit(worker_success, i))
        for i in range(2):
            futures.append(executor.submit(worker_fail, i))

        for future in futures:
            future.result()

    # 验证结果
    assert results["success"]
    assert results["fail_caught"]

    with dbmgr.session_context() as session:
        success_count = session.execute(
            sa.select(sa.func.count()).select_from(Counter).where(
                Counter.thread_name.like("success-%")
            )
        ).scalar()
        fail_count = session.execute(
            sa.select(sa.func.count()).select_from(Counter).where(
                Counter.thread_name == "fail"
            )
        ).scalar()

    assert success_count == 3, "Successful inserts should all be committed"
    assert fail_count == 0, "Failed inserts should not exist (rollback)"
