import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import random
import json
import time
import pickle
import os
from datetime import datetime
from fake_useragent import UserAgent
import logging

class StealthSession:
    def __init__(self, cookie_file="cookies.pkl", proxy=None):
        self.session = self._create_session()
        self.cookie_file = cookie_file
        self.last_request_time = None
        self.proxy = proxy
        self.ua = UserAgent(browsers=['chrome'])
        self.load_cookies()
        self._setup_headers()
        self.logger = logging.getLogger(__name__)

    def _create_session(self):
        session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504, 429]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    def _setup_headers(self):
        self.default_headers = {
            'User-Agent': self._get_random_user_agent(),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
            'device-type': self._get_random_device(),
            'sec-ch-ua-platform': self._get_random_platform(),
            'sec-ch-ua-mobile': '?1',
        }
        self.session.headers.update(self.default_headers)

    def _get_random_user_agent(self):
        return self.ua.random

    def _get_random_device(self):
        devices = ['mobile', 'tablet', 'desktop']
        return random.choice(devices)

    def _get_random_platform(self):
        platforms = ['Android', 'Windows', 'iOS']
        return f'"{random.choice(platforms)}"'

    def load_cookies(self):
        if os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, 'rb') as f:
                    cookies = pickle.load(f)
                    if self._are_cookies_valid(cookies):
                        self.session.cookies.update(cookies)
                        self.logger.info(f"Loaded cookies from {self.cookie_file}")
                    else:
                        self.logger.warning("Cookies expired, starting fresh session")
            except Exception as e:
                self.logger.error(f"Error loading cookies: {e}")

    def save_cookies(self):
        try:
            os.makedirs(os.path.dirname(self.cookie_file), exist_ok=True)
            with open(self.cookie_file, 'wb') as f:
                pickle.dump(self.session.cookies, f)
            self.logger.info(f"Saved cookies to {self.cookie_file}")
        except Exception as e:
            self.logger.error(f"Error saving cookies: {e}")

    def _are_cookies_valid(self, cookies):
        for cookie in cookies:
            if hasattr(cookie, 'expires'):
                if cookie.expires:
                    if datetime.fromtimestamp(cookie.expires) < datetime.now():
                        return False
        return True

    def _add_random_delay(self):
        if self.last_request_time:
            elapsed = time.time() - self.last_request_time
            if elapsed < 2:
                delay = random.uniform(1, 3)
                time.sleep(delay)
        self.last_request_time = time.time()

    def _simulate_human_behavior(self):
        # Add random mouse movements
        time.sleep(random.uniform(0.5, 1.5))

        # Simulate random scroll
        if random.random() < 0.3:
            time.sleep(random.uniform(0.2, 0.8))

    def request(self, method, url, **kwargs):
        self._add_random_delay()
        self._simulate_human_behavior()

        # Add random request ID and timestamp
        headers = kwargs.get('headers', {})
        request_headers = self.default_headers.copy()
        request_headers.update({
            'X-Request-ID': f'{random.getrandbits(32):08x}',
            'X-Timestamp': str(int(time.time() * 1000))
        })
        request_headers.update(headers)
        kwargs['headers'] = request_headers

        # Add random query parameter to bypass caching
        if '?' in url:
            url += f'&_={int(time.time() * 1000)}'
        else:
            url += f'?_={int(time.time() * 1000)}'

        if self.proxy:
            kwargs['proxies'] = self.proxy

        try:
            response = self.session.request(method, url, **kwargs)
            self.save_cookies()
            return response
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {e}")
            raise

    def get(self, *args, **kwargs):
        return self.request('GET', *args, **kwargs)

    def post(self, *args, **kwargs):
        return self.request('POST', *args, **kwargs)
