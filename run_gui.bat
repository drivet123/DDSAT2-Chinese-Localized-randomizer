@echo off
rem Launch the DDS2 randomizer GUI.
cd /d "%~dp0"
python dds2_randomizer_gui.py
if errorlevel 1 pause
