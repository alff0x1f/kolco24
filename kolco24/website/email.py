from django.core.mail import send_mail, BadHeaderError
from django.core.mail import EmailMessage

def send_test_email(flogin_key):
    subject = 'тема письма'
    message = 'Ключ: %s' % flogin_key
    from_email = "Кольцо 24 <org@kolco24.ru>"

    recepient = []
    recepient.append('alff3one@gmail.com')

    email1 = EmailMessage(
        subject,
        message,
        from_email,
        recepient,
        ["alff0x1f@gmail.com"]
    )
    email1.send(fail_silently=False)

def send_login_email(recepient_email, flogin_key):
    subject = 'Ссылка для входа'
    message = """Ссылка для входа:
https://kolco24.ru/login/%s

Ссылка действительна 24 часа. Получить новую ссылку для входа можно тут: https://kolco24.ru/login
    """% (flogin_key)

    from_email = 'Кольцо 24 <org@kolco24.ru>'

    recepient = []
    recepient.append('alff3one@gmail.com')

    flogin_email = EmailMessage(
        subject,
        message,
        from_email,
        recepient,
        ["alff0x1f@gmail.com"]
    )
    flogin_email.send(fail_silently=False)