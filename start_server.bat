@echo off
cd /d "C:\Users\Administrator\Desktop\AI项目\空间质检"
python -m uvicorn app.main:app --host 0.0.0.0 --port 45678 > uvicorn_out.log 2> uvicorn_err.log
