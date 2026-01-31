

Мини‑SCADA прототип: сканер сети + задел под подключение к контроллерам (S7/OPC UA) и хранение данных в SQLite.

## Подключение к Siemens S7‑1500 (S7comm)

> ⚠️ Для работы с S7 нужен пакет `python-snap7`.

Пример подключения и чтения тегов с записью в БД:

```python
from app.config import DB_PATH, S7_POLL_INTERVAL
from app.drivers.s7_driver import TagSpec
from app.services.s7_service import S7Service
from app.storage.workspace import WorkspaceStorage
from app.state import AppState

storage = WorkspaceStorage(DB_PATH)
state = AppState()

# Опишите теги, которые нужно читать
s7_tags = [
    TagSpec(name="TankLevel", area="DB", db=1, byte_index=0, data_type="REAL"),
    TagSpec(name="PumpOn", area="DB", db=1, byte_index=4, data_type="BOOL", bit_index=0),
]

svc = S7Service(storage=storage, tags=s7_tags, poll_interval=S7_POLL_INTERVAL, state=state, logger=print)
svc.connect(ip="10.10.101.100", rack=0, slot=1)
svc.start_polling()

# ... позже можно остановить:
# svc.stop_polling()
# svc.disconnect()
```

Хранение и чтение из БД:

```python
latest = storage.get_latest_values()
series = storage.get_series(tag_name="TankLevel", since_ts=time.time() - 3600)
```

## Запуск UI

```bash
python -m app.main
```
