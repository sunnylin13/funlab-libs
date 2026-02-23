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
from sqlalchemy import event

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
        is_new = session.is_modified(self, include_collections=False) if self in session else True

        # 觸發 before_save hook
        if app and hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_save',
                model=self,
                model_class=self.__class__,
                session=session,
                is_new=is_new
            )

        session.add(self)

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


def register_model_events(app: 'Flask', model_class: type) -> None:
    """
    為 SQLAlchemy Model 註冊事件監聽器，自動觸發 hooks

    這是另一種觸發 model hooks 的方式，不需要手動呼叫 save/delete 方法。
    適用於無法修改既有程式碼，但想要自動觸發 hooks 的情況。

    使用方式:
        from funlab.core.model_hook import register_model_events
        from myapp.models import User

        register_model_events(app, User)

    Args:
        app: Flask app 實例
        model_class: 要監聽的 Model 類別
    """
    @event.listens_for(model_class, 'before_insert')
    def before_insert(mapper, connection, target):
        if hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_save',
                model=target,
                model_class=model_class,
                session=None,
                is_new=True
            )

    @event.listens_for(model_class, 'after_insert')
    def after_insert(mapper, connection, target):
        if hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_save',
                model=target,
                model_class=model_class,
                session=None,
                is_new=True
            )
            app.hook_manager.call_hook(
                'model_after_create',
                model=target,
                model_class=model_class,
                session=None
            )

    @event.listens_for(model_class, 'before_update')
    def before_update(mapper, connection, target):
        if hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_save',
                model=target,
                model_class=model_class,
                session=None,
                is_new=False
            )

    @event.listens_for(model_class, 'after_update')
    def after_update(mapper, connection, target):
        if hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_save',
                model=target,
                model_class=model_class,
                session=None,
                is_new=False
            )

    @event.listens_for(model_class, 'before_delete')
    def before_delete(mapper, connection, target):
        if hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_before_delete',
                model=target,
                model_class=model_class,
                session=None
            )

    @event.listens_for(model_class, 'after_delete')
    def after_delete(mapper, connection, target):
        if hasattr(app, 'hook_manager'):
            app.hook_manager.call_hook(
                'model_after_delete',
                model=target,
                model_class=model_class,
                session=None
            )
