import time
import random
import threading
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from divar_api import DivarAPI
from auth_manager import AuthManager
from database import Database

logger = logging.getLogger("scraper_engine")

class ScraperEngine:
    """Core background scraping engine with live callbacks and rate-limiting."""

    def __init__(self, db: Database, auth_manager: AuthManager):
        self.db = db
        self.auth_manager = auth_manager
        self.api = DivarAPI()
        
        self.is_running = False
        self.should_stop = False
        self.thread: Optional[threading.Thread] = None

        self.stats = {
            "status": "idle",  # idle, running, stopped, finished, error
            "total_found": 0,
            "saved_count": 0,
            "phones_extracted": 0,
            "current_page": 0,
            "target_limit": 100,
            "start_time": None,
            "last_error": None
        }

        # Subscribers for live logs & events
        self.log_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.ad_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.status_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.recent_logs: List[Dict[str, Any]] = []

    def subscribe_log(self, cb: Callable[[Dict[str, Any]], None]):
        self.log_callbacks.append(cb)

    def subscribe_ad(self, cb: Callable[[Dict[str, Any]], None]):
        self.ad_callbacks.append(cb)

    def subscribe_status(self, cb: Callable[[Dict[str, Any]], None]):
        self.status_callbacks.append(cb)

    def log(self, message: str, level: str = "info"):
        """Emits a log message to all subscribers and stores in recent buffer."""
        now = datetime.now().strftime("%H:%M:%S")
        entry = {
            "time": now,
            "message": message,
            "level": level  # info, success, warning, error
        }
        self.recent_logs.append(entry)
        if len(self.recent_logs) > 300:
            self.recent_logs.pop(0)

        for cb in self.log_callbacks:
            try:
                cb(entry)
            except Exception:
                pass

    def emit_status(self):
        for cb in self.status_callbacks:
            try:
                cb(dict(self.stats))
            except Exception:
                pass

    def start(self, config: Dict[str, Any]) -> bool:
        """Starts the scraper in a background thread."""
        if self.is_running:
            return False

        self.is_running = True
        self.should_stop = False
        self.stats["status"] = "running"
        self.stats["total_found"] = 0
        self.stats["saved_count"] = 0
        self.stats["phones_extracted"] = 0
        self.stats["current_page"] = 0
        self.stats["target_limit"] = config.get("limit", 100)
        self.stats["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stats["last_error"] = None
        self.emit_status()

        self.thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self.thread.start()
        return True

    def stop(self):
        """Signals the scraper to stop."""
        if self.is_running:
            self.should_stop = True
            self.log("درخواست توقف ارسال شد. در حال تکمیل عملیات جاری...", "warning")

    def _run(self, config: Dict[str, Any]):
        try:
            url = config.get("url", "").strip()
            city = config.get("city", "mashhad")
            category = config.get("category", "")
            limit = int(config.get("limit", 100))
            get_phone = bool(config.get("get_phone", False))
            fetch_full_details = bool(config.get("fetch_full_details", True))
            delay_min = float(config.get("delay_min", 2.5))
            delay_max = float(config.get("delay_max", 5.0))

            # Parse URL if provided
            if url:
                parsed = self.api.parse_divar_url(url)
                city = parsed.get("city") or city
                category = parsed.get("category") or category
                self.log(f"آدرس ورودی پردازش شد: شهر: {city} | دسته‌بندی: {category}", "info")

            self.log(f"شروع جمع‌آوری آگهی‌ها (هدف: {limit if limit > 0 else 'نامحدود'} آگهی | دریافت شماره: {'فعال' if get_phone else 'غیرفعال'})", "info")

            if get_phone:
                active_accounts = [a for a in self.auth_manager.get_accounts() if a.get("status") == "active"]
                if not active_accounts:
                    self.log("هشدار: هیچ حساب فعالی برای دریافت شماره تلفن وجود ندارد! جهت دریافت شماره باید ابتدا لاگین کنید.", "warning")
                else:
                    self.log(f"{len(active_accounts)} اکانت فعال برای استخراج شماره تماس آماده است.", "success")

            pagination_data = None
            page_num = 0

            while self.is_running and not self.should_stop:
                page_num += 1
                self.stats["current_page"] = page_num
                self.log(f"در حال بارگذاری صفحه {page_num} جستجو...", "info")

                try:
                    posts, next_pagination = self.api.search_posts(
                        city=city,
                        category=category,
                        pagination_data=pagination_data
                    )
                except Exception as e:
                    self.log(f"خطا در دریافت لیست آگهی‌ها از دیوار: {e}", "error")
                    time.sleep(3)
                    break

                if not posts:
                    self.log("هیچ آگهی دیگری یافت نشد.", "info")
                    break

                self.stats["total_found"] += len(posts)
                self.log(f"تعداد {len(posts)} آگهی در صفحه {page_num} یافت شد.", "info")
                self.emit_status()

                # Process each post
                for post in posts:
                    if self.should_stop:
                        break

                    if limit > 0 and self.stats["saved_count"] >= limit:
                        self.log(f"سقف تعیین شده ({limit} آگهی) تکمیل شد.", "success")
                        self.should_stop = True
                        break

                    token = post.get("token")
                    if not token:
                        continue

                    # Retrieve full details if requested
                    full_info = dict(post)
                    if fetch_full_details:
                        try:
                            details = self.api.get_post_details(token)
                            if details:
                                full_info.update(details)
                        except Exception as e:
                            logger.warning(f"Error fetching details for {token}: {e}")

                    # Extract Phone Number if enabled
                    phone_extracted = None
                    if get_phone:
                        account = self.auth_manager.get_active_account()
                        if not account:
                            self.log("تمام اکانت‌ها سقف روزانه مشاهده شماره را رد کرده‌اند یا لاگین نشده‌اند. آگهی بدون شماره ذخیره می‌شود.", "warning")
                        else:
                            auth_token = account.get("token")
                            acc_phone = account.get("phone")
                            contact_uuid = full_info.get("contact_uuid")
                            
                            contact_res = self.api.get_contact_info(token, auth_token, contact_uuid=contact_uuid)
                            if contact_res.get("success"):
                                phone_extracted = contact_res.get("phone_number")
                                self.auth_manager.record_usage(acc_phone, success=True)
                                self.stats["phones_extracted"] += 1
                                self.log(f"شماره تماس برای «{post.get('title')[:25]}...»: {phone_extracted} (اکانت: {acc_phone})", "success")
                            elif contact_res.get("quota_exceeded"):
                                self.log(f"اکانت {acc_phone} به سقف مجاز روزانه دیوار رسید! در حال چرخش به اکانت بعدی...", "warning")
                                self.auth_manager.mark_quota_exceeded(acc_phone)
                                # Try one more active account if exists
                                next_acc = self.auth_manager.get_active_account()
                                if next_acc:
                                    contact_res2 = self.api.get_contact_info(token, next_acc.get("token"), contact_uuid=contact_uuid)
                                    if contact_res2.get("success"):
                                        phone_extracted = contact_res2.get("phone_number")
                                        self.auth_manager.record_usage(next_acc.get("phone"), success=True)
                                        self.stats["phones_extracted"] += 1
                                        self.log(f"شماره تماس دریافت شد: {phone_extracted} (اکانت جایگزین: {next_acc.get('phone')})", "success")
                            else:
                                self.log(f"عدم دریافت شماره برای {token}: {contact_res.get('error')}", "warning")

                            # Polite jitter delay between contact calls
                            sleep_sec = random.uniform(delay_min, delay_max)
                            time.sleep(sleep_sec)

                    full_info["phone_number"] = phone_extracted or ""
                    
                    # Save to database
                    self.db.save_ad(full_info)
                    self.stats["saved_count"] += 1
                    self.emit_status()

                    # Notify ad subscribers
                    for cb in self.ad_callbacks:
                        try:
                            cb(full_info)
                        except Exception:
                            pass

                # Move to next page
                if not next_pagination or self.should_stop:
                    break

                pagination_data = next_pagination
                # Short delay between search pages
                time.sleep(1.0)

            if self.should_stop:
                self.stats["status"] = "stopped"
                self.log(f"عملیات متوقف شد. مجموع آگهی‌های ذخیره شده: {self.stats['saved_count']}", "warning")
            else:
                self.stats["status"] = "finished"
                self.log(f"عملیات با موفقیت پایان یافت! مجموع آگهی‌های ذخیره شده: {self.stats['saved_count']} (شماره‌های استخراج‌شده: {self.stats['phones_extracted']})", "success")

        except Exception as e:
            self.stats["status"] = "error"
            self.stats["last_error"] = str(e)
            self.log(f"خطای غیرمنتظره در موتور استخراج: {e}", "error")
        finally:
            self.is_running = False
            self.should_stop = False
            self.emit_status()
