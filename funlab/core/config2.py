from __future__ import annotations
import os
import pathlib
import re
import tomllib
from funlab.utils import log
# mylogger = log.get_logger(__name__)

class Config():
    def __init__(self, config_file_or_values:str|pathlib.Path|dict|Config, env_file_or_values:str|dict=None,
                 only_section:str=None) -> None:  #, case_insensitive=False
        """
        Initialize the Config object.

        Args:
            config_file_or_values (str | dict | pathlib.Path): The path to the configuration file or a dictionary containing the configuration values.
            env_file_or_values (str | dict, optional): The path to the environment file or a dictionary containing the environment values. Defaults to None.
            only_section (str, optional): The name of the section to read from the configuration file. Defaults to None.
            case_insensitive (bool, optional): Whether to perform case-insensitive key lookups. Defaults to False.

        Raises:
            FileNotFoundError: If the configuration file is not found.
        """
        if isinstance(env_file_or_values, str): 
            try:
                with open(env_file_or_values, "rb") as f:
                    self._env_vars = tomllib.load(f)
            except Exception as e:
                raise Exception(f"The {env_file_or_values} for configfile 'ENV_VAR' not exist.") from e
        elif env_file_or_values is None:
            self._env_vars = {}
        elif isinstance(env_file_or_values, dict):
            self._env_vars = env_file_or_values
        else:
            raise Exception(f"Wrong provide {env_file_or_values} for configfie 'ENV_VAR'. Check!")

        if isinstance(config_file_or_values, dict):
            self._from_dict(config_file_or_values, only_section)
        elif isinstance(config_file_or_values, Config):
            self._from_dict(self.as_dict(), only_section)
        else:
            config_file = self._lookup_config(config_file_or_values)
            with open(config_file, "rb") as f:
                self._from_dict(tomllib.load(f), only_section)

    def _lookup_config(self, file_path:str|pathlib.Path)->pathlib.Path:
        """lookup config.toml by following:
            1. if it is directory, set to 'config.toml' in 'conf' sub-directory of that directory
            2. if it is py program file, set to 'config.toml' in  'conf' sub-directory of same directory
            3. else, just check if exist and return Path object

        Args:
            file_path (str | pathlib.Path): the config file name, or a directory, or your py program file

        Raises:
            Exception: config file not found

        Returns:
            pathlib.Path: the config file
        """
        if file_path and isinstance(file_path, str):
            config_file = pathlib.Path(file_path)
        elif isinstance(file_path, pathlib.Path):
            config_file = file_path
        else:
             raise Exception(f'Unsupported file_path value:{file_path}')

        if config_file.is_dir():
            config_file = config_file.joinpath('conf/config.toml')
        elif config_file.is_file() and config_file.name.endswith('.py'):
            config_file = config_file.parent.joinpath('conf/config.toml')

        if not config_file.exists():
            raise FileNotFoundError(config_file)

        return config_file

    def _from_dict(self, data:dict, only_section:str):
        def replace_var_ref_in_config(value):
            if isinstance(value, dict):
                # Origina, here try to not do replace_var_ref_in_config everytimes, but just check if it is needed. Neet to test
                if True: # re.findall(r"{{([\w.:]+)}}", str(value)): # str(value).find(r'{{')>=0:
                    new_dict = {}
                    for key, value in value.items():
                        new_dict.update({key: replace_var_ref_in_config(value)})
                    return new_dict
                else:
                    return value
            elif isinstance(value, list):
                # Origina, here try to not do replace_var_ref_in_config everytimes, but just check if it is needed. Neet to test
                if True: # re.findall(r"{{([\w.:]+)}}", str(value)): # str(value).find(r'{{')>=0:
                    new_list = []
                    for val in value:
                        new_list.append(replace_var_ref_in_config(val))
                    return new_list
                else:
                    return value
            elif isinstance(value, str):
                variables = re.findall(r"{{([\w.:]+)}}", value)  # e.g. {{username}}, {{ENV_VAR:POSTGRE_USER}}
                var:str
                for var in variables:
                    if var.startswith('ENV_VAR:'):  # get from environment variable with encoding check
                        try:
                            var_name = var[len('ENV_VAR:'):]
                            var_value:str = self._env_vars[var_name]
                            value = value.replace('{{'+var+'}}', var_value)
                        except KeyError as e:
                            raise Exception(f"You need provide 'envfile' for found {var} setting in configfile.") from e
                    else:
                        ref_var = var.split('.')
                        ref_value = data
                        for ref in ref_var:
                            try:
                                ref_value = ref_value[ref]
                            except Exception as e:
                                raise Exception(f'Fail to get "{var}" of {ref}. raw value={value}') from e
                        if isinstance(ref_value, dict):
                            for key, value in ref_value.items():
                                ref_value.update({key: replace_var_ref_in_config(value)})
                        return ref_value
            return value

        def set_dict_attr(dictattrs:dict):
            for key, value in dictattrs.items():
                if isinstance(value, list):
                    new_value = []
                    for val in value:
                        new_value.append(replace_var_ref_in_config(val))
                else:
                    new_value = replace_var_ref_in_config(value)
                # if re.findall(r"{{([\w.:]+)}}", str(new_value)):
                #     print('still exist variable!? ')
                setattr(self, key, new_value)
        if only_section:
            data = data.get(only_section, {})
            setattr(self, only_section, data)
            set_dict_attr(data)
        else:
            set_dict_attr(data)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            raise ValueError()

    def __contains__(self, key):
        return key in self.keys()

    def items(self)->list[tuple]:
        return [(key, value,) for key, value in vars(self).items() if(not key.startswith('_'))]

    def keys(self)->list:
        return [key for key in vars(self).keys() if(not key.startswith('_'))]

    def values(self)->list:
        return [value for key, value in vars(self).items() if(not key.startswith('_'))]

    def as_dict(self):
        d = {key:val for key, val in vars(self).items() if(not key.startswith('_')) }
        return d

    def update_with_ext(self, ext_conf:Config | dict, section:str=None):
        if not section:
            update_section = self.as_dict()
            # ext_conf = {}
        elif section in self:
            update_section = self.get(section)
        else:
            update_section = {}
        # else:
        #     raise Exception(f"No section [{section}] in config data to update.")
        if not section:
            ext_conf = ext_conf.as_dict()
        elif section in ext_conf:
            ext_conf = ext_conf.get(section)
        else:
            # if isinstance(ext_conf, Config):
            #     ext_conf = ext_conf.as_dict()
            raise Exception(f"No section [{section}] in config data to update.")

        update_section.update(ext_conf)
        setattr(self, section, update_section)
        pass

    def get(self, attrname:str, default=None, case_insensitive=False)->dict:
        if value:=getattr(self, attrname, None):
            return value
        
        if case_insensitive:
            attrname = attrname.lower()
            attrs = {k.lower(): v for k, v in vars(self).items()}
            return attrs.get(attrname, default)
        return default

    def get_section_config(self, section:str, default=None, case_insensitive=False, keep_section=False)->Config:
        try:
            cfg_dict:dict = self.as_dict()
            orig_section = section
            if case_insensitive:
                section = section.upper()
                cfg_dict = { key.upper(): value for key, value in cfg_dict.items()}

            cfg_dict = cfg_dict[section]
            if not isinstance(cfg_dict, dict) or keep_section:
                cfg_dict = { orig_section: cfg_dict}
            config = Config(cfg_dict, env_file_or_values=self._env_vars)
        except KeyError as e:
            if default is None:
                raise Exception(f"Can not get section [{section}] from config data.") from e
            else:
                return default
        return config
