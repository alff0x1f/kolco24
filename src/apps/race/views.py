from django.db.models import Count, OuterRef, Subquery
from django.http import Http404
from django.shortcuts import render
from django.views import View

from website.forms import NewsPostForm
from website.models import NewsPost, Race, Team
from website.models.race import Category, RegStatus
from website.views.views_ import is_race_admin


class RacePageView(View):
    def get(self, request, race_slug):
        try:
            race = Race.objects.get(slug=race_slug)
        except Race.DoesNotExist:
            raise Http404
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
        context = {
            "race": race,
            "categories": categories,
            "links": race.links.order_by("-id"),
            "news_list": NewsPost.objects.filter(race=race)[:10],
            "reg_open": race.reg_status == RegStatus.OPEN,
            "race_team_count": race.team_count(),
            "race_people_count": race.people_count(),
        }
        if is_race_admin(request.user, race):
            context["post_form"] = NewsPostForm()
        return render(request, "race/race_page.html", context)
