import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import BaseUserManager
from django.db import IntegrityError, models, transaction
from django.utils import timezone


def _normalize_email(email):
    return BaseUserManager().normalize_email(email or "").lower()


class EmailVerification(models.Model):
    """One row backs both the 6-digit code and the magic link for a login attempt.

    The raw code is never stored — only its hash. The magic link carries no token
    column: the URL embeds ``TimestampSigner().sign(str(pk))`` and this row's
    ``expires_at``/``consumed_at`` enforce lifetime and single use.
    """

    CODE_TTL = timedelta(minutes=15)
    MAX_ATTEMPTS = 5
    RESEND_COOLDOWN = timedelta(seconds=60)

    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=128)
    purpose = models.CharField(max_length=32, default="login")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "purpose", "consumed_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["email", "purpose"],
                condition=models.Q(consumed_at__isnull=True),
                name="unique_active_email_verification",
            )
        ]

    def __str__(self):
        return f"EmailVerification({self.email}, {self.purpose})"

    @staticmethod
    def generate_code():
        return f"{secrets.randbelow(1_000_000):06d}"

    @classmethod
    def create_for(cls, email, purpose="login"):
        """Issue a verification for ``email``.

        Returns ``(obj, raw_code)``. To curb bombing, if an alive row for the same
        email/purpose was created less than ``RESEND_COOLDOWN`` ago, no new code is
        issued and ``(existing, None)`` is returned (the raw code is unrecoverable
        since only the hash is stored).

        Concurrent callers are serialized: ``select_for_update`` locks any existing
        alive rows; a unique partial index on ``(email, purpose) WHERE consumed_at IS
        NULL`` prevents two simultaneous inserts from both creating live rows.
        """
        email = _normalize_email(email)
        now = timezone.now()

        with transaction.atomic():
            # Lock ALL unconsumed rows (alive or expired) to serialize concurrent
            # callers and prevent INSERT conflicts against the partial unique index on
            # (email, purpose) WHERE consumed_at IS NULL. An expired row still holds
            # that index slot until it is explicitly consumed.
            existing_rows = list(
                cls.objects.filter(
                    email=email,
                    purpose=purpose,
                    consumed_at__isnull=True,
                )
                .select_for_update()
                .order_by("-created_at")
            )

            if existing_rows:
                newest = existing_rows[0]
                if newest.is_alive and newest.created_at > now - cls.RESEND_COOLDOWN:
                    return newest, None
                # Past cooldown or attempts exhausted: revoke all and fall through.
                cls.objects.filter(pk__in=[r.pk for r in existing_rows]).update(
                    consumed_at=now
                )

            raw_code = cls.generate_code()
            try:
                with transaction.atomic():  # savepoint for unique-constraint race
                    obj = cls.objects.create(
                        email=email,
                        code_hash=make_password(raw_code),
                        purpose=purpose,
                        expires_at=now + cls.CODE_TTL,
                    )
            except IntegrityError:
                # A concurrent request (also seeing no alive rows) won the INSERT race.
                # Return its row without a raw code so the caller skips sending email.
                winner = (
                    cls.objects.filter(
                        email=email,
                        purpose=purpose,
                        consumed_at__isnull=True,
                        expires_at__gt=now,
                    )
                    .order_by("-created_at")
                    .first()
                )
                if winner is not None:
                    return winner, None
                # Extremely unlikely: winner was consumed before we read it.
                return cls.create_for(email, purpose)
        return obj, raw_code

    @property
    def is_alive(self):
        return (
            self.consumed_at is None
            and timezone.now() < self.expires_at
            and self.attempts < self.MAX_ATTEMPTS
        )

    def verify_code(self, raw_code):
        """Check ``raw_code`` against this row, counting the attempt.

        Returns ``False`` (without consuming) when the row is dead or the code is
        wrong; a wrong code increments ``attempts``.
        """
        if not self.is_alive:
            return False
        if check_password(raw_code, self.code_hash):
            return True
        self.attempts = models.F("attempts") + 1
        self.save(update_fields=["attempts"])
        self.refresh_from_db(fields=["attempts"])
        return False

    def mark_consumed(self):
        if self.consumed_at is None:
            self.consumed_at = timezone.now()
            self.save(update_fields=["consumed_at"])

    def mark_consumed_atomic(self):
        """Atomically mark this row consumed. Returns True only if this call won."""
        now = timezone.now()
        updated = EmailVerification.objects.filter(
            pk=self.pk,
            consumed_at__isnull=True,
            expires_at__gt=now,
            attempts__lt=self.MAX_ATTEMPTS,
        ).update(consumed_at=now)
        return updated == 1

    def atomic_consume_if_valid(self, raw_code):
        """Verify ``raw_code`` and mark this row consumed in one atomic DB operation.

        Returns ``True`` only if this call was the one that consumed the row.
        A wrong code atomically increments ``attempts``. Concurrent callers with
        the same correct code all pass ``check_password`` but only one wins the
        ``UPDATE … WHERE consumed_at IS NULL`` race — the others get ``False``.
        """
        if not self.is_alive:
            return False
        if not check_password(raw_code, self.code_hash):
            EmailVerification.objects.filter(
                pk=self.pk, attempts__lt=self.MAX_ATTEMPTS
            ).update(attempts=models.F("attempts") + 1)
            return False
        now = timezone.now()
        updated = EmailVerification.objects.filter(
            pk=self.pk,
            consumed_at__isnull=True,
            expires_at__gt=now,
            attempts__lt=self.MAX_ATTEMPTS,
        ).update(consumed_at=now)
        return updated == 1
