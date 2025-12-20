from supabase import create_client
import os
import logging
from datetime import datetime, timezone
from typing import Optional, List

# configuration (prefer environment variables)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qmlmexokphzqrinbdfwk.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFtbG1leG9rcGh6cXJpbmJkZndrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjU2NDg5ODksImV4cCI6MjA4MTIyNDk4OX0.n4SY5_s-VSr9BHudSUhgJRc90wMcIJkP75UTJcX76Qo")

PERMANENT_TABLE = os.getenv("PERMANENT_TABLE", "permanent_allowed_user")
EXTRA_TABLE = os.getenv("EXTRA_TABLE", "other_extra_user")
ACTIVITY_TABLE = os.getenv("ACTIVITY_TABLE", "user_activity")

MAX_EXTRA_USERS = int(os.getenv("MAX_EXTRA_USERS", "500"))

logger = logging.getLogger("auth_utils")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(os.getenv("AUTH_UTILS_LOG_LEVEL", "INFO"))

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error("Failed to initialize Supabase client: %s", e)
    supabase = None

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_select(table: str, query: str, *args, count: Optional[str] = None):
    if not supabase:
        raise RuntimeError("Supabase client not initialized")
    try:
        q = supabase.table(table).select(query, count=count) if count else supabase.table(table).select(query)
        # apply filters passed in args as pairs: (method, key, value)
        # but keep simple usage in functions below
        return q
    except Exception as e:
        logger.error("Error building select for table %s: %s", table, e)
        raise

def get_vip_user_count() -> int:
    try:
        if not supabase:
            return 0
        res = supabase.table(PERMANENT_TABLE).select("email", count="exact").execute()
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return len(res.data) if getattr(res, "data", None) else 0
    except Exception as e:
        logger.error("get_vip_user_count error: %s", e)
        return 0

def get_active_extra_users_count() -> int:
    try:
        if not supabase:
            return 0
        now = _now_iso()
        res = supabase.table(EXTRA_TABLE).select("email", count="exact").gt("expires_at", now).execute()
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return len(res.data) if getattr(res, "data", None) else 0
    except Exception as e:
        logger.error("get_active_extra_users_count error: %s", e)
        return 0

def get_active_vip_sessions_count() -> int:
    try:
        if not supabase:
            return 0
        now = _now_iso()
        res = supabase.table(ACTIVITY_TABLE).select("email", count="exact").eq("role", "vip").eq("session_type", "login").gt("expires_at", now).execute()
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return len(res.data) if getattr(res, "data", None) else 0
    except Exception as e:
        logger.error("get_active_vip_sessions_count error: %s", e)
        return 0

def is_vip_user(email: str) -> bool:
    try:
        if not supabase:
            return False
        res = supabase.table(PERMANENT_TABLE).select("email").eq("email", email).execute()
        return bool(getattr(res, "data", None))
    except Exception as e:
        logger.error("is_vip_user error: %s", e)
        return False

def has_active_extra_session(email: str) -> bool:
    try:
        if not supabase:
            return False
        now = _now_iso()
        res = supabase.table(EXTRA_TABLE).select("email").eq("email", email).gt("expires_at", now).execute()
        return bool(getattr(res, "data", None))
    except Exception as e:
        logger.error("has_active_extra_session error: %s", e)
        return False

def has_active_vip_session(email: str) -> bool:
    try:
        if not supabase:
            return False
        now = _now_iso()
        res = supabase.table(ACTIVITY_TABLE).select("email").eq("email", email).eq("role", "vip").eq("session_type", "login").gt("expires_at", now).execute()
        return bool(getattr(res, "data", None))
    except Exception as e:
        logger.error("has_active_vip_session error: %s", e)
        return False

def create_extra_user_session(email: str, minutes: int = 2) -> bool:
    try:
        if not supabase:
            return False
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=minutes)
        supabase.table(EXTRA_TABLE).insert({
            "email": email,
            "login_time": now.isoformat(),
            "expires_at": expires_at.isoformat()
        }).execute()
        logger.info("Created extra session for %s", email)
        return True
    except Exception as e:
        logger.error("create_extra_user_session error: %s", e)
        return False

def create_vip_session(email: str, minutes: int = 2) -> bool:
    try:
        if not supabase:
            return False
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=minutes)
        supabase.table(ACTIVITY_TABLE).insert({
            "email": email,
            "role": "vip",
            "session_type": "login",
            "login_time": now.isoformat(),
            "expires_at": expires_at.isoformat()
        }).execute()
        logger.info("Created vip session for %s", email)
        return True
    except Exception as e:
        logger.error("create_vip_session error: %s", e)
        return False

def can_extra_user_login() -> bool:
    try:
        count = get_active_extra_users_count()
        return count < MAX_EXTRA_USERS
    except Exception as e:
        logger.error("can_extra_user_login error: %s", e)
        return False

def cleanup_expired_sessions() -> None:
    try:
        if not supabase:
            return
        now = _now_iso()
        try:
            supabase.table(EXTRA_TABLE).delete().lt("expires_at", now).execute()
        except Exception as ex:
            logger.debug("cleanup expired extra users delete failed: %s", ex)
        try:
            supabase.table(ACTIVITY_TABLE).delete().lt("expires_at", now).execute()
        except Exception as ex:
            logger.debug("cleanup expired vip sessions delete failed: %s", ex)
        logger.debug("Cleanup run at %s", now)
    except Exception as e:
        logger.error("cleanup_expired_sessions error: %s", e)

def kick_oldest_public_user() -> Optional[str]:
    try:
        if not supabase:
            return None
        res = supabase.table(EXTRA_TABLE).select("email").order("login_time", desc=False).limit(1).execute()
        if getattr(res, "data", None):
            oldest_email = res.data[0].get("email")
            supabase.table(EXTRA_TABLE).delete().eq("email", oldest_email).execute()
            logger.info("Kicked oldest extra user: %s", oldest_email)
            return oldest_email
        return None
    except Exception as e:
        logger.error("kick_oldest_public_user error: %s", e)
        return None

# Compatibility wrapper for older code that expects "allowed_users" behavior:
def is_email_allowed(email: str) -> bool:
    try:
        if is_vip_user(email):
            return True
        if has_active_extra_session(email):
            return True
        return False
    except Exception as e:
        logger.error("is_email_allowed error: %s", e)
        return False

# small imports used above
from datetime import timedelta
