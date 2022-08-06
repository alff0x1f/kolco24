from django.core.mail import EmailMessage
from website.models import Payment, PaymentsYa, Team


def send_login_email(recepient_email, flogin_key):
    subject = "Кольцо24 - ссылка для входа"
    message = """Ссылка для входа:
https://kolco24.ru/login/%s

Ссылка действительна 24 часа. Получить новую ссылку для входа можно тут: https://kolco24.ru/login
    """ % (
        flogin_key
    )

    from_email = "Кольцо 24 <org@kolco24.ru>"

    recepient = []
    recepient.append(recepient_email)

    flogin_email = EmailMessage(
        subject, message, from_email, recepient, ["alff0x1f@gmail.com"]
    )
    flogin_email.send(fail_silently=False)


def send_message(recepient_email, teamname, username, payments, return_sum):
    subject = "Возврат стартовых взносов за команду %s" % teamname
    message = """Здравствуйте, {username}

Мы наконец-то отошли от оформления протоколов и готовы приступить к возврату взносов 
тем командам, которые отказались от участия. 
Ваша команда "{teamname}" в их числе. 

Платежи за команду:
{payments}
Сумма к возврату (с вычетом 8%): {return_sum} руб

Варианты возврата следующие:
 1) Яндекс деньги (самый удобный для нас вариант, тк платежи поступали туда)
 2) Тиньков
 3) Сбербанк
 4) Карта любого банка с подключенной системой быстрых платежей (СБП)
 
Если вы ещё не сообщали нам реквизиты и способ возврата, то это можно сделать, ответив 
на это письмо. В случае Яндекс Денег необходим номер кошелька, в варианте банковской 
карты, необходимо сообщить номер телефона, к которому привязана банковская карта.

Спасибо за ожидание и терпение! Надеемся что в следующим году не будет таких 
форм-мажорных факторов и соревнование пройдут в штатном режиме. 

Команда организаторов Кольца 24
""".format(
        username=username, teamname=teamname, payments=payments, return_sum=return_sum
    )

    from_email = "Кольцо 24 <org@kolco24.ru>"

    recepient = []
    recepient.append(recepient_email)

    message = EmailMessage(
        subject, message, from_email, recepient, ["alff0x1f@gmail.com"]
    )
    message.send(fail_silently=False)


def send_to_all_teams():
    teams = Team.objects.filter(year="10").select_related("owner")
    teams = [t for t in teams if t.paid_sum > 0]

    for team in teams:
        payment_str = ""
        payments = Payment.objects.filter(status="done", team_id=team.id)
        for payment in payments:
            ya_pay = PaymentsYa.objects.filter(label=payment.id)[0]
            payment_str += "%s %s\n" % (ya_pay.datetime, ya_pay.withdraw_amount)
            print(team.teamname, " ", ya_pay.datetime, ya_pay.withdraw_amount)
        send_message(
            recepient_email=team.owner.email,
            teamname=team.teamname,
            username=team.owner.first_name,
            payments=payment_str,
            return_sum=team.paid_sum * 0.92,
        )
