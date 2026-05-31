"""
Model Hook Mixin for SQLAlchemy Models

提供 Model 層級的 Hook 觸發點，讓 Plugin 可以在資料庫操作時執行擴充邏輯。

使用方式:
    from funlab.core.model_hook import ModelHookMixin
    from sqlalchemy.orm import Session

    class MyModel(ModelHookMixin, Base):
        ...

    # 在 save/delete 操作時自動觸發 hooks
    instance = MyModel()
    instance.save(session, app)  # 觸發 model_before_save, model_after_save hooks
"""

from typing import TYPE_CHECKING, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import inspect as sa_inspect

__all__ = ["ModelHookMixin"]

if TYPE_CHECKING:
    from flask import Flask


class ModelHookMixin:
    """
    Model Hook Mixin - 提供資料庫操作的 Hook 觸發點

    可用的 Hook 點:
    - model_before_save: 在物件 save 前觸發
    - model_after_save: 在物件 save 後觸發
    - model_before_delete: 在物件 delete 前觸發
    - model_after_delete: 在物件 delete 後觸發
    - model_after_create: 在物件首次建立後觸發
    """

    def save(self, session: Session, app: Optional['Flask'] = None, commit: bool = True) -> 'ModelHookMixin':
        """
        儲存物件到資料庫，觸發 before_save 和 after_save hooks

        Args:
            session: SQLAlchemy Session
            app: Flask app 實例（用於存取 hook_manager）
            commit: 是否自動 commit

        Returns:
            self
        """
        # session.add() is a no-op for persistent objects; for transient ones it
        # moves them to 'pending' state.  We check *after* add so that
        # inspect(self).pending reliably distinguishes new vs existing objects.
        session.add(self)
        is_new = sa_inspect(self).pending  # True = new (not yet flushed), False = updating existing

        # 觸發 before_save hook
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_save',
                model=self,
                model_class=self.__class__,
                session=session,
                is_new=is_new
            )

        if commit:
            session.commit()

        # 觸發 after_save hook
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_save',
                model=self,
                model_class=self.__class__,
                session=session,
                is_new=is_new
            )

            # 如果是新建物件，額外觸發 after_create
            if is_new:
                app.hook_manager.call_hook(
                    'model_after_create',
                    model=self,
                    model_class=self.__class__,
                    session=session
                )

        return self

    def delete(self, session: Session, app: Optional['Flask'] = None, commit: bool = True) -> None:
        """
        從資料庫刪除物件，觸發 before_delete 和 after_delete hooks

        Args:
            session: SQLAlchemy Session
            app: Flask app 實例（用於存取 hook_manager）
            commit: 是否自動 commit
        """
        # 觸發 before_delete hook
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_delete',
                model=self,
                model_class=self.__class__,
                session=session
            )

        session.delete(self)

        if commit:
            session.commit()

        # 觸發 after_delete hook
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_delete',
                model=self,
                model_class=self.__class__,
                session=session
            )
