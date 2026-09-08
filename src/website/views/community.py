from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from website.models import NewsPost, PublicationKind, Race
from website.models.race import RegStatus


def visible_publications():
    """Public publication feed, including scheduled items only after release."""
    return (
        NewsPost.objects.filter(
            is_published=True,
            publication_date__lte=timezone.now(),
        )
        .select_related("race")
        .order_by("-publication_date", "-pk")
    )


class HomeView(View):
    template_name = "website/home.html"
    publication_paginate_by = 9

    def get(self, request):
        today = timezone.localdate()
        featured_race = (
            Race.objects.filter(
                is_published=True,
                reg_status__in=(RegStatus.OPEN, RegStatus.UPCOMING),
                date_end__gte=today,
            )
            .order_by("date", "date_end", "pk")
            .first()
        )

        upcoming_races = Race.objects.filter(
            is_published=True,
            date_end__gte=today,
        ).order_by("date", "pk")
        if featured_race is not None:
            upcoming_races = upcoming_races.exclude(pk=featured_race.pk)

        page_obj = Paginator(
            visible_publications(), self.publication_paginate_by
        ).get_page(request.GET.get("page"))
        context = {
            "featured_race": featured_race,
            "page_obj": page_obj,
            "publications": page_obj.object_list,
            "upcoming_races": upcoming_races[:3],
        }
        return render(request, self.template_name, context)


class ArticleListView(View):
    template_name = "website/publication_list.html"
    paginate_by = 9

    def get(self, request):
        publications = visible_publications().filter(kind=PublicationKind.ARTICLE)
        page_obj = Paginator(publications, self.paginate_by).get_page(
            request.GET.get("page")
        )
        return render(
            request,
            self.template_name,
            {
                "page_obj": page_obj,
                "publications": page_obj.object_list,
                "section_tab": "articles",
                "catalog_title": "Статьи",
                "catalog_description": (
                    "Практические статьи о подготовке, навигации "
                    "и туристских соревнованиях."
                ),
            },
        )


class PublicationDetailView(View):
    template_name = "website/publication_detail.html"

    def get(self, request, pk):
        publication = get_object_or_404(visible_publications(), pk=pk)
        return render(
            request,
            self.template_name,
            {"publication": publication},
        )


class RaceListView(View):
    template_name = "website/race_list.html"

    def get(self, request):
        today = timezone.localdate()
        races = Race.objects.filter(is_published=True)
        context = {
            "current_races": races.filter(
                date__lte=today,
                date_end__gte=today,
            ).order_by("date", "pk"),
            "future_races": races.filter(date__gt=today).order_by("date", "pk"),
            "past_races": races.filter(date_end__lt=today).order_by("-date", "pk"),
        }
        return render(request, self.template_name, context)
