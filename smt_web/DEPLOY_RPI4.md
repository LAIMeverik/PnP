# Развёртывание SMT Navigator Web на Raspberry Pi 4

## 1) Подготовка Raspberry Pi

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git
```

## 2) Клонирование проекта

```bash
sudo systemctl stop smt-web || true
sudo systemctl disable smt-web || true
sudo rm -rf /opt/PnP
```

Прямая ссылка на ZIP ветки `copilot/update-calculation-logic-visualization`:

`https://github.com/LAIMeverik/PnP/archive/refs/heads/copilot/update-calculation-logic-visualization.zip`

Рекомендуемый вариант через git:

```bash
cd /opt
sudo git clone --branch copilot/update-calculation-logic-visualization --single-branch https://github.com/LAIMeverik/PnP.git /opt/PnP
sudo chown -R $USER:$USER /opt/PnP
cd /opt/PnP
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
cd /opt/PnP/smt_web
/opt/PnP/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

После запуска откройте в браузере:

`http://<IP_вашей_Raspberry>:8000`

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
WorkingDirectory=/opt/PnP/smt_web
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
git pull origin copilot/update-calculation-logic-visualization
source .venv/bin/activate
pip install -r /opt/PnP/smt_web/requirements.txt
sudo systemctl restart smt-web
```

## 7) Полезные команды

```bash
sudo systemctl restart smt-web
sudo systemctl stop smt-web
sudo journalctl -u smt-web -f
```
