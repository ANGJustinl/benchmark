"""Task metadata only. No hidden tests or reference source exposed to model workers."""
from .catalog import TASKS as LEGACY_TASKS
from .poly_catalog import TASKS as POLY_TASKS, metadata as poly_metadata
ALL_TASKS=LEGACY_TASKS+POLY_TASKS

def metadata(task):
    if task in POLY_TASKS:return poly_metadata(task)
    if task not in LEGACY_TASKS:raise ValueError('Unknown task')
    return {'task':task,'family':task,'language':'python','level':{'config':1,'records':2,'pricing':2,'delivery':2,'inventory':3,'reports':2}[task],
            'track':'legacy-python','difficulty_status':'provisional_unmeasured','persistent':False}

def select_tasks(suite='poly',languages=None,levels=None):
    values={'legacy':LEGACY_TASKS,'poly':POLY_TASKS,'all':ALL_TASKS}
    if suite not in values:raise ValueError('Unknown suite')
    result=tuple(t for t in values[suite] if (not languages or metadata(t)['language'] in languages)
                 and (not levels or metadata(t)['level'] in levels))
    if not result:raise ValueError('Task selection is empty')
    return result
