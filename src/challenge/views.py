from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import CharField, Count, Value
from django.db.models.functions import Coalesce, Concat, NullIf
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.views import View

from challenge.models import TelegramMessage


def can_view_challenge(user):
    return user.is_authenticated and user.is_staff


@method_decorator(user_passes_test(can_view_challenge, login_url="passlogin"), name="dispatch")
class ChallengeMessagesView(View):
    template_name = "challenge/messages.html"
    paginate_by = 100

    def get(self, request):
        participant_name_expr = Coalesce(
            NullIf("sender_name", Value("")),
            NullIf("actor_name", Value("")),
            Value("System"),
        )
        participant_key_expr = Coalesce(
            NullIf("sender_id", Value("")),
            NullIf("actor_id", Value("")),
            Concat(Value("name:"), participant_name_expr, output_field=CharField()),
        )

        base_queryset = TelegramMessage.objects.select_related("chat").annotate(
            participant_name=participant_name_expr,
            participant_key=participant_key_expr,
        )

        date_from = parse_date(request.GET.get("date_from", ""))
        date_to = parse_date(request.GET.get("date_to", ""))
        selected_user = request.GET.get("user", "").strip()

        filtered_queryset = base_queryset
        if date_from:
            filtered_queryset = filtered_queryset.filter(sent_at__date__gte=date_from)
        if date_to:
            filtered_queryset = filtered_queryset.filter(sent_at__date__lte=date_to)
        if selected_user:
            filtered_queryset = filtered_queryset.filter(participant_key=selected_user)

        users = list(
            base_queryset.values("participant_key", "participant_name")
            .annotate(message_count=Count("id"))
            .order_by("participant_name", "participant_key")
        )

        total_messages = filtered_queryset.count()
        paginator = Paginator(
            filtered_queryset.order_by("-sent_at", "-telegram_id"),
            self.paginate_by,
        )
        page_obj = paginator.get_page(request.GET.get("page"))

        query_params = request.GET.copy()
        query_params.pop("page", None)
        page_query = query_params.urlencode()

        context = {
            "messages_page": page_obj,
            "users": users,
            "selected_user": selected_user,
            "date_from": request.GET.get("date_from", ""),
            "date_to": request.GET.get("date_to", ""),
            "total_messages": total_messages,
            "total_users": len(users),
            "page_query": page_query,
        }
        return render(request, self.template_name, context)
