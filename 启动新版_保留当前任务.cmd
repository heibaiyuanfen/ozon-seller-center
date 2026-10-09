@echo off
chcp 65001 >nul
set "OZON_RFBS_DATA_DIR=%~dp0RFBS上品工具"
start "" "%~dp0发布\image-wait-120s\Ozon_RFBS上品工具\Ozon_RFBS上品工具.exe"
