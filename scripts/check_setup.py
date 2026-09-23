"""Verify .env credentials before trying to run anything else.

    python -m scripts.check_setup

Checks the Atlas connection and the Groq key independently, and translates the
common failures into the actual fix rather than a raw driver traceback.
"""

import asyncio
import re
import sys


def _mask(uri: str) -> str:
    """Show the host without leaking the password."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", uri)


def check_env() -> tuple[bool, object]:
    try:
        from backend.config import settings
    except Exception as exc:
        print(f"  FAIL  could not load .env: {exc}")
        print("        Copy .env.example to .env and fill it in.")
        return False, None

    ok = True

    if "USER:PASSWORD" in settings.mongodb_uri or not settings.mongodb_uri:
        print("  FAIL  MONGODB_URI is still the placeholder from .env.example")
        ok = False
    elif "<" in settings.mongodb_uri or ">" in settings.mongodb_uri:
        print("  FAIL  MONGODB_URI still contains <angle brackets>")
        print("        Replace <db_password> with the real password, brackets included.")
        ok = False
    else:
        print(f"  ok    MONGODB_URI  {_mask(settings.mongodb_uri)}")

    if not settings.groq_api_key or settings.groq_api_key.startswith("gsk_xxx"):
        print("  WARN  GROQ_API_KEY not set — everything works except /api/query")
    else:
        print(f"  ok    GROQ_API_KEY  {settings.groq_api_key[:7]}…")

    return ok, settings


async def check_mongo() -> bool:
    from motor.motor_asyncio import AsyncIOMotorClient

    from backend.config import settings

    client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=8000)
    try:
        await client.admin.command("ping")
        count = await client[settings.mongodb_db]["events"].count_documents({})
        print(f"  ok    connected — database '{settings.mongodb_db}', {count} event(s)")
        return True
    except Exception as exc:
        message = str(exc)
        print(f"  FAIL  {message[:180]}")

        lowered = message.lower()
        if "authentication failed" in lowered or "bad auth" in lowered:
            print("        Wrong username/password, or the password contains characters")
            print("        that need percent-encoding (@ : / ? # [ ] %).")
            print("        Fix under Atlas > Database Access.")
        elif "timed out" in lowered or "no servers" in lowered:
            print("        Your IP is probably not allowed.")
            print("        Atlas > Network Access > Add IP Address > Allow Access from Anywhere.")
        elif "nodename nor servname" in lowered or "name or service" in lowered:
            print("        Hostname didn't resolve — check the string was copied in full.")
        return False
    finally:
        client.close()


async def check_groq() -> bool:
    from groq import AsyncGroq

    from backend.config import settings

    if not settings.groq_api_key or settings.groq_api_key.startswith("gsk_xxx"):
        return True  # already reported as a warning

    client = AsyncGroq(api_key=settings.groq_api_key)
    try:
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=5,
        )
        print(f"  ok    {settings.groq_model} replied {response.choices[0].message.content!r}")
        return True
    except Exception as exc:
        message = str(exc)
        print(f"  FAIL  {message[:180]}")
        if "invalid_api_key" in message or "401" in message:
            print("        Get a fresh key at https://console.groq.com/keys")
        elif "model" in message.lower() and "not found" in message.lower():
            print(f"        Model '{settings.groq_model}' unavailable — check GROQ_MODEL in .env")
        return False


async def main() -> int:
    print("\nEnvironment")
    env_ok, _ = check_env()
    if not env_ok:
        print("\nFix .env, then run this again.\n")
        return 1

    print("\nMongoDB Atlas")
    mongo_ok = await check_mongo()

    print("\nGroq")
    groq_ok = await check_groq()

    if mongo_ok and groq_ok:
        print("\nAll good. Start the backend:  python -m backend.main\n")
        return 0

    print("\nFix the failures above, then run this again.\n")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
