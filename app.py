import os
import json
import asyncio
from typing import Dict, Any, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from divar_api import DivarAPI
from auth_manager import AuthManager
from database import Database
from scraper_engine import ScraperEngine

# Initialize modules
db = Database("data/divar_scraper.db")
auth_manager = AuthManager("data/accounts.json")
scraper = ScraperEngine(db, auth_manager)
divar_api = DivarAPI()

# Connected WebSocket clients
active_websockets: List[WebSocket] = []
main_loop = None

async def broadcast_ws(message: dict):
    for ws in list(active_websockets):
        try:
            await ws.send_json(message)
        except Exception:
            if ws in active_websockets:
                active_websockets.remove(ws)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()

    # Hook engine callbacks to websocket broadcast
    def on_log(entry):
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                broadcast_ws({"type": "log", "data": entry}), main_loop
            )

    def on_ad(ad):
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                broadcast_ws({"type": "new_ad", "data": ad}), main_loop
            )

    def on_status(stats):
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                broadcast_ws({"type": "status", "data": stats}), main_loop
            )

    scraper.subscribe_log(on_log)
    scraper.subscribe_ad(on_ad)
    scraper.subscribe_status(on_status)
    yield

app = FastAPI(title="Divar Scraper Bot", lifespan=lifespan)

# Pydantic models
class SendOtpRequest(BaseModel):
    phone: str

class VerifyOtpRequest(BaseModel):
    phone: str
    code: str

class StartScraperRequest(BaseModel):
    url: str = ""
    city: str = "mashhad"
    category: str = ""
    limit: int = 100
    get_phone: bool = False
    fetch_full_details: bool = True
    delay_min: float = 2.5
    delay_max: float = 5.0

# API Endpoints
@app.get("/api/accounts")
def get_accounts():
    return auth_manager.get_accounts()

@app.delete("/api/accounts/{phone}")
def delete_account(phone: str):
    success = auth_manager.remove_account(phone)
    if not success:
        raise HTTPException(status_code=404, detail="اکانت یافت نشد")
    return {"success": True}

@app.post("/api/accounts/{phone}/reset")
def reset_account(phone: str):
    auth_manager.reset_status(phone)
    return {"success": True}

@app.post("/api/auth/send-otp")
def send_otp(req: SendOtpRequest):
    res = divar_api.request_otp(req.phone)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "ارسال پیامک با خطا مواجه شد"))
    return res

@app.post("/api/auth/verify-otp")
def verify_otp(req: VerifyOtpRequest):
    res = divar_api.verify_otp(req.phone, req.code)
    if not res.get("success") or not res.get("token"):
        raise HTTPException(status_code=400, detail=res.get("error", "کد وارد شده صحیح نیست یا منقضی شده است"))
    
    # Save account
    account = auth_manager.add_account(res.get("phone"), res.get("token"))
    return {"success": True, "account": account}

@app.post("/api/scraper/start")
def start_scraper(req: StartScraperRequest):
    if scraper.is_running:
        return {"success": False, "message": "ربات در حال حاضر در حال اجرا است."}

    # Validate active accounts if phone extraction is requested
    if req.get_phone:
        active_accounts = [a for a in auth_manager.get_accounts() if a.get("status") == "active"]
        if not active_accounts:
            return {
                "success": False,
                "message": "⚠️ برای استخراج شماره تماس، باید حداقل یک حساب فعال دیوار ثبت کرده باشید! لطفاً ابتدا با شماره موبایل خود لاگین کنید یا تیک «استخراج شماره تماس» را بردارید."
            }

    success = scraper.start(req.dict())
    return {"success": success}

@app.post("/api/scraper/stop")
def stop_scraper():
    scraper.stop()
    return {"success": True}

@app.get("/api/scraper/status")
def get_scraper_status():
    return {
        "stats": scraper.stats,
        "db_counts": db.get_counts(),
        "recent_logs": scraper.recent_logs[-50:]
    }

@app.get("/api/ads")
def get_ads(limit: int = 50, offset: int = 0, with_phone_only: bool = False):
    ads = db.get_ads(limit=limit, offset=offset, with_phone_only=with_phone_only)
    counts = db.get_counts()
    return {"ads": ads, "counts": counts}

@app.post("/api/ads/clear")
def clear_ads():
    db.clear_ads()
    return {"success": True}

@app.get("/api/export/excel")
def export_excel():
    path = db.export_to_excel("output/divar_ads.xlsx")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="divar_ads.xlsx"
    )

@app.get("/api/export/csv")
def export_csv():
    path = db.export_to_csv("output/divar_ads.csv")
    return FileResponse(
        path,
        media_type="text/csv",
        filename="divar_ads.csv"
    )

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        # Send initial state
        await websocket.send_json({"type": "status", "data": scraper.stats})
        await websocket.send_json({"type": "init_logs", "data": scraper.recent_logs[-30:]})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if websocket in active_websockets:
            active_websockets.remove(websocket)

# Serve Frontend HTML
@app.get("/", response_class=HTMLResponse)
def index_page():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
