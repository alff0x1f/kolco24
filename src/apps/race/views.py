from django.db.models import Count, OuterRef, Subquery
from django.http import Http404
from django.shortcuts import render
from django.views import View

from website.forms import NewsPostForm
from website.models import NewsPost, Race, Team
from website.models.race import Category, RegStatus
from website.views.views_ import is_race_admin


class RacePageView(View):
    @staticmethod
    def build_context(race, user=None):
        categories = (
            Category.active_objects.filter(race=race)
            .order_by("order", "id")
            .annotate(
                team_count=Subquery(
                    Team.objects.filter(
                        category2=OuterRef("id"),
                        paid_people__gt=0,
                    )
                    .values("category2")
                    .annotate(count=Count("id"))
                    .values("count")[:1]
                )
            )
        )
        news_qs = NewsPost.objects.filter(race=race)
        context = {
            "race": race,
            "categories": categories,
            "links": race.links.order_by("-id"),
            "news_list": list(news_qs.order_by("-publication_date")[:10]),
            "news_count": news_qs.count(),
            "reg_open": race.reg_status == RegStatus.OPEN,
            "reg_upcoming": race.reg_status == RegStatus.UPCOMING,
            "race_team_count": race.team_count(),
            "race_people_count": race.people_count(),
        }
        if user is not None and is_race_admin(user, race):
            context["post_form"] = NewsPostForm()
        return context

    def get(self, request, race_slug):
        try:
            race = Race.objects.get(slug=race_slug)
        except Race.DoesNotExist:
            raise Http404
        context = self.build_context(race, request.user)
        return render(request, "race/race_page.html", context)
