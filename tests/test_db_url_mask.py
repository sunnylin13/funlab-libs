"""LIB-02：mask_db_url 必須遮掉完整密碼且保留可讀部分。"""
from funlab.core.dbmgr import mask_db_url


def test_masks_full_long_password():
    url = "postgresql+psycopg://fund:VeryLongSecretPw@127.0.0.1:5432/fund13"
    out = mask_db_url(url)
    assert "VeryLong" not in out
    assert out == "postgresql+psycopg://fund:***@127.0.0.1:5432/fund13"


def test_no_password_unchanged():
    url = "sqlite:////tmp/app.db"
    assert mask_db_url(url) == url


def test_non_url_string_unchanged():
    assert mask_db_url("not a url") == "not a url"
