
import copy
from dataclasses import is_dataclass
import dataclasses
from datetime import date, datetime, time, timedelta
import inspect
import json

from pathlib import Path
from funlab.utils import dtts
from funlab.core.config import Config

class _Configuable:
    """
    A base class for class that can be configured using a configuration file.
   """

    def get_config(self, file_name: str, section=None, ext_config: Config = None, case_insensitive=False) -> Config:
        """
        Retrieves the configuration from a specified file.

        Args:
            file_name (str): The name of the configuration file in TOML format. The file path is de
            section (str, optional): The section within the configuration file. Defaults to None.
            ext_config (Config, optional): An external configuration object for overriding. Defaults to None.
            case_insensitive (bool, optional): Flag indicating whether the configuration should be case-insensitive. Defaults to False.

        Returns:
            Config: The configuration object.

        """
        def _esclate_env_settings(config:Config):
            if (env_conf:= config.get('ENV')):
                for key, value in env_conf.items():
                    setattr(config, key, value)
                del config.ENV

        if not section:
            section = self.__class__.__name__

        root = Path(inspect.getmodule(self).__file__).parent
        conf_file = root.joinpath(f'conf/{file_name}')
        if conf_file.exists():
            local_config = Config(conf_file, env_file_or_values=ext_config._env_vars if ext_config else {},)
                                    # case_insensitive=case_insensitive)
        else:
            local_config = Config({})

        if ext_config:
            local_config.update_with_ext(ext_conf=ext_config, section=section)

        if section in local_config:
            config = local_config.get_section_config(section,
                                                    case_insensitive=case_insensitive)
        else:
            config = local_config
        _esclate_env_settings(config)
        return config

class _Readable:
    """
    A mixin class that provides methods for converting an object to a readable format.

    Methods:
    - __to_readable__(attr_name:str, attr_type:str|type): Converts an attribute to a readable format.
    - __readattrs__(): Returns a dictionary of readable attributes.
    - __str__(): Returns a string representation of the object.
    - to_json(): Converts the object to a JSON string.
    """

    def __to_readable__(self, attr_name:str, attr_type:str|type):
        """
        Converts an attribute to a readable format.

        Parameters:
        - attr_name (str): The name of the attribute.
        - attr_type (str|type): The type of the attribute.

        Returns:
        - dict: A dictionary containing the readable attribute.
        """
        if isinstance(attr_type, type):
            attr_type = str(attr_type)
            c, n, *_ = str(type(attr_type)).split("'")
            if c.endswith('class'):
                attr_type = n
            else:
                *_, attr_type = c.strip().split('<')

        if attr_name == 'timestamp' or attr_name.endswith('_ts'):
            val = getattr(self, attr_name)
            val:datetime = dtts.utc_timestamp2local_datetime(val) #datetime.fromtimestamp(val).replace(tzinfo=timezone.utc).astimezone(tz=LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S') if val else ''
            if isinstance(val, datetime) and val - datetime.combine(val.date(), time(0, 0, 0)) == timedelta(0):
                val = val.date()
            val = val.isoformat()
        elif attr_type in ('datetime', 'date') :
            val = getattr(self, attr_name).isoformat()
        elif attr_type == 'float':
            val = float(getattr(self, attr_name))
            if attr_name.endswith('_rate') or attr_name.endswith('_ratio'):
                val = f'{val:.2%}'
            elif int(val) == getattr(self, attr_name):
                val = f'{val:.0f}'
            else:
                val = f'{getattr(self, attr_name):.2f}'
        elif attr_type == 'int':
            val = f'{int(getattr(self, attr_name)):d}'
        elif attr_type == 'enum':
            val = getattr(self, attr_name)
            val = f'{val.name}'
        elif attr_name=='password' or attr_name=='passwd':
            val = '***'
        else:
            val = getattr(self, attr_name)
        return {attr_name: val}

    def __readattrs__(self)-> dict:
        """
        Returns a dictionary of readable attributes.

        Returns:
        - dict: A dictionary containing the readable attributes.
        """
        attrs = {}
        fields = dataclasses.fields(self)
        for field in fields:
            if field.init:
                attrs.update(self.__to_readable__(field.name, field.type))
        # try:
        #     extra_readattr = self.__extra_readattr.items()
        # except:
        #     extra_readattr = {}

        # for attr_name, attr_type in extra_readattr:
        #     attrs.update(self.__to_readable__(attr_name, attr_type))

        property_names=[p for p in dir(self) if isinstance(getattr(self,p),property)]

        for prop in property_names:
            prop_type = type(getattr(self, prop))
            attrs.update(self.__to_readable__(prop, prop_type))
        return attrs

    def __str__(self):
        """
        Returns a string representation of the object.

        Returns:
        - str: A string representation of the object.
        """
        return f'{self.__class__.__name__}{self.__readattrs__()}'

    def to_json(self) -> str:
        """
        Converts the object to a JSON string.

        Returns:
        - str: A JSON string representation of the object.
        """
        return json.dumps(self, cls=DataclassJSONEncoder)
    # def __repr__(self):
    #     return f'{self.__readattrs__()}'  #self.to_json()}'

    # def to_dict(self)->dict:
    #     pass
class DataclassJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if is_dataclass(o):
            if isinstance(o, _Readable):
                return o.__readattrs__()
            else:
                attrs = {}
                for field in dataclasses.fields(self):
                    if field.repr:
                        if field.name == 'timestamp' or field.name.endswith('_ts'):
                            val = getattr(self, field.name)
                            val:datetime = dtts.utc_timestamp2local_datetime(val) #datetime.fromtimestamp(val).replace(tzinfo=timezone.utc).astimezone(tz=LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S') if val else ''
                            if isinstance(val, datetime) and val - datetime.combine(val.date(), time(0, 0, 0)) == timedelta(0):
                                val = val.date()
                            val = val.isoformat()
                        elif field.type in (datetime, date) :
                            val = getattr(self, field.name).isoformat()
                        else:
                            val = getattr(self, field.name)
                        attrs[field.name] = val
                return f'{attrs}'
        elif type(o) in (datetime, date) :
            return o.isoformat()
        elif hasattr(o, 'to_json'):
            return o.to_json()
        return super().default(o)
    
class _Extendable:
    """
    A base class that provides the ability to dynamically add extra attributes to an object.

    Attributes:
        _extra_data (dict): A dictionary to store the extra attributes added to the object.
    """

    def __init__(self) -> None:
        self._extra_data = {}

    def __copy__(self):
        cls = self.__class__
        result = cls.__new__(cls)
        result.__dict__.update(self.__dict__)
        return result

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            setattr(result, k, copy.deepcopy(v, memo))
        return result

    def __getattr__(self, key):
        if key in self._extra_data:
            val = self._extra_data[key]
            return val
        return super().__getattribute__(key)

    def set_attr(self, attr, value):
        self._extra_data[attr] = value
