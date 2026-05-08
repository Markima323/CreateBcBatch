# Container Batch

Root launcher: `..\run.bat`

Files in this folder:

- `create_container_batch.py`: GUI and Jiandaoyun create flow
- `print_utils.py`: Windows printer integration
- `config.json`: API token, app ids, field ids and label settings
- `cache.json`: last used combination cache
- `Template.pdf`: generated automatically if missing

Notes:

- The GUI automatically saves the current combination when you click `Create Batch / 批量创建`.
- End-of-process popups are disabled. Progress and errors are written to the status bar and log.
- Label output is PDF. The code, area and size fields use Songti 48 bold styling; type and SKU use Songti 18 bold styling.
