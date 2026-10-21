@echo off
rem st-sync-ui.cmd : open the ST Sync screen (Streamlit) in the browser
cd /d "%~dp0"
python -m streamlit run "%~dp0st_sync_ui.py"
