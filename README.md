# kolco24

##### How to install:
You need python3
```
git clone git@github.com:alff0x1f/kolco24.git 
(or git clone https://github.com/alff0x1f/kolco24.git)
cd kolco24
virtualenv -p python3 venv
source venv/bin/activate
cd kolco24
pip install -r requirements.txt 
```

Rename `kolco24/settings.py.example` to `kolco24/settings.py` and edit it (at least you must set SECRET_KEY (just random string) and DIRS TEMPLATES)

##### Create DB:
```
python manage.py migrate
```

##### Run devserver:

```
python manage.py runserver 0:8000
```
