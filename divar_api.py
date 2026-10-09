import re
import time
import json
import logging
import urllib.parse
from typing import Dict, Any, Tuple, List, Optional
import requests

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

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    @staticmethod
    def parse_divar_url(url: str) -> Dict[str, Any]:
        """
        Parses a Divar category or search URL.
        Example: https://divar.ir/s/mashhad/vehicles
        Returns: {'city': 'mashhad', 'category': 'vehicles', 'query': None, 'filters': {}}
        """
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url

        parsed = urllib.parse.urlparse(url)
        path = parsed.path.strip("/")
        parts = path.split("/")

        city = "iran"
        category = "ROOT"

        # e.g., s/mashhad/vehicles or s/tehran
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
        """
        Sends OTP verification SMS to user's phone number.
        Returns: dict response from Divar
        """
        phone = phone.strip()
        # Normalization: ensure starting with 0
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
        """
        Submits OTP code and retrieves permanent auth token.
        """
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
        Fetches one page of search results.
        Returns: (list_of_posts, next_pagination_data)
        """
        url = f"{self.BASE_URL}/postlist/w/search"
        
        city_ids = [city] if city and city != "iran" else ["tehran"]
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
        
        # Extract individual ads
        posts = []
        for w in widgets:
            if w.get("widget_type") == "POST_ROW":
                post_data = w.get("data", {})
                token = post_data.get("token")
                if not token and "action" in post_data:
                    token = post_data["action"].get("payload", {}).get("token")
                
                title = post_data.get("title", "")
                image_url = post_data.get("image_url", "")
                
                # Extract pricing or description text
                desc_lines = []
                for field in ["top_description_text", "middle_description_text", "bottom_description_text"]:
                    val = post_data.get(field)
                    if val:
                        desc_lines.append(val)

                # Web info (city, district)
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
        """
        Fetches detailed information of an ad (description, all attributes, photos).
        Does NOT require login.
        """
        url = f"{self.BASE_URL}/posts-v2/web/{token}"
        response = self.session.get(url, timeout=timeout)
        if response.status_code != 200:
            return {}

        data = response.json()
        seo = data.get("seo", {})
        schema = seo.get("post_seo_schema", {})
        web_info = seo.get("web_info", {})

        title = seo.get("title") or schema.get("name") or ""
        description = schema.get("description") or seo.get("description") or ""
        price = schema.get("offers", {}).get("price") or ""
        category = schema.get("category") or web_info.get("category_slug_persian") or ""
        district = web_info.get("district_persian") or ""
        city_persian = web_info.get("city_persian") or ""
        unavailable_after = seo.get("unavailable_after") or ""

        # Extract attributes & images from sections
        attributes = {}
        images = []
        if schema.get("image"):
            images.append(schema["image"])

        for section in data.get("sections", []):
            widgets = section.get("widgets", [])
            for w in widgets:
                w_type = w.get("widget_type")
                w_data = w.get("data", {})
                # List items (e.g., کارکرد, مدل, وضعیت بدنه)
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
            "url": f"https://divar.ir/v/{token}"
        }

    def get_contact_info(self, token: str, auth_token: str, timeout: int = 15) -> Dict[str, Any]:
        """
        Retrieves phone number for an ad using an authenticated session token.
        Returns: {'success': bool, 'phone_number': Optional[str], 'quota_exceeded': bool, 'error': str}
        """
        url = f"{self.BASE_URL}/post-contact/web/{token}"
        headers = dict(self.DEFAULT_HEADERS)
        headers["authorization"] = f"Basic {auth_token}" if not auth_token.startswith("Basic ") else auth_token

        try:
            response = self.session.get(url, headers=headers, timeout=timeout)
            
            if response.status_code == 200:
                resp_data = response.json()
                raw_text = json.dumps(resp_data, ensure_ascii=False)
                
                # Check for phone number pattern 09xxxxxxxxx or +989xxxxxxxxx
                phones = re.findall(r'(?:(?:\+|00)?98|0)?(9\d{9})', raw_text)
                if phones:
                    phone_number = "0" + phones[0]
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
                    "error": f"HTTP {response.status_code}: {err_text[:100]}"
                }
            else:
                return {
                    "success": False,
                    "phone_number": None,
                    "quota_exceeded": False,
                    "error": f"HTTP {response.status_code}: {response.text[:100]}"
                }

        except Exception as e:
            return {
                "success": False,
                "phone_number": None,
                "quota_exceeded": False,
                "error": str(e)
            }
