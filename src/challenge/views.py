from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import CharField, Count, Max, Min, Value
from django.db.models.functions import Coalesce, Concat, NullIf, TruncDate
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.views import View

from challenge.models import (
    Challenge,
    ChallengeMessageBatchReview,
    ChallengeParticipant,
    ChallengeTrainingLabel,
    TelegramMessage,
)


def can_view_challenge(user):
    return user.is_authenticated and user.is_staff


def get_participant_annotations():
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
    return participant_name_expr, participant_key_expr


@method_decorator(user_passes_test(can_view_challenge, login_url="passlogin"), name="dispatch")
class ChallengeMessagesView(View):
    template_name = "challenge/messages.html"
    paginate_by = 100

    def get(self, request):
        participant_name_expr, participant_key_expr = get_participant_annotations()

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


@method_decorator(user_passes_test(can_view_challenge, login_url="passlogin"), name="dispatch")
class ChallengeMessageMarkupView(View):
    template_name = "challenge/messages_markup.html"

    def dispatch(self, request, *args, **kwargs):
        self.challenge = self.get_challenge(request)
        return super().dispatch(request, *args, **kwargs)

    def get_challenge(self, request):
        challenge_id = request.GET.get("challenge") or request.POST.get("challenge")
        if challenge_id:
            return Challenge.objects.get(pk=challenge_id)

        today = timezone.localdate()
        return (
            Challenge.objects.filter(start_date__lte=today, end_date__gte=today)
            .order_by("-start_date", "-id")
            .first()
            or Challenge.objects.order_by("-start_date", "-id").first()
        )

    def get(self, request):
        if self.challenge is None:
            raise Http404("Нет настроенного челленджа.")
        context = self.get_markup_context(request)
        return render(request, self.template_name, context)

    def post(self, request):
        if self.challenge is None:
            raise Http404("Нет настроенного челленджа.")

        context = self.get_markup_context(request)
        current_batch = context["current_batch"]
        if current_batch is None:
            return redirect(self.base_markup_url())

        action = request.POST.get("action")
        if action == "add_label":
            return self.handle_add_label(request, context)
        if action == "finish_batch":
            return self.handle_finish_batch(request, current_batch, context)
        if action == "mark_flood":
            return self.handle_mark_flood(request, current_batch)
        return render(request, self.template_name, context)

    def handle_add_label(self, request, context):
        current_batch = context["current_batch"]
        training_date = parse_date(request.POST.get("training_date", ""))
        decision = request.POST.get("decision", "").strip()
        training_type = request.POST.get("training_type", "").strip()
        comment = request.POST.get("comment", "").strip()

        if not training_date:
            context["form_error"] = "Нужно указать дату тренировки."
            context["form_data"] = request.POST
            return render(request, self.template_name, context)

        if decision not in dict(ChallengeTrainingLabel.Decision.choices):
            context["form_error"] = "Нужно выбрать статус тренировки."
            context["form_data"] = request.POST
            return render(request, self.template_name, context)

        if training_type not in dict(ChallengeTrainingLabel.TrainingType.choices):
            context["form_error"] = "Нужно выбрать тип тренировки."
            context["form_data"] = request.POST
            return render(request, self.template_name, context)

        label = ChallengeTrainingLabel.objects.filter(
            challenge=self.challenge,
            participant=current_batch["participant"],
            training_date=training_date,
        ).first()
        if label is None:
            label = ChallengeTrainingLabel(
                challenge=self.challenge,
                participant=current_batch["participant"],
                training_date=training_date,
            )
        label.decision = decision
        label.training_type = training_type
        label.comment = comment
        label.reviewed_by = request.user
        label.reviewed_at = timezone.now()

        try:
            label.save()
        except ValidationError as exc:
            context["form_error"] = "; ".join(
                [
                    item
                    for messages in exc.message_dict.values()
                    for item in messages
                ]
            )
            context["form_data"] = request.POST
            return render(request, self.template_name, context)

        batch_messages = context["batch_messages"]
        label.source_messages.add(*batch_messages)

        return redirect(self.batch_url(current_batch))

    def handle_finish_batch(self, request, current_batch, context):
        if not context["batch_labels"]:
            context["form_error"] = "Сначала добавьте хотя бы одну тренировку или отметьте флуд."
            context["form_data"] = request.POST
            return render(request, self.template_name, context)

        ChallengeMessageBatchReview.objects.update_or_create(
            challenge=self.challenge,
            participant=current_batch["participant"],
            message_day=current_batch["message_day"],
            defaults={
                "resolution": ChallengeMessageBatchReview.Resolution.LABELED,
                "reviewed_by": request.user,
                "reviewed_at": timezone.now(),
                "comment": request.POST.get("comment", "").strip(),
            },
        )
        return redirect(self.base_markup_url())

    def handle_mark_flood(self, request, current_batch):
        ChallengeMessageBatchReview.objects.update_or_create(
            challenge=self.challenge,
            participant=current_batch["participant"],
            message_day=current_batch["message_day"],
            defaults={
                "resolution": ChallengeMessageBatchReview.Resolution.FLOOD,
                "reviewed_by": request.user,
                "reviewed_at": timezone.now(),
                "comment": request.POST.get("comment", "").strip(),
            },
        )
        return redirect(self.base_markup_url())

    def get_markup_context(self, request):
        batches = self.get_batches()
        current_batch = self.get_current_batch(request, batches)
        batch_messages = self.get_batch_messages(current_batch) if current_batch else []
        batch_labels = self.get_batch_labels(batch_messages) if batch_messages else []

        return {
            "challenge": self.challenge,
            "today": timezone.localdate(),
            "batches": batches[:50],
            "current_batch": current_batch,
            "batch_messages": batch_messages,
            "batch_labels": batch_labels,
            "current_review": current_batch["review"] if current_batch else None,
            "decision_choices": ChallengeTrainingLabel.Decision.choices,
            "training_type_choices": ChallengeTrainingLabel.TrainingType.choices,
            "form_data": request.POST if request.method == "POST" else {},
        }

    def get_batches(self):
        participant_name_expr, participant_key_expr = get_participant_annotations()
        batch_queryset = (
            TelegramMessage.objects.annotate(
                participant_name=participant_name_expr,
                participant_key=participant_key_expr,
                message_day=TruncDate("sent_at"),
            )
            .values("participant_key", "participant_name", "message_day")
            .annotate(
                message_count=Count("id"),
                first_sent_at=Min("sent_at"),
                last_sent_at=Max("sent_at"),
            )
            .order_by("message_day", "participant_name", "participant_key")
        )

        participants = list(
            ChallengeParticipant.objects.filter(challenge=self.challenge).order_by(
                "display_name", "id"
            )
        )
        participants_by_id = {
            participant.telegram_user_id: participant
            for participant in participants
            if participant.telegram_user_id
        }
        name_counts = {}
        for participant in participants:
            name_counts[participant.display_name] = (
                name_counts.get(participant.display_name, 0) + 1
            )
        participants_by_name = {
            participant.display_name: participant
            for participant in participants
            if name_counts.get(participant.display_name) == 1
        }

        reviews = ChallengeMessageBatchReview.objects.filter(challenge=self.challenge)
        review_map = {
            (review.participant_id, review.message_day): review
            for review in reviews.select_related("reviewed_by", "participant")
        }

        batches = []
        for row in batch_queryset:
            participant = participants_by_id.get(row["participant_key"]) or participants_by_name.get(
                row["participant_name"]
            )
            if participant is None:
                continue

            review = review_map.get((participant.id, row["message_day"]))
            batches.append(
                {
                    "participant": participant,
                    "participant_key": row["participant_key"],
                    "participant_name": row["participant_name"],
                    "message_day": row["message_day"],
                    "message_count": row["message_count"],
                    "first_sent_at": row["first_sent_at"],
                    "last_sent_at": row["last_sent_at"],
                    "review": review,
                    "is_reviewed": review is not None,
                }
            )

        batches.sort(
            key=lambda batch: (
                batch["is_reviewed"],
                batch["message_day"],
                batch["participant_name"],
                batch["participant"].id,
            )
        )
        return batches

    def get_current_batch(self, request, batches):
        participant_id = request.GET.get("participant") or request.POST.get("participant")
        message_day = parse_date(
            request.GET.get("message_day", "") or request.POST.get("message_day", "")
        )
        if participant_id and message_day:
            for batch in batches:
                if (
                    str(batch["participant"].id) == str(participant_id)
                    and batch["message_day"] == message_day
                ):
                    return batch

        for batch in batches:
            if not batch["is_reviewed"]:
                return batch
        return batches[0] if batches else None

    def get_batch_messages(self, batch):
        if batch is None:
            return []
        participant_name_expr, participant_key_expr = get_participant_annotations()
        return list(
            TelegramMessage.objects.select_related("chat")
            .annotate(
                participant_name=participant_name_expr,
                participant_key=participant_key_expr,
            )
            .filter(
                participant_key=batch["participant_key"],
                sent_at__date=batch["message_day"],
            )
            .order_by("sent_at", "telegram_id")
        )

    def get_batch_labels(self, batch_messages):
        message_ids = [message.id for message in batch_messages]
        if not message_ids:
            return []
        return list(
            ChallengeTrainingLabel.objects.filter(source_messages__in=message_ids)
            .select_related("reviewed_by")
            .distinct()
            .order_by("training_date", "id")
        )

    def base_markup_url(self):
        url = reverse("challenge_messages_markup")
        if self.challenge:
            return f"{url}?challenge={self.challenge.id}"
        return url

    def batch_url(self, batch):
        return (
            f"{self.base_markup_url()}&participant={batch['participant'].id}"
            f"&message_day={batch['message_day'].isoformat()}"
        )
