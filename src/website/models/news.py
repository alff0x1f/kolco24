import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit

import nh3
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from markdown import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

# Tags produced by Python-Markdown (with extra) that are safe to render
_MD_ALLOWED_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "caption",
        "cite",
        "code",
        "col",
        "colgroup",
        "dd",
        "del",
        "details",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "iframe",
        "img",
        "ins",
        "kbd",
        "li",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "samp",
        "section",
        "small",
        "span",
        "strong",
        "sub",
        "summary",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "time",
        "tr",
        "tt",
        "ul",
        "var",
    }
)

_MD_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "abbr": {"title"},
    "img": {"src", "alt", "title", "width", "height"},
    "iframe": {"src"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align", "scope"},
    "ol": {"start", "type"},
    "li": {"value"},
    "col": {"span"},
    "colgroup": {"span"},
    "code": {"class"},
    "span": {"class"},
    "div": {"class"},
    "pre": {"class"},
    "h1": {"id"},
    "h2": {"id"},
    "h3": {"id"},
    "h4": {"id"},
    "h5": {"id"},
    "h6": {"id"},
}

_FEED_ALLOWED_TAGS = {
    "a",
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "img",
    "iframe",
    "s",
    "del",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}

_FEED_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "iframe": {"src"},
}

_VK_VIDEO_HOST_RE = re.compile(r"(?:www\.|m\.)?(?:vkvideo\.ru|vk\.com|vk\.ru)")
_VK_VIDEO_PAGE_RE = re.compile(r"/(?:video|live)(-?\d+)_(\d+)")
# Quotes inside attribute values are always escaped in nh3 output, so this
# can only match a real iframe tag.
_IFRAME_SRC_RE = re.compile(r'<iframe src="([^"]*)"')
# Feed headings carry no attributes, so the cleaned tags are always bare.
_FEED_HEADING_RE = re.compile(r"<(/?)h[1-6]>")
_IFRAME_ATTRIBUTES = {
    "allow": "autoplay; encrypted-media; fullscreen; picture-in-picture; "
    "screen-wake-lock;",
    "allowfullscreen": "",
    "loading": "lazy",
    "referrerpolicy": "strict-origin-when-cross-origin",
}


def _vk_video_params(url):
    """Return (oid, id, hash, hd) for a VK Video player or page URL, else None."""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or parts.username or parts.port:
            return None
    except ValueError:
        return None
    if not _VK_VIDEO_HOST_RE.fullmatch(parts.hostname or ""):
        return None
    if parts.path == "/video_ext.php":
        query = parse_qs(parts.query)
        oid = query.get("oid", [""])[0]
        video_id = query.get("id", [""])[0]
    elif page := _VK_VIDEO_PAGE_RE.fullmatch(parts.path):
        query = {}
        oid, video_id = page.groups()
    else:
        return None
    video_hash = query.get("hash", [""])[0]
    hd = query.get("hd", [""])[0]
    if not re.fullmatch(r"-?\d{1,20}", oid) or not re.fullmatch(r"\d{1,20}", video_id):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{0,64}", video_hash):
        return None
    return oid, video_id, video_hash, hd if re.fullmatch(r"[1-4]", hd) else ""


def _vk_player_url(url):
    params = _vk_video_params(url)
    if params is None:
        return None
    oid, video_id, video_hash, hd = params
    query = {"oid": oid, "id": video_id, "hash": video_hash, "hd": hd}
    return "https://vkvideo.ru/video_ext.php?" + urlencode(
        {key: value for key, value in query.items() if value}
    )


def _iframe_attribute_filter(tag, attribute, value):
    if tag == "iframe" and attribute == "src":
        return _vk_player_url(value)
    return value


def _clean_html(html, tags, attributes):
    """Sanitize HTML; an iframe keeps only a canonical VK Video player src."""
    return nh3.clean(
        html,
        tags=tags,
        attributes=attributes,
        attribute_filter=_iframe_attribute_filter,
        set_tag_attribute_values={"iframe": _IFRAME_ATTRIBUTES},
    )


def _clean_feed_html(html):
    """Sanitize a feed preview, ranking every heading below the card title."""
    html = _clean_html(html, _FEED_ALLOWED_TAGS, _FEED_ALLOWED_ATTRIBUTES)
    return _FEED_HEADING_RE.sub(r"<\1h4>", html)


def _iframe_srcs(html):
    return _IFRAME_SRC_RE.findall(html)


def _normalized_text(html):
    html = re.sub(
        r"<br\b[^>]*>|</(?:p|div|li|h[1-6]|blockquote|pre)>",
        " ",
        html,
        flags=re.IGNORECASE,
    )
    return " ".join(unescape(strip_tags(html)).split())


class _VideoLinkTreeprocessor(Treeprocessor):
    """Turn a paragraph holding nothing but a VK Video link into a player."""

    def run(self, root):
        for paragraph in root.iter("p"):
            children = list(paragraph)
            if not children:
                url = paragraph.text or ""
            elif (
                len(children) == 1
                and children[0].tag == "a"
                and not (paragraph.text or "").strip()
                and not (children[0].tail or "").strip()
                and not list(children[0])
                and (children[0].text or "").strip() == children[0].get("href")
            ):
                url = children[0].get("href")
            else:
                continue
            src = _vk_player_url(url)
            if src:
                paragraph.clear()
                paragraph.tag = "iframe"
                paragraph.set("src", src)


class _VideoLinkExtension(Extension):
    def extendMarkdown(self, md):
        # After inline patterns (priority 20) have turned autolinks into <a>.
        md.treeprocessors.register(_VideoLinkTreeprocessor(md), "vk_video", 5)


def _render_markdown(text):
    raw_html = markdown(str(text), extensions=["extra", _VideoLinkExtension()])
    return _clean_html(raw_html, _MD_ALLOWED_TAGS, _MD_ALLOWED_ATTRIBUTES)


class PublicationKind(models.TextChoices):
    NEWS = "news", "Новость"
    ARTICLE = "article", "Статья"


class NewsPostQuerySet(models.QuerySet):
    def visible(self):
        """Released posts belonging to public races, in stable feed order."""
        return (
            self.filter(
                models.Q(race__isnull=True) | models.Q(race__is_published=True),
                is_published=True,
                publication_date__lte=timezone.now(),
            )
            .select_related("race")
            .order_by("-publication_date", "-pk")
        )


class NewsPost(models.Model):
    """A news item or evergreen article shown in the site publication feed."""

    objects = NewsPostQuerySet.as_manager()

    title = models.CharField("Заголовок", max_length=255)
    summary = models.TextField(
        "Анонс",
        blank=True,
        help_text=(
            "Короткий текст для карточки. Если пусто, используется начало статьи."
        ),
    )
    kind = models.CharField(
        "Тип",
        max_length=16,
        choices=PublicationKind.choices,
        default=PublicationKind.NEWS,
        db_index=True,
    )
    is_published = models.BooleanField("Опубликована", default=True, db_index=True)
    publication_date = models.DateTimeField(
        "Дата публикации", default=timezone.now, db_index=True
    )

    # Main content of the news post
    content = models.TextField("Текст", help_text="Use Markdown format")
    content_html = models.TextField(
        "Текст (HTML)", editable=False, help_text="Rendered HTML content"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Featured image for the post, it can be optional
    image = models.ImageField(upload_to="blog_images/", blank=True, null=True)

    race = models.ForeignKey(
        "Race",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name="Гонка",
    )

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("publication_detail", kwargs={"pk": self.pk})

    @property
    def is_draft(self):
        return not self.is_published

    @property
    def is_scheduled(self):
        return self.is_published and self.publication_date > timezone.now()

    def _summary(self, limit):
        if self.summary.strip():
            return self.summary.strip()
        return Truncator(unescape(strip_tags(self.content_html))).chars(limit)

    @property
    def card_summary(self):
        """Keep a compact description for metadata and publication cards."""
        return self._summary(220)

    @property
    def feed_summary(self):
        """Give news more room in the feed while keeping article teasers short."""
        return self._summary(600 if self.kind == PublicationKind.NEWS else 220)

    @property
    def feed_summary_html(self):
        """Reuse the preview until its source fields change on this instance."""
        source = (self.summary, self.content_html, self.kind)
        if getattr(self, "_feed_summary_source", None) != source:
            self._feed_summary_html = self._render_feed_summary_html()
            self._feed_summary_source = source
        return self._feed_summary_html

    def _render_feed_summary_html(self):
        """Render safe feed formatting and close tags when shortening the text."""
        html = _clean_feed_html(
            _render_markdown(self.summary.strip())
            if self.summary.strip()
            else self.content_html
        )
        if not self.summary.strip():
            limit = 600 if self.kind == PublicationKind.NEWS else 220
            text = unescape(strip_tags(html))
            if Truncator(text).chars(limit) != text:
                html = Truncator(html).chars(limit, html=True)
        return mark_safe(_clean_feed_html(html))

    @property
    def has_more_content(self):
        """Whether the full publication contains text not shown in its preview."""
        if _iframe_srcs(self.content_html) != _iframe_srcs(self.feed_summary_html):
            return True
        content = _normalized_text(self.content_html)
        summary = _normalized_text(self.feed_summary_html)
        return bool(content) and summary != content

    class Meta:
        """Meta options for the model"""

        ordering = ["-publication_date"]
        verbose_name = "Публикация"
        verbose_name_plural = "Публикации"

    def save(self, *args, **kwargs):
        """Render the markdown content to HTML"""
        self.content_html = _render_markdown(self.content)
        super().save(*args, **kwargs)


class MenuItem(models.Model):
    """Model for a menu item"""

    objects = models.Manager()

    name = models.CharField("Название пункта меню", max_length=100)
    url = models.CharField("URL пункта меню", max_length=255)
    order = models.IntegerField("Порядок", default=0)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["order"]
        verbose_name = "Пункт меню"
        verbose_name_plural = "Пункты меню"


class Page(models.Model):
    """Model for a page"""

    title = models.CharField("Заголовок страницы", max_length=255)
    slug = models.SlugField("URL страницы", unique=True)
    content = models.TextField("Содержимое страницы", help_text="Use Markdown format")
    content_html = models.TextField(
        "Содержимое страницы (HTML)", editable=False, help_text="Rendered HTML content"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ["title"]
        verbose_name = "Страница"
        verbose_name_plural = "Страницы"

    def save(self, *args, **kwargs):
        """Render the markdown content to HTML"""
        self.content_html = _render_markdown(self.content)
        super().save(*args, **kwargs)
