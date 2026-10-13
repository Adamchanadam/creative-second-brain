"""Shared offline reader identity and navigation. No data or catalogue writes."""
from contextlib import contextmanager
from pathlib import Path
import html
import os
import re
import threading
from uuid import uuid4
from urllib.parse import quote

VERSION = 'creative-brain-reader/1'
BRAND_ENGLISH = 'CREATIVE 2ND BRAIN'
BRAND_NAME = '創造型第二大腦'
LOGO_NAME = 'creative-second-brain-nebula-logo-v4.png'
PRODUCT_FOOTER = '<footer class="brain-product-footer" data-brain-footer="creative-second-brain">創造型第二大腦 | Adam Chan</footer>'
CATALOG_PAGE_NAMES = frozenset(("index.html", "knowledge.html", "thinking.html"))
_projection_lock_guard = threading.Lock()
_projection_locks: dict[str, threading.Lock] = {}

def brand_content(logo_href=None):
    """The same identity for guides and readers; the wordmark remains real text."""
    mark = ('<img class="brain-logo" src="'+html.escape(logo_href,quote=True)+'" width="40" height="40" alt="" decoding="async">') if logo_href else ''
    return mark+'<span><small>'+BRAND_ENGLISH+'</small><b>'+BRAND_NAME+'</b></span>'

def guide_brand(document, logo_href):
    return re.sub(r'(<a\b[^>]*class="brand"[^>]*>).*?</a>',lambda m:m.group(1)+brand_content(logo_href)+'</a>',document,count=1,flags=re.S)


def product_footer(document):
    """Add the one product signature without changing source-owned page content."""
    document = re.sub(r'<footer\b[^>]*\bdata-brain-footer="creative-second-brain"[^>]*>.*?</footer>', '', document, flags=re.S)
    if '</body>' in document:
        return document.replace('</body>', PRODUCT_FOOTER + '</body>', 1)
    if '</html>' in document:
        return document.replace('</html>', PRODUCT_FOOTER + '</html>', 1)
    return document + PRODUCT_FOOTER

def theme_css():
    return Path(__file__).with_name('reader_theme.css').read_text(encoding='utf-8')


def apply_visual(document, mode='reader'):
    """One shared offline style/passage, including home and unchanged nebula content."""
    if mode not in ('reader', 'home', 'nebula'):
        raise ValueError('unknown reader visual mode')
    document=re.sub(r'<style id="brain-reader-visual">.*?</style>', '', document, flags=re.S)
    document=re.sub(r'<script id="brain-reader-passage">.*?</script>', '', document, flags=re.S)
    document=re.sub(r' data-brain-visual="[^"]*"', '', document)
    if not re.search(r'<html(?=[ >])', document):
        document='<html>'+document
    document=re.sub(r'<html(?=[ >])', '<html data-brain-visual="'+mode+'"', document, count=1)
    css=Path(__file__).with_name('reader_visual.css').read_text(encoding='utf-8')
    script=Path(__file__).with_name('reader_passage.js').read_text(encoding='utf-8')
    layer='<style id="brain-reader-visual">'+css+'</style><script id="brain-reader-passage">'+script+'</script>'
    if '</head>' in document:
        return document.replace('</head>', layer+'</head>', 1)
    return re.sub(r'<body\b', lambda _:layer+'<body', document, count=1)

def guide_for(vault):
    vault=Path(vault).resolve()
    for candidate in (vault.parent/'index.html',):
        if candidate.is_file(): return candidate
    if vault.parent.name == 'examples' and (vault.parent.parent/'index.html').is_file():
        return vault.parent.parent/'index.html'
    return None

def logo_for(vault):
    vault=Path(vault).resolve()
    guide=guide_for(vault)
    if guide:
        for relative in ('assets/brand/', 'assets/workspace/assets/brand/'):
            candidate=guide.parent/relative/LOGO_NAME
            if candidate.is_file(): return candidate
    return None

def apply_theme(document, page, vault, active=''):
    """Idempotent visual/navigation projection; original content stays intact."""
    page,vault=Path(page).absolute(),Path(vault).absolute()
    def href(target): return html.escape(quote(os.path.relpath(target,page.parent).replace('\\','/'),safe='/'),quote=True)
    guide=guide_for(vault)
    destinations=[('overview','成果總覽',vault/'outcome-pages/index.html'),('knowledge','知識成果',vault/'outcome-pages/knowledge.html'),('thinking','思考紀錄',vault/'outcome-pages/thinking.html')]
    if (vault/'outcome-pages/nebula.html').is_file():
        destinations.append(('nebula','知識星雲',vault/'outcome-pages/nebula.html'))
    if guide: destinations.insert(0,('guide','工作首頁',guide))
    nav=''.join('<a class="brain-nav" href="'+href(target)+'"'+(' aria-current="page"' if key==active else '')+'>'+label+'</a>' for key,label,target in destinations)
    example=vault.parent.name=='examples'
    label='合成示例 · 並非你的正式知識庫' if example else '仰望知識的星空，讓想法相遇。'
    logo=logo_for(vault)
    logo_href=quote(os.path.relpath(logo,page.parent).replace('\\','/'),safe='/') if logo else None
    sidebar='<aside id="brain-site-nav" class="brain-sidebar" aria-label="專案導航"><a class="brain-brand" href="'+href(guide or destinations[0][2])+'">'+brand_content(logo_href)+'</a><nav>'+nav+'</nav><small class="brain-note">'+label+'</small></aside>'
    document=re.sub(r'<style id="brain-reader-theme">.*?</style>','',document,flags=re.S)
    document=re.sub(r'<style id="brain-reader-assets">.*?</style>','',document,flags=re.S)
    document=re.sub(r'<aside id="brain-site-nav".*?</aside>','',document,flags=re.S)
    style='<style id="brain-reader-theme">'+theme_css()+'</style>'
    stardust = logo.parent / 'gold-silver-stardust.png' if logo else None
    if stardust and stardust.is_file():
        stardust_href = quote(os.path.relpath(stardust, page.parent).replace('\\','/'), safe='/')
        assets = '<style id="brain-reader-assets">:root{--brain-stardust:url(\''+stardust_href+'\');}</style>'
    else:
        assets = '<style id="brain-reader-assets">:root{--brain-stardust:none;}</style>'
    if '</head>' in document: document=document.replace('</head>',style+assets+'</head>',1)
    elif re.search(r'<body\b', document): document=re.sub(r'<body\b',lambda _:style+assets+'<body',document,count=1)
    elif '<main' in document: document=document.replace('<main',style+assets+'<body><main',1)
    else: document=style+assets+'<body>'+document
    def body(match):
        tag=match.group(0)
        if 'brain-reader' not in tag:
            if 'class="' in tag: tag=tag.replace('class="','class="brain-reader ',1)
            else: tag=tag[:-1]+' class="brain-reader">'
        return tag+sidebar
    return apply_visual(product_footer(re.sub(r'<body\b[^>]*>',body,document,count=1)))


def _catalogue_document(page: Path, document: str) -> None:
    """Reject an incomplete catalogue before a visual-only projection can hide it."""
    if not document.strip():
        raise ValueError('reader page is empty; keep the existing reader until a complete projection is available')
    if page.name in CATALOG_PAGE_NAMES:
        if not re.search(r'<main\b', document, re.I) or not re.search(r'<script\b[^>]*\bid=["\']catalog-data["\']', document, re.I):
            raise ValueError('catalogue projection is incomplete; keep the existing reader until a complete catalogue is available')


@contextmanager
def catalogue_projection_lock(output_dir):
    """Coordinate catalogue writers and shell refreshes across reader processes.

    This is an operating-system lock, not an exclusive-created sentinel: an
    interrupted writer releases it automatically, while the retained tiny file
    cannot create a second, independent lock race during hand-off.
    """
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.reader-projection.lock'
    with _projection_lock_guard:
        local_lock = _projection_locks.setdefault(str(lock), threading.Lock())
    if not local_lock.acquire(blocking=False):
        raise ValueError('another reader projection is active; keep the existing readers and retry after it finishes')
    handle = None
    try:
        handle = lock.open('a+', encoding='utf-8')
        if lock.stat().st_size == 0:
            handle.write('\0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if handle is not None:
            handle.close()
        local_lock.release()
        raise ValueError('another reader projection is active; keep the existing readers and retry after it finishes') from error
    try:
        handle.seek(0)
        handle.write(str(os.getpid()) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                handle.close()
            finally:
                local_lock.release()


def atomic_reader_write(page, document: str) -> None:
    """Publish a complete reader document in one replacement, never by truncation."""
    page = Path(page)
    pending = page.with_name('.' + page.name + '.' + uuid4().hex + '.reader-pending')
    try:
        with pending.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, page)
    finally:
        if pending.exists():
            pending.unlink()


def refresh_current_reader_visuals(vault):
    """Reproject only current HTML reader envelopes from saved local pages.

    This never reads or changes source notes, citations, receipts, or generated
    content bodies.  It updates the replaceable shared shell on current product
    readers so a visual release reaches existing pages as well as future ones.
    """
    vault = Path(vault).resolve()
    output = vault / 'outcome-pages'
    patterns = ('index.html', 'knowledge.html', 'thinking.html', 'canvases.html',
                'page-[0-9][0-9][0-9].html', '*_outcome.html',
                'thinking/ASK-*.html', 'notes/N-*.html', 'canvases/canvas-*.html')
    pages = sorted({path for pattern in patterns for path in output.glob(pattern) if path.is_file()})
    changed = []
    with catalogue_projection_lock(output):
        for page in pages:
            source = page.read_text(encoding='utf-8')
            _catalogue_document(page, source)
            active = {'index.html': 'overview', 'knowledge.html': 'knowledge', 'thinking.html': 'thinking'}.get(page.name, 'thinking' if page.parts[-2:-1] == ('thinking',) else 'knowledge')
            rendered = apply_theme(source, page, vault, active)
            if rendered != source:
                if page.read_text(encoding='utf-8') != source:
                    raise ValueError('reader page changed during visual projection; existing content was not overwritten')
                atomic_reader_write(page, rendered)
                changed.append(str(page))
    return {'status': 'ready', 'pages_scanned': len(pages), 'visual_pages_reprojected': len(changed)}
