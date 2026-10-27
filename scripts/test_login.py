#!/usr/bin/env python3
"""Test the AF Bostäder client outside Home Assistant.

Usage:  pip install aiohttp
        python scripts/test_login.py you@example.com 'your-password'
"""

import asyncio
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import aiohttp

API_PATH = Path(__file__).parent.parent / "custom_components" / "afbostader" / "api.py"
spec = importlib.util.spec_from_file_location("afb_api", API_PATH)
api = importlib.util.module_from_spec(spec)
sys.modules["afb_api"] = api
spec.loader.exec_module(api)


async def main(username: str, password: str) -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = api.AfbClient(
            session, username, password, ZoneInfo("Europe/Stockholm")
        )
        await client.login()
        print("Login OK, Aptus token found")
        data = await client.async_get_data()
        print("\nYour bookings:")
        for b in data.bookings or []:
            print(" ", b.as_dict())
        print("\nNext available (first 5):")
        for s in data.available[:5]:
            print(" ", s.as_dict())


if __name__ == "__main__":
    asyncio.run(main(input("Email: "), input("Password: ")))
