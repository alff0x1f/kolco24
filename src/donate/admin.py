from django.contrib import admin

from donate.models import ClubMember, DonateRequest, DonationPeriod, MemberDonation


@admin.register(DonateRequest)
class DonateRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "sender_name", "comment", "payment", "created")
    list_filter = ("comment", "created")
    search_fields = ("sender_name", "payment__order_id")


class MemberDonationInline(admin.TabularInline):
    model = MemberDonation
    extra = 0
    fields = ("period", "is_paid", "amount", "note")
    ordering = ("-period__date",)


@admin.register(ClubMember)
class ClubMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "paid_count", "total_count", "notes")
    search_fields = ("name",)
    inlines = [MemberDonationInline]

    @admin.display(description="Оплатил периодов")
    def paid_count(self, obj):
        return obj.donations.filter(is_paid=True).count()

    @admin.display(description="Всего периодов")
    def total_count(self, obj):
        return obj.donations.count()


@admin.register(DonationPeriod)
class DonationPeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "is_active", "paid_count", "total_count")
    list_filter = ("is_active",)
    ordering = ("-date",)

    @admin.display(description="Оплатили")
    def paid_count(self, obj):
        return obj.member_donations.filter(is_paid=True).count()

    @admin.display(description="Всего")
    def total_count(self, obj):
        return obj.member_donations.count()


@admin.register(MemberDonation)
class MemberDonationAdmin(admin.ModelAdmin):
    list_display = ("member", "period", "is_paid", "amount", "note")
    list_filter = ("period", "is_paid")
    search_fields = ("member__name",)
    list_editable = ("is_paid", "amount")
    ordering = ("-period__date", "member__name")
