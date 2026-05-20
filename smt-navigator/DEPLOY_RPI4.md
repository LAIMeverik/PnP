# Развёртывание SMT Navigator Web на Raspberry Pi 4

## 1) Подготовка Raspberry Pi

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git
```

## 2) Клонирование проекта

```bash
cd /opt
sudo git clone https://github.com/LAIMeverik/PnP.git
sudo chown -R $USER:$USER /opt/PnP
cd /opt/PnP
git checkout final
```

## 3) Установка зависимостей

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r /opt/PnP/smt_web/requirements.txt
```

## 4) Локальный запуск

```bash
cd /opt/PnP/smt-navigator
uvicorn main:app --host 0.0.0.0 --port 8000
```

После запуска откройте в браузере:

`http://<IP_RASPBERRY_PI>:8000`

## 5) Автозапуск через systemd

Создайте unit-файл:

```bash
sudo nano /etc/systemd/system/smt-web.service
```

Содержимое:

```ini
[Unit]
Description=SMT Navigator Web
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/PnP/smt-navigator
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/PnP/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Активируйте сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smt-web
sudo systemctl start smt-web
sudo systemctl status smt-web
```

## 6) Обновление приложения

```bash
cd /opt/PnP
git pull
git checkout final
source .venv/bin/activate
pip install -r /opt/PnP/smt-navigator/requirements.txt
sudo systemctl restart smt-web
```

## 8) Данные склада (SQLite)

- Склад теперь хранится в файле: `/opt/PnP/warehouse.db`
- При первом запуске данные из старого `warehouse.json` автоматически мигрируются в SQLite.
- Для бэкапа достаточно копировать `warehouse.db` и `smt_progress.json`.

## 7) Полезные команды

```bash
sudo systemctl restart smt-web
sudo systemctl stop smt-web
sudo journalctl -u smt-web -f
```

## 9) Тестирование напрямую в PyCharm (IDE)

1. Откройте проект `PnP` в PyCharm.
2. Выберите интерпретатор из `.venv` и установите зависимости:
   `pip install -r <project_root>/smt-navigator/requirements.txt`
3. Создайте Run Configuration:
   - **Script path**: `<project_root>/smt-navigator/main.py`
   - **Working directory**: `<project_root>/smt-navigator`
4. (Опционально) переменные окружения:
   - `SMT_HOST=0.0.0.0`
   - `SMT_PORT=8000`
   - `SMT_RELOAD=1` (для авто-перезапуска при изменениях)
5. Запустите конфигурацию и откройте:
   `http://127.0.0.1:8000`
