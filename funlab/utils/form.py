from dataclasses import dataclass, field, fields
from flask_wtf import FlaskForm
from wtforms import DateTimeField, Form, StringField, IntegerField, FloatField, BooleanField, DateField, SelectField
from wtforms.validators import DataRequired, Email, Length, Optional as OptionalValidator
from typing import get_type_hints, Optional, Dict, Any, Type, List, Union
import datetime
from datetime import date

# 映射 Python 類型到 WTForms 欄位類型
TYPE_MAPPING = {
    str: StringField,
    int: IntegerField,
    float: FloatField,
    bool: BooleanField,
    datetime.date: DateField,
    date: DateField
}

# 從 dataclass 生成 WTForm
def create_form_from_dataclass(dataclass_type):
    TYPE_MAPPING = {
        str: StringField,
        int: IntegerField,
        float: FloatField,
        bool: BooleanField,
        datetime.date: DateField,
        datetime.datetime: DateTimeField
    }
    form_fields = {}
    type_hints = get_type_hints(dataclass_type)
    for field in fields(dataclass_type):
        field_metadata = field.metadata
        field_name = field.name
        field_type = type_hints[field_name]
                    # 處理 Optional 類型
        is_optional = False
        if hasattr(field_type, "__origin__") and field_type.__origin__ is Union:
            args = field_type.__args__
            if type(None) in args:
                is_optional = True
                # 找出非 None 的類型
                field_type = next(arg for arg in args if arg is not type(None))
        form_field_class = field_metadata.get('type', TYPE_MAPPING.get(field_type, StringField))

        field_kwargs = {}
        for key, value in field_metadata.items():
            if key=='default':
                if callable(value):
                    try:
                        value = value()
                    except TypeError:
                        value = value(dataclass_type)  # if default value need dataclass_type, like id, name
                field_kwargs.update({key: value})
            elif key != 'type':
                field_kwargs.update({key:value})
        # field_kwargs.update({'default':field_metadata.get('default', None)})
        for key, value in field_kwargs.copy().items():
            if value is None:
                field_kwargs.pop(key)

        # 如果是可選欄位且沒有明確設置驗證器，則不添加 DataRequired
        if is_optional and not field_kwargs.get('validators', None):
            field_kwargs['validators'].append(OptionalValidator())

        form_fields[field.name] = form_field_class(**field_kwargs)
    if getattr(dataclass_type, 'form_javascript', None):
        form_fields['javascript'] = dataclass_type.form_javascript()

    form_class = type(dataclass_type.__name__+ 'ParamsForm', (FlaskForm,), form_fields)
    return form_class

# 使用範例
@dataclass
class PriceQuery:
    from_date: date = field(
        metadata={
            'type': DateField,
            'label': 'From Date',
            'default': date.today(),
            'description': 'The start date to fetch daily price. Format: yyyy/mm/dd',
            'render_kw': {"placeholder": 'yyyy-mm-dd'},
        }
    )
    to_date: date = field(
        metadata={
            'type': DateField,
            'label': 'To Date',
            'validators': [DataRequired()],
            'description': 'The end date to fetch daily price. Format: yyyy/mm/dd',
            'render_kw': {"placeholder": 'yyyy-mm-dd'},
        }
    )
    symbol: str = field(
        metadata={
            'type': StringField,
            'label': 'Stock Symbol',
            'validators': [DataRequired(), Length(min=1, max=10)],
            'description': 'Stock symbol (e.g., AAPL, MSFT)',
            'render_kw': {"placeholder": 'Enter stock symbol'},
        }
    )
    data_type: str = field(
        metadata={
            'type': SelectField,
            'label': 'Data Type',
            'choices': [('open', 'Open'), ('close', 'Close'), ('high', 'High'), ('low', 'Low'), ('volume', 'Volume')],
            'default': 'close',
            'description': 'Type of price data to fetch',
        }
    )
    include_dividends: Optional[bool] = field(
        default=False,
        metadata={
            'type': BooleanField,
            'label': 'Include Dividends',
            'description': 'Whether to include dividend information',
        }
    )

# 創建表單類
# PriceQueryForm = create_form_from_dataclass(PriceQuery)

# # 使用範例
# def example_usage():
#     # 創建表單實例
#     form = PriceQueryForm()

#     # 驗證數據
#     test_data = {
#         'from_date': '2024-01-01',
#         'to_date': '2024-02-28',
#         'symbol': 'AAPL',
#         'data_type': 'close',
#         'include_dividends': 'true'
#     }

#     if form.validate(test_data):
#         print("驗證成功!")
#         # 創建 dataclass 實例
#         query = PriceQuery(
#             from_date=form.from_date.data,
#             to_date=form.to_date.data,
#             symbol=form.symbol.data,
#             data_type=form.data_type.data,
#             include_dividends=form.include_dividends.data
#         )
#         print(f"創建查詢: {query}")
#     else:
#         print("驗證失敗:", form.errors)

#     # 打印表單欄位的屬性，以便檢查是否正確配置
#     print("\n表單欄位屬性:")
#     for field_name, field_obj in form._fields.items():
#         print(f"{field_name}:")
#         print(f"  - 標籤: {field_obj.label.text}")
#         print(f"  - 描述: {field_obj.description}")
#         print(f"  - 預設值: {field_obj.default}")
#         if hasattr(field_obj, 'choices') and field_obj.choices:
#             print(f"  - 選項: {field_obj.choices}")
#         if field_obj.render_kw:
#             print(f"  - 渲染屬性: {field_obj.render_kw}")
#         print()

# # 額外示例：Flask應用中的使用方式
# def flask_example():
#     """
#     如何在 Flask 應用中使用這個功能的示例代碼
#     (注意：這只是示範，不會實際執行)
#     """
#     from flask import Flask, render_template, request

#     app = Flask(__name__)

#     @app.route('/price-query', methods=['GET', 'POST'])
#     def price_query():
#         form = PriceQueryForm(request.form if request.method == 'POST' else None)

#         if request.method == 'POST' and form.validate():
#             # 創建 dataclass 實例
#             query = PriceQuery(
#                 from_date=form.from_date.data,
#                 to_date=form.to_date.data,
#                 symbol=form.symbol.data,
#                 data_type=form.data_type.data,
#                 include_dividends=form.include_dividends.data
#             )

#             # 處理查詢...
#             result = {"message": "查詢成功", "query": str(query)}
#             return render_template('result.html', result=result)

#         return render_template('query_form.html', form=form)

# # 執行範例
# if __name__ == "__main__":
#     example_usage()
#     # flask_example() # 這只是示例，不會實際執行