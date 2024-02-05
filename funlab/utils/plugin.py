
from importlib.metadata import entry_points
import logging
from .log import get_logger

_mylogger = get_logger(__name__, level=logging.INFO)

def load_plugins(group:str)->dict:
    plugins = {}
    # load dynamically, ref: https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/
    plugin_entry_points = entry_points(group=group)
    for entry_point in plugin_entry_points:
        plugin_name = entry_point.name
        try:
            # print(entry_point)
            plugin_class = entry_point.load()
            plugins[plugin_name] = plugin_class
            #_mylogger.info(f"Loaded plugin: {plugin_name}:{entry_point.value}")
        except Exception as e:
            _mylogger.error(f"Error loading plugin: {plugin_name} - {str(e)}")
            raise e
    return plugins
