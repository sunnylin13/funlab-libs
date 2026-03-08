import dataclasses
import importlib
import inspect
from pathlib import Path
import logging
from funlab.utils import log
mylogger = log.get_logger(__name__, level=logging.INFO)

def get_caller_module(level=1):
    # Get the full stack
    stack = inspect.stack()
    # Get one level up from current
    previous_stack_frame = stack[level]  # previous_stack_frame.filename  # Filename where caller lives
    # Get the module object of the caller
    caller_module = inspect.getmodule(previous_stack_frame[0])
    #print(caller_module.__file__)
    return caller_module

def get_package_dir(fromfile, packagename):
    current_dir = Path(fromfile)
    try:
        rootdir = next(p for p in current_dir.parents if p.name == packagename)
    except StopIteration:
        raise Exception(f"Not found the packagename:{packagename}, check!")
    return rootdir

def get_class(class_name:str, from_module=None) :
    if from_module:
        mylogger.progress(f"Getting class {class_name} from module {from_module} ...", key=f'get_class_{class_name}')
        module = importlib.import_module(from_module)
        mylogger.end_progress(f"Got class {class_name} from module {from_module}.", key=f'get_class_{class_name}')
        return getattr(module, class_name)
    else :
        frm = inspect.stack()[1]
        from_module = inspect.getmodule(frm[0])  # get calling from module
        mylogger.progress(f"Getting class {class_name} from module {from_module} ...", key=f'get_class_{class_name}')
        cls = getattr(from_module, class_name)
        mylogger.end_progress(f"Got class {class_name} from module {from_module}.", key='get_class')
        return cls

def create_entity_from_dataclass(dataclassobj, from_module, name_ext=''):
    entityclass_name = f'{dataclassobj.__class__.__name__}{name_ext}Entity'
    entityclass = get_class(entityclass_name, from_module)
    fields = dataclasses.fields(dataclassobj)
    attrs = {}
    for field in fields:
        if field.init:
            attrs[field.name] = getattr(dataclassobj, field.name)
    entity = entityclass(**attrs)
    return entity

