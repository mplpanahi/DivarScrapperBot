import os
import re
import time
import json
import logging
import urllib.parse
from typing import Dict, Any, Tuple, List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("divar_api")

class DivarAPI:
    """Client for interacting with Divar REST API."""

    BASE_URL = "https://api.divar.ir/v8"

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fa,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://divar.ir",
        "Referer": "https://divar.ir/",
    }

    COMMON_CITIES = {
        "tehran": "1",
        "karaj": "2",
        "mashhad": "3",
        "isfahan": "4",
        "tabriz": "5",
        "shiraz": "6",
        "ahvaz": "7",
        "qom": "8",
        "kermanshah": "9",
        "urmia": "10",
        "zahedan": "11",
        "rasht": "12",
        "kerman": "13",
        "hamedan": "14",
        "arak": "15",
        "yazd": "16",
        "ardabil": "17",
        "bandar-abbas": "18",
        "zanjan": "20",
        "sanandaj": "21",
        "qazvin": "22",
        "khorramabad": "23",
        "gorgan": "24",
        "sari": "25",
        "bojnurd": "28",
        "bushehr": "29",
        "birjand": "30",
        "ilam": "31",
        "semnan": "33",
        "yasuj": "35",
        "shahr-e-kord": "36",
    }

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        
        # Setup retry adapter for stable connection to Sotoon CDN
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.city_map = self._load_city_map()

    def _load_city_map(self) -> Dict[str, str]:
        path = "data/city_map.json"
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return dict(self.COMMON_CITIES)

    def get_city_id(self, city: str) -> str:
        """Resolves city slug or Persian name to Divar's internal numeric city ID."""
        c = str(city).strip().lower()
        if c.isdigit():
            return c
        if c in self.COMMON_CITIES:
            return self.COMMON_CITIES[c]
        if c in self.city_map:
            return self.city_map[c]
        for slug, cid in self.COMMON_CITIES.items():
            if slug == c:
                return cid
        return self.COMMON_CITIES.get("mashhad", "3")

    @staticmethod
    def parse_divar_url(url: str) -> Dict[str, Any]:
        """
        Parses a Divar category or search URL.
        Example: https://divar.ir/s/mashhad/jobs
        Returns: {'city': 'mashhad', 'category': 'jobs', 'query': None, 'filters': {}}
        """
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url

        parsed = urllib.parse.urlparse(url)
        path = parsed.path.strip("/")
        parts = path.split("/")

        city = "mashhad"
        category = "ROOT"

        # e.g., s/mashhad/jobs or s/tehran
        if len(parts) >= 2 and parts[0] == "s":
            city = parts[1]
            if len(parts) >= 3:
                category = parts[2]
        elif len(parts) == 1 and parts[0]:
            city = parts[0]

        query_params = urllib.parse.parse_qs(parsed.query)
        q = query_params.get("q", [None])[0]

        return {
            "city": city,
            "category": category if category != "ROOT" else None,
            "query": q,
            "original_url": url
        }

    def request_otp(self, phone: str) -> Dict[str, Any]:
        """Sends OTP verification SMS to user's phone number."""
        phone = phone.strip()
        if phone.startswith("+98"):
            phone = "0" + phone[3:]
        elif phone.startswith("98"):
            phone = "0" + phone[2:]

        url = f"{self.BASE_URL}/auth/authenticate"
        payload = {"phone": phone}
        response = self.session.post(url, json=payload, timeout=15)
        
        if response.status_code == 200:
            return {"success": True, "data": response.json(), "phone": phone}
        else:
            try:
                err_data = response.json()
                msg = err_data.get("message", response.text)
            except Exception:
                msg = response.text
            return {"success": False, "error": msg, "status_code": response.status_code, "phone": phone}

    def verify_otp(self, phone: str, code: str) -> Dict[str, Any]:
        """Submits OTP code and retrieves permanent auth token."""
        phone = phone.strip()
        if phone.startswith("+98"):
            phone = "0" + phone[3:]
        elif phone.startswith("98"):
            phone = "0" + phone[2:]

        code = code.strip()
        url = f"{self.BASE_URL}/auth/confirm"
        payload = {"phone": phone, "code": code}
        response = self.session.post(url, json=payload, timeout=15)

        if response.status_code == 200:
            data = response.json()
            token = data.get("token")
            return {"success": True, "token": token, "phone": phone}
        else:
            try:
                err_data = response.json()
                msg = err_data.get("message", response.text)
            except Exception:
                msg = response.text
            return {"success": False, "error": msg, "status_code": response.status_code}

    def search_posts(
        self,
        city: str = "mashhad",
        category: Optional[str] = None,
        query: Optional[str] = None,
        pagination_data: Optional[Dict[str, Any]] = None,
        timeout: int = 15
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Fetches one page of search results for a specific city.
        """
        url = f"{self.BASE_URL}/postlist/w/search"
        
        # Resolve to numeric city ID (e.g. '3' for mashhad)
        if city and city.lower() != "iran":
            city_id = self.get_city_id(city)
            city_ids = [city_id]
        else:
            city_ids = []

        form_data = {}
        if category and category != "ROOT":
            form_data["category"] = {"str": {"value": category}}
        if query:
            form_data["query"] = {"str": {"value": query}}

        payload: Dict[str, Any] = {
            "city_ids": city_ids,
            "search_data": {
                "form_data": {
                    "data": form_data
                }
            }
        }

        if pagination_data:
            payload["pagination_data"] = pagination_data

        response = self.session.post(url, json=payload, timeout=timeout)
        if response.status_code != 200:
            logger.error(f"Search failed with code {response.status_code}: {response.text[:200]}")
            response.raise_for_status()

        data = response.json()
        widgets = data.get("list_widgets", [])
        
        posts = []
        for w in widgets:
            if w.get("widget_type") == "POST_ROW":
                post_data = w.get("data", {})
                token = post_data.get("token")
                if not token and "action" in post_data:
                    token = post_data["action"].get("payload", {}).get("token")
                
                title = post_data.get("title", "")
                image_url = post_data.get("image_url", "")
                
                desc_lines = []
                for field in ["top_description_text", "middle_description_text", "bottom_description_text"]:
                    val = post_data.get(field)
                    if val:
                        desc_lines.append(val)

                web_info = post_data.get("action", {}).get("payload", {}).get("web_info", {})
                district = web_info.get("district_persian", "")
                city_persian = web_info.get("city_persian", city)

                posts.append({
                    "token": token,
                    "title": title,
                    "image_url": image_url,
                    "city": city,
                    "city_persian": city_persian,
                    "district": district,
                    "summary_desc": " | ".join(desc_lines),
                    "url": f"https://divar.ir/v/{token}" if token else ""
                })

        next_page = None
        pagination = data.get("pagination", {})
        if pagination.get("has_next_page") and pagination.get("data"):
            next_page = pagination.get("data")

        return posts, next_page

    def get_post_details(self, token: str, timeout: int = 15) -> Dict[str, Any]:
        """Fetches detailed information of an ad (description, attributes, photos, contact_uuid)."""
        url = f"{self.BASE_URL}/posts-v2/web/{token}"
        response = self.session.get(url, timeout=timeout)
        if response.status_code != 200:
            return {}

        data = response.json()
        seo = data.get("seo", {})
        schema = seo.get("post_seo_schema", {})
        web_info = seo.get("web_info", {})
        contact_info = data.get("contact", {})
        contact_uuid = contact_info.get("contact_uuid", "")

        title = seo.get("title") or schema.get("name") or ""
        description = schema.get("description") or seo.get("description") or ""
        price = schema.get("offers", {}).get("price") or ""
        category = schema.get("category") or web_info.get("category_slug_persian") or ""
        district = web_info.get("district_persian") or ""
        city_persian = web_info.get("city_persian") or ""
        unavailable_after = seo.get("unavailable_after") or ""

        attributes = {}
        images = []
        if schema.get("image"):
            images.append(schema["image"])

        for section in data.get("sections", []):
            widgets = section.get("widgets", [])
            for w in widgets:
                w_type = w.get("widget_type")
                w_data = w.get("data", {})
                if w_type == "UNEXPANDABLE_ROW":
                    title_attr = w_data.get("title")
                    val_attr = w_data.get("value")
                    if title_attr and val_attr:
                        attributes[title_attr] = val_attr
                elif w_type == "IMAGE_CAROUSEL":
                    for item in w_data.get("items", []):
                        img_url = item.get("image", {}).get("url")
                        if img_url and img_url not in images:
                            images.append(img_url)

        return {
            "token": token,
            "title": title,
            "description": description,
            "price": price,
            "category": category,
            "district": district,
            "city_persian": city_persian,
            "unavailable_after": unavailable_after,
            "images": images,
            "attributes": attributes,
            "contact_uuid": contact_uuid,
            "url": f"https://divar.ir/v/{token}"
        }

    def get_contact_info(
        self,
        token: str,
        auth_token: str,
        contact_uuid: Optional[str] = None,
        timeout: int = 15
    ) -> Dict[str, Any]:
        """
        Retrieves phone number for an ad using an authenticated session token.
        Uses Divar's v8 endpoint: POST https://api.divar.ir/v8/postcontact/web/contact_info_v2/{token}
        """
        if not contact_uuid:
            details = self.get_post_details(token)
            contact_uuid = details.get("contact_uuid")

        url = f"{self.BASE_URL}/postcontact/web/contact_info_v2/{token}"
        headers = dict(self.DEFAULT_HEADERS)
        headers["authorization"] = f"Basic {auth_token}" if not auth_token.startswith("Basic ") else auth_token
        headers["content-type"] = "application/json"
        headers["Referer"] = f"https://divar.ir/v/{token}"

        payload = {}
        if contact_uuid:
            payload["contact_uuid"] = contact_uuid

        try:
            response = self.session.post(url, json=payload, headers=headers, timeout=timeout)
            
            if response.status_code == 200:
                resp_data = response.json()
                
                phone_number = None
                for widget in resp_data.get("widget_list", []):
                    w_data = widget.get("data", {})
                    action = w_data.get("action", {})
                    payload_data = action.get("payload", {})
                    if "phone_number" in payload_data:
                        phone_number = payload_data["phone_number"]
                        break
                    val = w_data.get("value", "")
                    if re.match(r'^(?:0|\+?98)?9\d{9}$', val.replace(" ", "")):
                        phone_number = val
                        break

                if not phone_number:
                    raw_text = json.dumps(resp_data, ensure_ascii=False)
                    phones = re.findall(r'(?:(?:\+|00)?98|0)?(9\d{9})', raw_text)
                    if phones:
                        phone_number = "0" + phones[0]

                if phone_number:
                    return {
                        "success": True,
                        "phone_number": phone_number,
                        "quota_exceeded": False,
                        "error": None
                    }
                else:
                    return {
                        "success": True,
                        "phone_number": "بدون شماره مستقیم (چت دیوار)",
                        "quota_exceeded": False,
                        "error": None
                    }

            elif response.status_code in (403, 429):
                err_text = response.text
                is_quota = ("سقف" in err_text or "limit" in err_text.lower() or 
                            "quota" in err_text.lower() or "blocked" in err_text.lower() or
                            response.status_code == 429)
                return {
                    "success": False,
                    "phone_number": None,
                    "quota_exceeded": is_quota,
                    "error": f"HTTP {response.status_code}: {err_text[:120]}"
                }
            else:
                return {
                    "success": False,
                    "phone_number": None,
                    "quota_exceeded": False,
                    "error": f"HTTP {response.status_code}: {response.text[:120]}"
                }

        except Exception as e:
            return {
                "success": False,
                "phone_number": None,
                "quota_exceeded": False,
                "error": str(e)
            }
