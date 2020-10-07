from django.core.mail import send_mail, BadHeaderError
from django.core.mail import EmailMessage
from website.models import Team
import string
import random


def send_login_email(recepient_email, flogin_key):
    subject = 'Кольцо24 - ссылка для входа'
    message = """Ссылка для входа:
https://kolco24.ru/login/%s

Ссылка действительна 24 часа. Получить новую ссылку для входа можно тут: https://kolco24.ru/login
    """% (flogin_key)

    from_email = 'Кольцо 24 <org@kolco24.ru>'

    recepient = []
    recepient.append(recepient_email)

    flogin_email = EmailMessage(
        subject,
        message,
        from_email,
        recepient,
        ["alff0x1f@gmail.com"]
    )
    flogin_email.send(fail_silently=False)


def send_message(recepient_email, start_number, teamname, password, username):
    subject = 'Кольцо24 - Онлайн'
    message = """Привет, {username}, в связи с тем, что в Челябинской области введен запрет на 
любые массовые мероприятия, мы вынуждены отменить "Кольцо-24".

И объявить о проведении "Кольца-24 Онлайн"!
Суть не меняется - вы можете так же сходить в поход выходного дня с картой и 
собрать контрольные пункты, соблюдая все меры социального дистанцирования. 
Всё остается прежним, кроме условий для массового скопления людей. Будем 
считать, что вы уже выросли и достаточно самостоятельны для такого.

Все подробности расскажем на сегодняшем (7 октября) онлайн-брифинге в 20:00 
по уфимскому времени, ссылка на трансляцию будет в группе ВК.

Старты 10 октября (суббота) в удобное время с 10.00 – 16.00.

Ваша команда: 
номер {start_number}, 
Название {teamname}

Необходимые действия перед выходом на маршрут:

1) со стартовой точки сделать контрольный звонок по номеру (будет указан позже) 
и сообщить о выходе на маршрут,
2) включить запись трека устройстве,
3) запустить приложение в смартфоне О-GPS (http://o-gps-center.ru)

На дистанции - должна быть постоянная запись трека и запущенное приложение 
O-GPS, на контрольных пунктах делаете фотографии со всеми участниками и ясно 
различимым номером КП.

При финишировании:
1) Позвонить по указанному номеру с точки финиша.

Трек, фото отправить на почту (будет указано позже) 12.10.20 до 23.00 по Уфе

Обязательное снаряжение:
1) Фотоаппарат с установленным и настроенным с точностью до минуты уфимским 
временем (+5 GMT)
2) Средство для записи трека (навигатор или смартфон)
3) Смартфон с установленным приложением O-GPS (http://o-gps-center.ru, только 
для андроид ) – для возможности онлайн наблюдения за командами.

Вероятно, вам понадобиться дополнительный аккумулятор. Крайне не рекомендуем 
объединять три вышеперечисленные устройства в одном смартфоне.
Остальное рекомендуемое снаряжение не отличается от предыдущих лет.

Как вы уже поняли – не будет никаких проверок обязательного снаряжения (но 
лучше бы вам его иметь - горы не прощают ошибок), скопления людей в финишных 
коридорах, и, к сожалению, борща.

Но, если вы не хотите участвовать в таком формате, то можно сдать слоты и 
получить назад деньги (за вычетом 8%, комиссии платежной системы), 
либо перенести слоты на следующий год - в личном кабинете выбрать год участия 
2021 год. Заявку на возврат взноса добавим в личный кабинет в скором времени.

Реквизиты для входа в личный кабинет (https://kolco24.ru/passlogin):
email: {email}
пароль: {password}

Все же надеемся, что никакие внешние силы не остановят вас в стремлении 
насладиться особой природой Аджигардака.

Команда организаторов Кольца 24
""".format(username=username,
           start_number=start_number,
           teamname=teamname,
           email=recepient_email,
           password=password)

    from_email = 'Кольцо 24 <org@kolco24.ru>'

    recepient = []
    recepient.append(recepient_email)

    message = EmailMessage(
        subject,
        message,
        from_email,
        recepient,
        ["alff0x1f@gmail.com"]
    )
    message.send(fail_silently=False)

def send_to_all_teams():
    teams = Team.objects.filter(year='2020').select_related('owner')
    teams = [t for t in teams if t.paid_sum > 0]

    for team in teams:
        alphabet = string.ascii_letters + string.digits
        password = ''.join(random.choice(alphabet) for _ in range(8))
        team.owner.set_password(password)
        team.owner.save()
        print(team.teamname, team.owner.email, password)
        send_message(recepient_email=team.owner.email,
                     start_number=team.start_number,
                     teamname=team.teamname,
                     password=password,
                     username=' '.join([team.owner.last_name,
                                        team.owner.first_name]))
