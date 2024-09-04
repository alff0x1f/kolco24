from markdown import markdown
from django.db import models


class NewsPost(models.Model):
    """Model for a news post"""

    title = models.CharField("Заголовок новости", max_length=255)
    publication_date = models.DateTimeField("Дата публикации", auto_now_add=True)

    # Main content of the news post
    content = models.TextField("Текст новости", help_text="Use Markdown format")
    content_html = models.TextField(
        "Текст новости (HTML)", editable=False, help_text="Rendered HTML content"
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
        """Return a string representation of the news post"""
        return self.title

    class Meta:
        """Meta options for the model"""

        ordering = ["-publication_date"]
        verbose_name = "Новость"
        verbose_name_plural = "Новости"

    def save(self, *args, **kwargs):
        """Render the markdown content to HTML"""
        self.content_html = markdown(self.content)
        super().save(*args, **kwargs)
