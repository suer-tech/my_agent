"""The specialist and Iva load the same maintained, deployable skill sources."""
from pathlib import Path

SKILLS=Path(__file__).resolve().parent/'skills'

def policy(workflow='search'):
    name='tender-history' if workflow=='history' else 'tender-search'
    return (SKILLS/name/'SKILL.md').read_text(encoding='utf-8')
