"""PR-3 §0 子任務測試：create_form_from_dataclass 支援 SelectField。

規格：finfun-fundmgr/docs/dev-specs/PR-3-fundmgr-bookkeeping-integration.md §0
"""
from dataclasses import dataclass, field

import pytest
from flask import Flask
from wtforms import SelectField, StringField

from funlab.utils.form import create_form_from_dataclass


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    return app


@dataclass
class _SampleParams:
    mode: str = field(
        default='verify',
        metadata={'type': 'SelectField',
                  'choices': [('verify', '僅驗證'), ('apply', '驗證通過後寫入')]},
    )


def test_select_field_type_resolves_correctly(app):
    form_cls = create_form_from_dataclass(_SampleParams)
    with app.test_request_context():
        form = form_cls()
        assert isinstance(form.mode, SelectField)
        # 未支援前會被靜默降級為 StringField；確認非純文字欄
        assert type(form.mode) is SelectField


def test_select_field_choices_passed_through(app):
    form_cls = create_form_from_dataclass(_SampleParams)
    with app.test_request_context():
        form = form_cls()
        assert list(form.mode.choices) == [('verify', '僅驗證'), ('apply', '驗證通過後寫入')]


def test_other_field_types_unaffected(app):
    """既有型別解析不受新增 mapping 條目影響（回歸）。"""
    @dataclass
    class _Mixed:
        name: str = 'x'
        count: int = 1

    from wtforms import IntegerField
    form_cls = create_form_from_dataclass(_Mixed)
    with app.test_request_context():
        form = form_cls()
        assert isinstance(form.name, StringField)
        assert isinstance(form.count, IntegerField)
