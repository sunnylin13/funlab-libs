"""LIB-04：PEP 604 `X | None` 必須與 typing.Optional[X] 同語意。"""
from dataclasses import dataclass, field

import flask
import pytest

from funlab.utils.form import create_form_from_dataclass


@pytest.fixture
def app():
    a = flask.Flask(__name__)
    a.config['SECRET_KEY'] = 'test'
    return a


def test_pep604_gets_optional_validator_and_int_field(app):
    @dataclass
    class T604:
        n: int | None = None

    with app.test_request_context():
        form = create_form_from_dataclass(T604)()
        from wtforms import IntegerField
        from wtforms.validators import Optional as OptionalValidator
        assert isinstance(form.n, IntegerField)
        assert any(isinstance(v, OptionalValidator) for v in form.n.validators)


def test_typing_optional_behaviour_unchanged(app):
    from typing import Optional

    @dataclass
    class TOld:
        n: Optional[int] = None

    with app.test_request_context():
        form = create_form_from_dataclass(TOld)()
        from wtforms.validators import Optional as OptionalValidator
        assert any(isinstance(v, OptionalValidator) for v in form.n.validators)


def test_required_field_has_no_optional_validator(app):
    @dataclass
    class TReq:
        n: int = 0

    with app.test_request_context():
        form = create_form_from_dataclass(TReq)()
        from wtforms.validators import Optional as OptionalValidator
        assert not any(isinstance(v, OptionalValidator) for v in form.n.validators)
