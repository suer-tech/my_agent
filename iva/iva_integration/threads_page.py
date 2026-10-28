"""Choose one working tab without rejecting a profile that restored extra tabs."""
from urllib.parse import urlsplit


def working_page(context):
    old=getattr(context,'_iva_threads_working_page',None)
    if old is not None and not old.is_closed():return old
    pages=[p for p in context.pages if not p.is_closed() and urlsplit(p.url).hostname in ('threads.com','www.threads.com')]
    if not pages:raise RuntimeError('No server Threads page')
    page=pages[-1];context._iva_threads_working_page=page
    return page
