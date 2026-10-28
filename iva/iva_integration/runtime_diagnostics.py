"""Small allowlisted operational events; never log prompts, bodies or credentials."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

ROOT=Path('/opt/iva/data/runtime-diagnostics')
FIELDS={'stage','state','kind','code','count','last_uid','uid','has_more','elapsed_ms','source_id','cycle_id','campaign','found','fresh','matched','url','submitted','error_type'}


def event(component,**fields):
    try:
        ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
        logger=logging.getLogger('iva.operational.'+component)
        if not logger.handlers:
            handler=RotatingFileHandler(ROOT/(component+'.jsonl'),maxBytes=1024*1024,backupCount=3,encoding='utf-8')
            logger.addHandler(handler);logger.setLevel(logging.INFO);logger.propagate=False
        logger.info(json.dumps({'at':time.time(),**{k:v for k,v in fields.items() if k in FIELDS}},ensure_ascii=False))
    except OSError:pass # Diagnostics cannot turn a successful publication into a failed one.
