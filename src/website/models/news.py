import re
import secrets
from html import escape, unescape
from urllib.parse import parse_qs, urlencode, urlsplit

import nh3
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from markdown import markdown

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
    "s",
    "del",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
}


def _clean_feed_html(html):
    return nh3.clean(
        html,
        tags=_FEED_ALLOWED_TAGS,
        attributes={
            "a": {"href", "title"},
            "img": {"src", "alt", "title", "width", "height"},
        },
    )


def _normalized_text(html):
    html = re.sub(
        r"<br\b[^>]*>|</(?:p|div|li|h[1-6]|blockquote|pre)>",
        " ",
        html,
        flags=re.IGNORECASE,
    )
    return " ".join(unescape(strip_tags(html)).split())


_VK_HOSTS = r"(?:www\.|m\.)?(?:vkvideo\.ru|vk\.com|vk\.ru)"
_VK_IFRAME_RE = re.compile(
    r"<iframe\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>\s*(?:</iframe>)?",
    re.IGNORECASE,
)
_VK_LINK_RE = re.compile(
    rf"^[ \t]*<?(https?://{_VK_HOSTS}/(?:video|live)(-?\d+)_(\d+)\S*?)>?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_VK_EXT_PATH_RE = re.compile(rf"^https?://{_VK_HOSTS}/video_ext\.php$", re.IGNORECASE)


def _vk_embed_html(oid, video_id, video_hash=""):
    params = {"oid": oid, "id": video_id}
    if video_hash:
        params["hash"] = video_hash
    params["hd"] = "2"
    src = "https://vkvideo.ru/video_ext.php?" + urlencode(params)
    return (
        '<div class="video-embed"><iframe src="'
        + escape(src)
        + '" allow="autoplay; encrypted-media; fullscreen; picture-in-picture;'
        ' screen-wake-lock;" allowfullscreen loading="lazy"'
        ' referrerpolicy="strict-origin-when-cross-origin"></iframe></div>'
    )


def _vk_iframe_params(src):
    src = unescape(src)
    parts = urlsplit(src)
    if not _VK_EXT_PATH_RE.match(f"{parts.scheme}://{parts.netloc}{parts.path}"):
        return None
    query = parse_qs(parts.query)
    oid = query.get("oid", [""])[0]
    video_id = query.get("id", [""])[0]
    video_hash = query.get("hash", [""])[0]
    if not re.fullmatch(r"-?\d+", oid) or not re.fullmatch(r"\d+", video_id):
        return None
    if video_hash and not re.fullmatch(r"[0-9a-fA-F]+", video_hash):
        return None
    return oid, video_id, video_hash


def _extract_video_embeds(text):
    """Swap VK Video iframes and bare links for markers that survive cleaning."""
    nonce = secrets.token_hex(8)
    embeds = {}

    def marker(html):
        key = f"vkvideoembed{nonce}x{len(embeds)}"
        embeds[key] = html
        return f"\n\n{key}\n\n"

    def replace_iframe(match):
        params = _vk_iframe_params(match.group(1))
        return marker(_vk_embed_html(*params)) if params else match.group(0)

    text = _VK_IFRAME_RE.sub(replace_iframe, text)
    text = _VK_LINK_RE.sub(lambda m: marker(_vk_embed_html(m[2], m[3])), text)
    return text, embeds


def _render_markdown(text):
    text, embeds = _extract_video_embeds(str(text))
    raw_html = markdown(text, extensions=["extra"])
    html = nh3.clean(raw_html, tags=_MD_ALLOWED_TAGS, attributes=_MD_ALLOWED_ATTRIBUTES)
    for key, embed in embeds.items():
        html = html.replace(f"<p>{key}</p>", embed)
    return html


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
        if 'class="video-embed"' in self.content_html:
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
