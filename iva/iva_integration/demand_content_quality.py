"""Pure HTML body selection and conservative empty-content checks.

Integration candidate for demand_sources._extract; this module performs no
fetches, mutations of the supplied soup, or persistence. In particular a page
whose ``article`` elements are documentation menus must not hide the real body.
Length alone is deliberately not a quality decision: a short forum complaint
can be useful evidence, while a longer URL path is not a read document.
"""
import re
from urllib.parse import urlsplit
from bs4.element import CData, NavigableString


_IGNORED = frozenset(('head','script','style','noscript','template','iframe','svg','nav','aside','footer','form'))
_HEADINGS = frozenset(('h1','h2','h3','h4','h5','h6'))
MAX_CONTACT_FRAGMENTS = 4
MAX_CONTACT_FRAGMENT_CHARS = 400
_CONTACT_IGNORED = frozenset(('head','script','style','noscript','template','iframe','svg','canvas','object','embed','form'))
_CONTACT_HIDDEN_CLASSES = frozenset(('hidden','d-none','invisible','sr-only','visually-hidden'))
_CONTACT_HIDE_STYLE = re.compile(
    r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|'
    r'content-visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0*)?)\s*(?:!important\s*)?(?:;|$)', re.I)
_CONTACT_SCOPE = re.compile(r'(?:^|[-_\s])(?:contacts?|contactus|контакты?)(?:$|[-_\s])', re.I)
_PUBLIC_EMAIL = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
    r"(?![A-Za-z0-9-])")


def _clean(value):
    return re.sub(r'\s+', ' ', value).strip()


def _hidden(node):
    style = getattr(node, 'attrs', {}).get('style', '')
    hidden_style = (isinstance(style, str)
                    and re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important\s*)?(?:;|$)', style, re.I))
    site_header = (getattr(node, 'name', None) == 'header'
                   and not any(parent.name in ('article', 'main') or parent.get('role') == 'main'
                               for parent in node.parents))
    return (getattr(node, 'name', None) in _IGNORED or site_header
            or getattr(node, 'attrs', {}).get('role') in ('navigation', 'complementary', 'banner')
            or getattr(node, 'attrs', {}).get('aria-hidden') == 'true'
            or 'hidden' in getattr(node, 'attrs', {}) or bool(hidden_style))


def _strings(node, blocked=frozenset(), omit_links=False):
    for item in node.find_all(string=True):
        if type(item) not in (NavigableString, CData):
            continue  # Comments, doctypes and processing instructions are not visible content.
        parents = list(item.parents)
        if any(_hidden(parent) or id(parent) in blocked for parent in parents):
            continue
        if omit_links and any(parent.name == 'a' or parent.name in _HEADINGS for parent in parents):
            continue
        if item.strip():
            yield str(item)


def _navigation_article(node):
    """Only strong structural evidence; no language/keyword complaint filter."""
    links = node.find_all('a', href=True)
    if len(links) < 3:
        return False
    text = _clean(' '.join(_strings(node)))
    prose = _clean(' '.join(_strings(node, omit_links=True)))
    linked_chars = sum(len(_clean(' '.join(_strings(link)))) for link in links)
    return (bool(text) and linked_chars >= len(text) * .65
            and re.search(r'\w', prose, flags=re.UNICODE) is None)


def insufficient_content(text):
    """Reject empty/URL-only shells, never reject a genuine short statement."""
    text = _clean(text)
    if not text:
        return True
    if re.fullmatch(r'/[^\s<>]+', text):
        return True
    if not any(char.isspace() for char in text):
        try:
            parsed = urlsplit(text)
            if parsed.scheme in ('http','https') and parsed.hostname:
                return True
        except ValueError:
            pass
    return False


def extract_html_body(soup):
    """Return (text, error_category) without accepting navigation as evidence.

    Prefer a real main element. A single substantive article remains a useful
    semantic hint. With many articles, preserve the body (e.g. forum replies)
    while dropping proven navigation articles; do not arbitrarily read the first.
    Publication dates remain the existing extractor's separate responsibility.
    """
    articles = soup.find_all('article')
    mains = soup.find_all('main') + soup.find_all(attrs={'role': 'main'})
    blocked = frozenset(id(node) for node in articles + mains if _navigation_article(node))
    candidates = [node for node in mains if id(node) not in blocked and not _hidden(node)]
    if not candidates:
        candidates = [node for node in articles if id(node) not in blocked and not _hidden(node)]
        if len(candidates) != 1:
            candidates = []
    body = soup.body or soup
    if _navigation_article(body):
        return '', 'insufficient_content'
    chosen = body
    # Empty/skeleton main containers must not mask useful sibling content.
    for node in candidates:
        text = _clean(' '.join(_strings(node, blocked)))
        if not insufficient_content(text):
            chosen = node
            break
    text = _clean(' '.join(_strings(chosen, blocked)))
    return text, 'insufficient_content' if insufficient_content(text) else None


def _contact_css_hidden(soup):
    """Conservative static visibility, not browser rendering or external CSS."""
    hidden = set()
    rules = 0
    for node in soup.find_all('style', limit=16):
        css = re.sub(r'/\*.*?\*/', '', node.get_text()[:32000], flags=re.S)
        for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
            if not _CONTACT_HIDE_STYLE.search(match[2]):
                continue
            for selector in match[1].split(','):
                selector = selector.strip()
                # Bounded ordinary selectors only; no :has(), scripts, dynamic
                # states, or CSS fetching. Conditional hiding is conservative.
                if (not selector or len(selector) > 160 or not re.fullmatch(
                        r'[A-Za-z0-9_#.*\-\s>+~]+', selector)):
                    continue
                rules += 1
                if rules > 32:
                    return hidden
                try:
                    hidden.update(id(item) for item in soup.select(selector))
                except Exception:
                    continue
    return hidden


def _contact_hidden(node, css_hidden):
    attrs = getattr(node, 'attrs', {})
    classes = attrs.get('class', [])
    if isinstance(classes, str):
        classes = classes.split()
    style = attrs.get('style', '')
    return (getattr(node, 'name', None) in _CONTACT_IGNORED or id(node) in css_hidden
            or str(attrs.get('aria-hidden', '')).casefold() == 'true'
            or 'hidden' in attrs or 'inert' in attrs
            or bool(_CONTACT_HIDDEN_CLASSES.intersection(classes))
            or (isinstance(style, str) and bool(_CONTACT_HIDE_STYLE.search(style)))
            or (getattr(node, 'name', None) == 'dialog' and 'open' not in attrs))


def _contact_strings(node, css_hidden):
    for item in node.find_all(string=True):
        if type(item) not in (NavigableString, CData):
            continue
        parents = list(item.parents)
        if any(_contact_hidden(parent, css_hidden) for parent in parents):
            continue
        collapsed = [parent for parent in parents if parent.name == 'details' and not parent.has_attr('open')]
        if any(not any(p.name == 'summary' and p.parent is detail for p in parents) for detail in collapsed):
            continue
        if item.strip():
            yield str(item)


def _email_window(text, match):
    """A literal substring of normalized visible text, with no invented label."""
    if len(text) <= MAX_CONTACT_FRAGMENT_CHARS:
        return text
    start = max(0, match.start() - 100)
    end = min(len(text), start + MAX_CONTACT_FRAGMENT_CHARS)
    if match.end() > end:
        end = match.end(); start = max(0, end - MAX_CONTACT_FRAGMENT_CHARS)
    if start:
        boundary = text.find(' ', start, match.start())
        if boundary != -1:
            start = boundary + 1
    if end < len(text):
        boundary = text.rfind(' ', match.end(), end)
        if boundary != -1:
            end = boundary
    return text[start:end].strip()


def extract_contact_fragments(soup):
    """Up to four literal public contact fragments, each at most 400 chars.

    This separate opt-in reader intentionally permits visible site headers and
    footers that body extraction drops. It does not infer company ownership,
    consent, aliases, publication dates, or demand. A visible mail link may supply
    its literal query-free ``mailto:address`` target, never cc/bcc/subject/query
    values or a decoded/constructed address. The supplied soup is not modified.
    """
    css_hidden = _contact_css_hidden(soup)
    sources = []; scopes = set(); links = 0
    # Document order gives a deterministic bounded selection. Hidden siblings
    # cannot enter a contact fragment through a parent's get_text().
    for node in soup.find_all(True):
        attrs = node.attrs
        names = str(attrs.get('id', '')) + ' ' + ' '.join(attrs.get('class', []))
        scope = node.name in ('header', 'footer', 'address') or str(attrs.get('itemprop', '')).casefold() == 'email' or bool(_CONTACT_SCOPE.search(names))
        if scope and len(scopes) < 32 and not any(id(parent) in scopes for parent in node.parents):
            scopes.add(id(node))
            sources.append(_clean(' '.join(_contact_strings(node, css_hidden))))
        if node.name != 'a' or any(_contact_hidden(parent, css_hidden) for parent in (node, *node.parents)):
            continue
        visible = _clean(' '.join(_contact_strings(node, css_hidden)))
        href = attrs.get('href', '')
        if not visible or not isinstance(href, str) or not href.lower().startswith('mailto:'):
            continue
        if links >= 128:
            continue
        links += 1
        if _PUBLIC_EMAIL.search(visible):
            sources.append(visible)
        address = href[7:]
        if ('?' not in address and '#' not in address and '%' not in address
                and _PUBLIC_EMAIL.fullmatch(address)):
            sources.append(href)  # Original public link target, not synthetic text.
    fragments, seen_emails = [], set()
    for source in sources:
        for match in _PUBLIC_EMAIL.finditer(source):
            email = match[0]
            local = email.split('@', 1)[0]
            if len(email) > 254 or local.startswith('.') or local.endswith('.') or '..' in local:
                continue
            if email.casefold() in seen_emails:
                continue
            fragment = _email_window(source, match)
            if not fragment or email not in fragment:
                continue
            seen_emails.add(email.casefold())
            if fragment not in fragments:
                fragments.append(fragment)
            if len(fragments) == MAX_CONTACT_FRAGMENTS:
                return fragments
    return fragments
